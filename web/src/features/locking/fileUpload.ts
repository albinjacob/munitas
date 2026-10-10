/**
 * Sending one file to the platform in parts, so a poor connection costs one part and not the whole file.
 *
 * The file is opened as an upload (named, sized and given to a patient), sent a part at a time, and completed. A part that fails because the
 * connection did is sent again a few times with a growing wait, and when it still fails the upload is paused, not lost: asking again carries on from
 * the parts the platform already holds, even after the page was closed, because the upload's id is remembered in the browser for the file.
 * A refusal that sending again cannot cure (a wrong length, a patient who was erased) stops at once and says so.
 */

import { ApiError } from "../../api/client";
import {
  completeFileUpload,
  openFileUpload,
  fileUploadStatus,
  sendFilePart,
  type FinishedUpload,
  type UploadSession,
} from "../../api/files";

export interface Progress {
  state: "waiting" | "sending" | "paused" | "locked" | "refused";
  /** Parts the platform holds for this file. */
  held: number;
  parts: number;
  message?: string;
}

export interface UploadJob {
  datasetId: string;
  file: File;
  personKind: string;
  personValue: string;
  onProgress: (p: Progress) => void;
  /** How long to wait before sending a failed part again, in milliseconds, by attempt. Tests shorten it. */
  waits?: number[];
}

const DEFAULT_WAITS = [1000, 2000, 4000, 8000];

function slot(datasetId: string, file: File): string {
  return `munitas-file-upload:${datasetId}:${file.name}:${file.size}`;
}

function remembered(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // The browser may refuse storage (a private window). Resuming after the page was closed is lost, and everything else works.
  }
}

/** A failure that sending the same part again could cure: the network, or the platform being briefly unavailable. */
function worthRetrying(error: unknown): boolean {
  if (error instanceof ApiError) return error.status >= 500 || error.status === 408 || error.status === 429;
  return true;
}

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

async function session(job: UploadJob, key: string): Promise<UploadSession> {
  const known = remembered(key);
  if (known) {
    try {
      const existing = await fileUploadStatus(known);
      if (existing.status === "open" && existing.size_bytes === job.file.size) return existing;
    } catch {
      // Gone, or not this person's: open a new one.
    }
  }
  const opened = await openFileUpload(job.datasetId, {
    file_name: job.file.name,
    size_bytes: job.file.size,
    person_kind: job.personKind,
    person_value: job.personValue,
  });
  remember(key, opened.id);
  return opened;
}

/** Sends the file and returns what the platform kept. Throws a PausedUpload when only the connection is to blame. */
export async function uploadFile(job: UploadJob): Promise<FinishedUpload> {
  const waits = job.waits ?? DEFAULT_WAITS;
  const key = slot(job.datasetId, job.file);
  let up: UploadSession;
  try {
    up = await session(job, key);
  } catch (error) {
    if (worthRetrying(error)) throw new PausedUpload(error instanceof Error ? error.message : "The connection failed.");
    throw error;
  }
  let held = up.received.length;
  job.onProgress({ state: "sending", held, parts: up.parts });

  for (const n of up.missing) {
    const bytes = job.file.slice((n - 1) * up.part_bytes, Math.min(job.file.size, n * up.part_bytes));
    for (let attempt = 0; ; attempt += 1) {
      try {
        await sendFilePart(up.id, n, bytes);
        break;
      } catch (error) {
        if (!worthRetrying(error)) throw error;
        if (attempt >= waits.length) throw new PausedUpload(error instanceof Error ? error.message : "The connection failed.");
        await pause(waits[attempt]);
      }
    }
    held += 1;
    job.onProgress({ state: "sending", held, parts: up.parts });
  }

  for (let attempt = 0; ; attempt += 1) {
    try {
      const done = await completeFileUpload(up.id);
      remember(key, null);
      return done;
    } catch (error) {
      // A completion that reached the platform but whose answer was lost: ask, and take what it kept.
      try {
        const now = await fileUploadStatus(up.id);
        if (now.status === "complete") {
          remember(key, null);
          return { id: up.id, source_id: "", file_name: up.file_name, stored_bytes: 0, facts: {} };
        }
      } catch {
        // Fall through to the usual retry or pause.
      }
      if (!worthRetrying(error)) throw error;
      if (attempt >= waits.length) throw new PausedUpload(error instanceof Error ? error.message : "The connection failed.");
      await pause(waits[attempt]);
    }
  }
}

export class PausedUpload extends Error {}

// The manifest: which patient each file is about ---------------------------------------------------------------------------------------

/** Splits one line of a CSV into its fields, honouring double quotes. */
function fields(line: string): string[] {
  const out: string[] = [];
  let current = "";
  let quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        current += '"';
        i += 1;
      } else if (c === '"') quoted = false;
      else current += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      out.push(current);
      current = "";
    } else current += c;
  }
  out.push(current);
  return out.map((f) => f.trim());
}

export const AUDIO = ".wav";
export const ANSWER_KEY = ".truth.json";

export interface Plan {
  /** The person identifier for each file to send, by file name. An answer key takes the person of the file it is named after. */
  people: Map<string, string>;
  problems: string[];
}

/**
 * Checks the manifest against the files chosen, before anything is sent, and returns every problem together: a manifest that is wrong should take
 * one look to understand and not one attempt per mistake.
 */
export function plan(manifestText: string, files: { name: string; size: number }[]): Plan {
  const problems: string[] = [];
  const lines = manifestText.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim() !== "");
  const header = lines.length ? fields(lines[0]).map((h) => h.toLowerCase()) : [];
  const fileAt = header.indexOf("file");
  const personAt = header.indexOf("person");
  const rows = new Map<string, string>();
  if (!lines.length) problems.push("The manifest is empty. It needs a header row with the columns file and person, then one row for each file.");
  else if (fileAt < 0 || personAt < 0) problems.push("The manifest's first row must name two columns: file and person.");
  else {
    lines.slice(1).forEach((line, i) => {
      const cells = fields(line);
      const name = cells[fileAt] ?? "";
      const person = cells[personAt] ?? "";
      if (!name) problems.push(`Manifest row ${i + 2} names no file.`);
      else if (!person) problems.push(`${name} names no person in the manifest, and a file is locked to one person.`);
      else if (rows.has(name)) problems.push(`${name} appears more than once in the manifest.`);
      else rows.set(name, person);
    });
  }

  const people = new Map<string, string>();
  const chosen = new Set(files.map((f) => f.name));
  for (const f of files) {
    if (f.name.endsWith(AUDIO)) {
      const person = rows.get(f.name);
      if (person) people.set(f.name, person);
      else if (lines.length) problems.push(`${f.name} is not in the manifest, so nobody is named as the person it is about.`);
    } else if (f.name.endsWith(ANSWER_KEY)) {
      const file = f.name.slice(0, -ANSWER_KEY.length) + AUDIO;
      const person = rows.get(file);
      if (person && chosen.has(file)) people.set(f.name, person);
      else problems.push(`${f.name} is an answer key, and its file ${file} is not among the files chosen or not in the manifest.`);
    } else problems.push(`${f.name} is neither a file (${AUDIO}) nor an answer key (${ANSWER_KEY}).`);
  }
  for (const name of rows.keys()) {
    if (!chosen.has(name)) problems.push(`${name} is in the manifest, and the file was not chosen.`);
  }
  return { people, problems };
}
