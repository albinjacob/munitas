/**
 * Bringing in recordings that are each one patient's, locked under that patient's key as they upload.
 *
 * A manifest names the patient for every recording, and everything wrong with it or with the files chosen is listed before anything is sent. Each
 * recording then goes up in parts: a part that fails because the connection did is sent again, and an upload that cannot go on is paused with the
 * parts already held kept, so asking again carries on from where it stopped. When every recording is locked, one button closes the upload.
 */

import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "../../api/client";
import { useSealRecordings } from "../../api/recordings";
import { Reasons } from "./LockedColumns";
import { PausedUpload, plan, uploadRecording, type Progress } from "./recordingUpload";

const STATE_WORDS: Record<Progress["state"], string> = {
  waiting: "Waiting",
  sending: "Sending",
  paused: "Paused",
  locked: "Locked",
  refused: "Refused",
};

function size(n: number): string {
  if (n < 1024 * 1024) return `${Math.max(1, Math.round(n / 1024))} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function RecordingsPanel({ datasetId }: { datasetId: string }) {
  const seal = useSealRecordings(datasetId);
  const [kind, setKind] = useState("");
  const [manifest, setManifest] = useState<File | null>(null);
  const [manifestText, setManifestText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [progress, setProgress] = useState<Record<string, Progress>>({});
  const [running, setRunning] = useState(false);
  const stopped = useRef(false);

  const checked = manifest ? plan(manifestText, files) : null;
  const problems = [...(checked?.problems ?? []), ...(files.length && manifest && !kind.trim() ? ["Say what kind of identifier names a patient."] : [])];
  const allLocked = files.length > 0 && files.every((f) => progress[f.name]?.state === "locked");
  const refused = files.filter((f) => progress[f.name]?.state === "refused");
  const paused = files.some((f) => progress[f.name]?.state === "paused");
  const started = Object.keys(progress).length > 0;

  async function run() {
    if (!checked) return;
    setRunning(true);
    stopped.current = false;
    for (const file of files) {
      if (progress[file.name]?.state === "locked") continue;
      const patient = checked.patients.get(file.name) ?? "";
      // The last thing said about this file, kept here and not read back from state, which is as it was when this run began.
      let last: Progress = { state: "waiting", held: 0, parts: 1 };
      const set = (p: Progress) => {
        last = p;
        setProgress((prev) => ({ ...prev, [file.name]: p }));
      };
      try {
        await uploadRecording({
          datasetId,
          file,
          patientKind: kind.trim(),
          patientValue: patient,
          onProgress: set,
        });
        set({ state: "locked", held: 1, parts: 1 });
      } catch (error) {
        if (error instanceof PausedUpload) {
          set({ state: "paused", held: last.held, parts: last.parts, message: error.message });
          break;
        }
        const reasons = error instanceof ApiError && error.reasons.length ? error.reasons.join(" ") : error instanceof Error ? error.message : "Refused.";
        set({ state: "refused", held: 0, parts: 1, message: reasons });
      }
    }
    setRunning(false);
  }

  if (seal.data) {
    return (
      <div data-testid="recordings-sealed" className="space-y-2 rounded border border-teal-300 bg-teal-50 p-4 text-sm text-teal-900">
        <p className="font-medium">
          Sealed as version {seal.data.version}: {seal.data.records} recording{seal.data.records === 1 ? "" : "s"} about {seal.data.people}{" "}
          {seal.data.people === 1 ? "patient" : "patients"}.
        </p>
        <p>Each recording is locked under its patient's key. Nothing about this version can change from here; later recordings become a new version.</p>
        <Link to={`/versions/${seal.data.id}`} data-testid="open-sealed-recordings" className="inline-block text-sky-700 underline">
          Open this version
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-3" data-testid="recordings-panel">
      <p className="text-sm text-slate-700">
        Each recording is locked under its patient's key as it uploads. Say which patient each recording is about in a manifest: a CSV file (a plain text table) with two columns, <strong>file</strong>{" "}
        and <strong>patient</strong>, and one row for each recording. If you also have an answer key, the correct written text of a recording, which is used later to check how well names were
        removed from it, name it after its recording (<code>visit-1.truth.json</code> beside <code>visit-1.wav</code>). It belongs to the same patient and needs no row of its own.
        A recording is about one patient: if two people speak in it, split it first.
      </p>
      <div className="flex flex-wrap gap-6">
        <label className="text-sm">
          <span className="block text-slate-600">What kind of identifier names a patient</span>
          <input
            data-testid="recordings-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            disabled={running || started}
            placeholder="hospital chart number"
            className="mt-1 w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="text-sm">
          <span className="block text-slate-600">The manifest</span>
          <input
            data-testid="recordings-manifest"
            type="file"
            accept=".csv,text/csv"
            disabled={running || started}
            onChange={async (e) => {
              const file = e.target.files?.[0] ?? null;
              setManifest(file);
              setManifestText(file ? await file.text() : "");
            }}
            className="mt-1 block text-xs"
          />
        </label>
        <label className="text-sm">
          <span className="block text-slate-600">The recordings</span>
          <input
            data-testid="recordings-files"
            type="file"
            multiple
            accept=".wav,.json"
            disabled={running || started}
            onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            className="mt-1 block text-xs"
          />
        </label>
      </div>

      {problems.length > 0 && (
        <div data-testid="recordings-problems" role="alert" className="rounded border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900">
          <p className="font-medium">Fix these before uploading:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      {!started && (
        <button
          type="button"
          data-testid="recordings-start"
          disabled={!manifest || files.length === 0 || problems.length > 0 || running}
          onClick={run}
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          Upload and lock
        </button>
      )}

      {started && (
        <ul data-testid="recordings-progress" className="space-y-2 text-sm">
          {files.map((f) => {
            const p = progress[f.name] ?? { state: "waiting", held: 0, parts: 1 };
            const percent = p.state === "locked" ? 100 : Math.round((p.held / Math.max(1, p.parts)) * 100);
            return (
              <li key={f.name} data-testid={`recording-${f.name}`} className="rounded border border-slate-200 p-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{f.name}</span>
                  <span className="text-xs text-slate-500">
                    {size(f.size)} · {STATE_WORDS[p.state]}
                    {p.state === "sending" || p.state === "paused" ? `, ${p.held} of ${p.parts} parts held` : ""}
                  </span>
                </div>
                <div className="mt-1 h-1.5 rounded bg-slate-100" aria-hidden="true">
                  <div className={`h-1.5 rounded ${p.state === "refused" ? "bg-red-500" : "bg-teal-500"}`} style={{ width: `${percent}%` }} />
                </div>
                {p.state === "paused" && (
                  <p className="mt-1 text-xs text-slate-600">
                    The connection failed, so this is paused. The parts already sent are kept, and carrying on sends only the rest.
                  </p>
                )}
                {p.state === "refused" && p.message && (
                  <p role="alert" className="mt-1 text-xs text-red-800">
                    {p.message}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {paused && !running && (
        <button
          type="button"
          data-testid="recordings-carry-on"
          onClick={run}
          className="rounded border border-slate-700 px-3 py-1.5 text-sm font-medium text-slate-900"
        >
          Carry on from where it stopped
        </button>
      )}

      {allLocked && (
        <div className="space-y-2">
          <button
            type="button"
            data-testid="recordings-seal"
            disabled={seal.isPending}
            onClick={() => seal.mutate()}
            className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {seal.isPending ? "Sealing" : "Seal these recordings"}
          </button>
          <span className="ml-3 text-xs text-slate-500">Closes the upload. Nothing can be added to this version afterwards.</span>
        </div>
      )}
      {refused.length > 0 && !running && (
        <p className="text-xs text-red-800">
          {refused.length} recording{refused.length === 1 ? " was" : "s were"} refused, so the upload cannot be sealed yet. Reload this page and upload{" "}
          {refused.length === 1 ? "it" : "them"} again once fixed.
        </p>
      )}
      {seal.error && (
        <div data-testid="recordings-seal-refused" role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
          <p className="font-medium">Nothing was sealed. Fix these first:</p>
          <Reasons error={seal.error} fallback="Sealing failed." />
        </div>
      )}
    </div>
  );
}
