/**
 * Sending a table to the platform in parts, so a poor connection costs one part and not the whole file.
 *
 * The file is opened as an upload (named, sized and given its contract), sent a part at a time, and finished. A part that fails because the
 * connection did is sent again a few times with a growing wait. When the platform turns a part away because it is busy, the wait is the one it
 * asks for. When it still fails the upload is paused, not lost: asking again for the same file carries on from the parts the platform already
 * holds, even after the page was closed, because the upload's id is remembered in the browser for the file. A refusal that sending again cannot
 * cure (the wrong size, a limit, a contract that is wrong) stops at once and says so.
 */

import { ApiError } from "../../api/client";
import {
  cancelTableUpload,
  finishTableUpload,
  openTableUpload,
  sendTablePart,
  tableUploadStatus,
  type TableUpload,
  type TableUploadOptions,
} from "../../api/tableUploads";

export interface Progress {
  state: "sending" | "finishing";
  /** Parts the platform holds for this file. */
  held: number;
  parts: number;
}

export interface TableJob {
  datasetId: string;
  file: File;
  contractText: string;
  options: TableUploadOptions;
  onProgress: (p: Progress) => void;
  onOpened?: (upload: TableUpload) => void;
  /** Asked between parts: true when the person has cancelled, so no more is sent. */
  stopped?: () => boolean;
  /** How long to wait before sending a failed part again, in milliseconds, by attempt. Tests shorten it. */
  waits?: number[];
}

export class PausedTableUpload extends Error {}

const DEFAULT_WAITS = [1000, 2000, 4000, 8000];

export const slot = (datasetId: string, file: { name: string; size: number }) => `munitas-table-upload:${datasetId}:${file.name}:${file.size}`;

export function remembered(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function remember(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // The browser may refuse storage (a private window). Carrying on after the page was closed is lost, and everything else works.
  }
}

/** A failure that sending the same part again could cure: the network, or the platform being briefly unavailable or busy. */
function worthRetrying(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500 || error.status === 408 || error.status === 429;
  return true;
}

/** How long the platform asked to wait, in milliseconds, when it turned a request away as busy. */
function askedToWait(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.status !== 429) return null;
  const detail = (error.body as { detail?: { retry_after_seconds?: number } } | null)?.detail;
  return typeof detail?.retry_after_seconds === "number" ? Math.min(30, detail.retry_after_seconds) * 1000 : null;
}

/** What a person reads when sending stops: the platform's own words when it said something, and otherwise that the connection was lost, never the browser's "Failed to fetch". */
const why = (error: unknown) => (error instanceof ApiError ? error.message : "The connection to the platform was lost.");

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function session(job: TableJob, key: string): Promise<TableUpload> {
  const known = remembered(key);
  if (known) {
    try {
      const existing = await tableUploadStatus(known);
      if (existing.open && existing.size_bytes === job.file.size) return existing;
    } catch {
      // Gone, or not this person's: open a new one.
    }
  }
  const opened = await openTableUpload(job.datasetId, {
    file_name: job.file.name,
    size_bytes: job.file.size,
    contract: job.contractText,
    options: job.options,
  });
  remember(key, opened.job_id);
  return opened;
}

/** Sends the file and finishes the upload. Returns the upload as the platform now has it. Throws PausedTableUpload when only the connection is to blame. */
export async function uploadTable(job: TableJob): Promise<TableUpload> {
  const waits = job.waits ?? DEFAULT_WAITS;
  const key = slot(job.datasetId, job.file);
  let up: TableUpload;
  try {
    up = await session(job, key);
  } catch (error) {
    // A limit is not a fault: it is said as it is, with how long to wait, and the upload is not started.
    if (error instanceof ApiError && error.status === 429) throw error;
    if (worthRetrying(error)) throw new PausedTableUpload(why(error));
    throw error;
  }
  job.onOpened?.(up);
  if (job.stopped?.()) {
    // Cancelled while the upload was being opened: the platform has it now, so it is told, and nothing is left holding a version number.
    try {
      await cancelTableUpload(up.job_id);
    } catch {
      // It may already have ended.
    }
    remember(key, null);
    throw new PausedTableUpload("Cancelled.");
  }
  let held = up.received.length;
  job.onProgress({ state: "sending", held, parts: up.parts });

  for (const n of up.missing) {
    if (job.stopped?.()) throw new PausedTableUpload("Cancelled.");
    const bytes = job.file.slice((n - 1) * up.part_bytes, Math.min(job.file.size, n * up.part_bytes));
    let failures = 0;
    let turnedAway = 0;
    for (;;) {
      try {
        await sendTablePart(up.job_id, n, bytes);
        break;
      } catch (error) {
        if (!worthRetrying(error)) throw error;
        const asked = askedToWait(error);
        if (asked !== null) {
          // The platform is busy and said how long to wait, which is not a fault: wait that long, up to a dozen times.
          turnedAway += 1;
          if (turnedAway > 12) throw new PausedTableUpload(why(error));
          await pause(asked);
          continue;
        }
        if (failures >= waits.length) throw new PausedTableUpload(why(error));
        await pause(waits[failures]);
        failures += 1;
      }
    }
    held += 1;
    job.onProgress({ state: "sending", held, parts: up.parts });
  }

  job.onProgress({ state: "finishing", held, parts: up.parts });
  for (let attempt = 0; ; attempt += 1) {
    try {
      const done = await finishTableUpload(up.job_id);
      remember(key, null);
      return done;
    } catch (error) {
      if (!worthRetrying(error)) throw error;
      if (attempt >= waits.length) throw new PausedTableUpload(why(error));
      await pause(askedToWait(error) ?? waits[attempt]);
    }
  }
}

/** The text of a list of problems as a file a person can keep: one problem to a line. */
export function problemsFile(fileName: string, problems: string[]): Blob {
  return new Blob([`Problems found in ${fileName}\n\n${problems.join("\n")}\n`], { type: "text/plain" });
}
