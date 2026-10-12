/**
 * A table too large for one request: sent to the platform in parts, then checked and written by a worker, which the person watches from here.
 *
 * What the person sees, in order: the file going up (parts sent of parts in all, with a bar), the table waiting for a worker and being written (the
 * rows counted so far), and how it ended. A table that is wrong is refused with every problem found, by row, line and column, which can be read here
 * and kept as a file. At any point before it ends the person can cancel, and the platform throws away what was sent. A break in the connection pauses
 * the upload and keeps what arrived; carrying on, even after the page was closed, sends only the parts that are missing.
 */

import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { cancelTableUpload, useTableUploadStatus, type TableUploadOptions } from "../../api/tableUploads";
import { sentence } from "../../lib/text";
import { byOn } from "../../lib/when";
import { PausedTableUpload, problemsFile, remember, slot, uploadTable } from "./tableUploadEngine";

const MB = 1024 * 1024;
const size = (bytes: number) => (bytes >= 1024 * MB ? `${(bytes / (1024 * MB)).toFixed(1)} GB` : `${Math.round(bytes / MB)} MB`);

type Stage =
  | { kind: "sending"; held: number; parts: number }
  | { kind: "finishing" }
  | { kind: "paused"; message: string }
  | { kind: "stopped"; reasons: string[] }
  | { kind: "turned-away"; reasons: string[]; seconds: number | null }
  | { kind: "waiting" }
  | { kind: "cancelled" };

export function LargeTableUpload({ datasetId, contract, rows, options }: { datasetId: string; contract: File; rows: File; options: TableUploadOptions }) {
  const [stage, setStage] = useState<Stage>({ kind: "sending", held: 0, parts: 0 });
  const [jobId, setJobId] = useState<string | null>(null);
  const [finished, setFinished] = useState<string | null>(null);
  const cancelled = useRef(false);
  const started = useRef(false);
  const status = useTableUploadStatus(finished);

  async function run() {
    cancelled.current = false;
    setStage({ kind: "sending", held: 0, parts: 0 });
    try {
      const contractText = await contract.text();
      const done = await uploadTable({
        datasetId,
        file: rows,
        contractText,
        options,
        stopped: () => cancelled.current,
        onOpened: (up) => setJobId(up.job_id),
        onProgress: (p) => setStage(p.state === "finishing" ? { kind: "finishing" } : { kind: "sending", held: p.held, parts: p.parts }),
      });
      if (!cancelled.current) {
        setFinished(done.job_id);
        setStage({ kind: "waiting" });
      }
    } catch (error) {
      if (cancelled.current) return;
      if (error instanceof PausedTableUpload) setStage({ kind: "paused", message: error.message });
      else if (error instanceof ApiError && error.status === 429) {
        const seconds = (error.body as { detail?: { retry_after_seconds?: number } } | null)?.detail?.retry_after_seconds;
        setStage({ kind: "turned-away", reasons: error.reasons, seconds: typeof seconds === "number" ? seconds : null });
      } else setStage({ kind: "stopped", reasons: error instanceof ApiError && error.reasons.length ? error.reasons : [error instanceof Error ? error.message : "Sending failed."] });
    }
  }

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function cancel() {
    cancelled.current = true;
    const id = finished ?? jobId;
    if (id) {
      try {
        await cancelTableUpload(id);
      } catch {
        // It may already have ended; the status below says how.
      }
    }
    remember(slot(datasetId, rows), null);
    setStage({ kind: "cancelled" });
  }

  const ended = status.data && ["sealed", "refused", "expired"].includes(status.data.status);
  const sent = stage.kind === "sending" ? stage : null;

  if (stage.kind === "cancelled" || (ended && status.data?.outcome === "cancelled")) {
    return (
      <div data-testid="upload-cancelled" className="space-y-1 rounded border border-slate-300 bg-slate-50 p-4 text-sm text-slate-800">
        <p className="font-medium">This was cancelled. Nothing was kept.</p>
        <p>What had been sent was thrown away and no version was made.</p>
      </div>
    );
  }

  if (ended && status.data?.status === "sealed") {
    const done = status.data;
    return (
      <div data-testid="table-sealed" className="space-y-2 rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900">
        <p className="font-medium">
          Sealed as version {done.version}: {(done.progress?.rows ?? 0).toLocaleString()} rows
          {done.about_people && done.people !== null ? ` about ${done.people.toLocaleString()} ${done.people === 1 ? "person" : "people"}` : ""}.
        </p>
        <p data-testid="brought-in">
          {rows.name} ({size(rows.size)}) was brought in {byOn(done.brought_in_by_label, done.finished_at)}.
          {done.about_people ? " The file itself was not kept, because it held the identifying values as they were written." : ""}
        </p>
        {done.about_people ? (
          <>
            <p>
              Their identifying values are locked, each under a key made for that person. Nothing about this version can change from here; a later table becomes a
              new version.
            </p>
            {!!done.people_in_small_groups && (
              <p data-testid="sealed-small-groups">
                {done.people_in_small_groups.toLocaleString()} {done.people_in_small_groups === 1 ? "person sits" : "people sit"} in a group smaller than{" "}
                {done.group_threshold}. It did not stop the seal. The version's page says what locking each column would leave.
              </p>
            )}
          </>
        ) : (
          <p>
            Nothing in it is locked. It starts at the most restricted level, so nobody can read it until the person who answers for it allows it. Nothing about this
            version can change from here; a later table becomes a new version.
          </p>
        )}
        {done.version_id && (
          <Link to={`/versions/${done.version_id}`} data-testid="open-sealed-table" className="inline-block text-sky-700 underline">
            Open this version
          </Link>
        )}
      </div>
    );
  }

  if (ended && status.data) {
    const done = status.data;
    const problems = (done.problems.length ? done.problems : [done.reason ?? "The table could not be written."]).map(sentence);
    const url = URL.createObjectURL(problemsFile(rows.name, problems));
    return (
      <div data-testid="table-refused" role="alert" className="space-y-2 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        <p className="font-medium">Nothing was sealed. {done.status === "expired" ? "It ran out of time." : "Fix these first:"}</p>
        <ul data-testid="table-problems" className="list-disc space-y-0.5 pl-5">
          {problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
        <a data-testid="download-problems" href={url} download={`${rows.name}.problems.txt`} className="inline-block text-sky-700 underline">
          Keep this list as a file
        </a>
      </div>
    );
  }

  if (stage.kind === "stopped") {
    return (
      <div data-testid="table-refused" role="alert" className="space-y-1 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800">
        <p className="font-medium">Nothing was sent. Fix these first:</p>
        <ul className="list-disc space-y-0.5 pl-5">
          {stage.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
    );
  }

  if (stage.kind === "turned-away") {
    const minutes = stage.seconds ? Math.max(1, Math.round(stage.seconds / 60)) : null;
    return (
      <div data-testid="upload-turned-away" className="space-y-2 rounded border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
        <p className="font-medium">Not started yet.</p>
        <ul className="list-disc space-y-0.5 pl-5">
          {stage.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <p>
          {minutes ? `Try again in about ${minutes} ${minutes === 1 ? "minute" : "minutes"}.` : "Try again in a while."} Nothing was sent.
        </p>
        <button type="button" data-testid="upload-retry" onClick={() => void run()} className="btn btn-primary">
          Try again
        </button>
      </div>
    );
  }

  if (stage.kind === "paused") {
    return (
      <div data-testid="upload-paused" className="space-y-2 rounded border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900">
        <p className="font-medium">The upload is paused.</p>
        <p>{sentence(stage.message)} What has arrived is kept, and carrying on sends only what is missing.</p>
        <div className="flex gap-3">
          <button type="button" data-testid="upload-resume" onClick={() => void run()} className="btn btn-primary">
            Carry on
          </button>
          <button type="button" data-testid="upload-cancel" onClick={() => void cancel()} className="btn btn-secondary">
            Cancel
          </button>
        </div>
      </div>
    );
  }

  const phase = (() => {
    if (sent && sent.parts === 0) return "Starting the upload.";
    if (sent) return `Sending ${rows.name}: part ${Math.min(sent.held + 1, sent.parts)} of ${sent.parts}.`;
    if (stage.kind === "finishing") return "All of it has arrived. Finishing.";
    const s = status.data;
    if (!s) return "Handing it to a worker.";
    if (s.status === "running" && s.progress?.phase === "checking") return `Checking every row: ${(s.progress?.rows ?? 0).toLocaleString()} rows so far.`;
    if (s.status === "running") return `${s.about_people ? "Locking and writing the table" : "Writing the table"}: ${(s.progress?.rows ?? 0).toLocaleString()} rows so far.`;
    return s.stalled ? "Waiting for a worker, which is taking longer than usual." : "Waiting for a worker to take it.";
  })();
  const percent = sent && sent.parts ? Math.round((sent.held / sent.parts) * 100) : stage.kind === "sending" ? 0 : 100;

  return (
    <div data-testid="large-upload" className="space-y-2 rounded border border-slate-300 bg-white p-4 text-sm text-slate-800">
      <p data-testid="upload-phase" className="font-medium">
        {phase}
      </p>
      <div className="h-2 w-full overflow-hidden rounded bg-slate-200" aria-hidden>
        <div data-testid="upload-bar" className="h-2 bg-indigo-500 transition-all" style={{ width: `${percent}%` }} />
      </div>
      {sent && sent.parts > 0 && (
        <p data-testid="upload-progress" className="text-xs text-slate-600">
          {sent.held} of {sent.parts} parts sent ({size(Math.min(rows.size, sent.held * 8 * MB))} of {size(rows.size)}).
        </p>
      )}
      <button type="button" data-testid="upload-cancel" onClick={() => void cancel()} className="btn btn-secondary">
        Cancel
      </button>
    </div>
  );
}
