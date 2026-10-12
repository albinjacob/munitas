/**
 * The files of a version, on the page of the version: what is there, whether you may open it, and playing or reading the ones you may.
 *
 * Opening goes through the platform. It unlocks a locked file with the key of the person it is about and does not hand you the key or where the file is stored. Every
 * opening is written down. A sound plays in the page and a text file is shown in part: there is no download here. Nothing about a patient is shown:
 * a file is named by its identifier, its size and whether it is locked.
 */

import { useState } from "react";
import { ApiError } from "../../api/client";
import { streamUrl, useOpenRecord, useRecordList, type OpenedRecord, type RecordFile } from "../../api/recordFiles";
import { Failure, Section } from "../../components/states";
import { Reasons } from "./LockedColumns";
import { when } from "../../lib/when";

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const PREVIEW_BYTES = 8 * 1024;
/** How much of a file that is neither sound nor text is shown, as bytes. */
const OTHER_PREVIEW_BYTES = 256;

function hexDump(bytes: Uint8Array): string {
  const rows: string[] = [];
  for (let at = 0; at < bytes.length; at += 16) {
    const row = Array.from(bytes.slice(at, at + 16));
    const hex = row.map((b) => b.toString(16).padStart(2, "0")).join(" ");
    const chars = row.map((b) => (b >= 32 && b < 127 ? String.fromCharCode(b) : ".")).join("");
    rows.push(`${at.toString(16).padStart(4, "0")}  ${hex.padEnd(47, " ")}  ${chars}`);
  }
  return rows.join("\n");
}

/** The platform's reasons are phrases; on the page each reads as a sentence. */
function sentence(reason: string): string {
  const text = reason.trim();
  return text.charAt(0).toUpperCase() + text.slice(1) + (/[.!?]$/.test(text) ? "" : ".");
}

function Row({ versionId, file, mayOpen }: { versionId: string; file: RecordFile; mayOpen: boolean }) {
  const open = useOpenRecord(versionId);
  const [opened, setOpened] = useState<OpenedRecord | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const isSound = file.content_type.startsWith("audio/");
  const isText = file.content_type.startsWith("text/") || file.content_type === "application/json";
  const isOther = !isSound && !isText;

  async function start() {
    setFailed(null);
    setText(null);
    try {
      const got = await open.mutateAsync(file.record_id);
      setOpened(got);
      if (!isSound) {
        const size = isText ? PREVIEW_BYTES : OTHER_PREVIEW_BYTES;
        const res = await fetch(streamUrl(got.handle), { headers: { Range: `bytes=0-${size - 1}` } });
        if (!res.ok && res.status !== 206) throw new Error(`The platform answered ${res.status}.`);
        setText(isText ? await res.text() : hexDump(new Uint8Array(await res.arrayBuffer())));
      }
    } catch (e) {
      setOpened(null);
      setFailed(e instanceof ApiError && e.reasons.length ? e.reasons.join(" ") : e instanceof Error ? e.message : "That was not accepted.");
    }
  }

  return (
    <li data-testid={`record-row-${file.record_id}`} className="space-y-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{file.record_id}</span>
        <span className="text-xs text-slate-500">{size(file.bytes)}</span>
        {file.locked && <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700">Locked</span>}
        {file.erased && <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-900">Person erased</span>}
        {mayOpen && !file.erased && (
          <button
            type="button"
            data-testid={`record-play-${file.record_id}`}
            onClick={start}
            disabled={open.isPending}
            className="btn btn-primary btn-sm ml-auto"
          >
            {isSound ? "Play" : isOther ? "Preview" : "Read"}
          </button>
        )}
      </div>
      {opened && isSound && (
        <audio data-testid={`record-player-${file.record_id}`} controls autoPlay controlsList="nodownload" src={streamUrl(opened.handle)} className="w-full" />
      )}
      {text !== null && isOther && (
        <p className="text-xs text-slate-600">
          The first 256 bytes of the file. On the left are the bytes as numbers, written in hexadecimal; on the right the same bytes as characters, with a dot
          where a byte is not a printable character.
        </p>
      )}
      {text !== null && (
        <pre data-testid={`record-text-${file.record_id}`} className={`${isOther ? "max-h-72" : "max-h-48"} overflow-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap`}>
          {text}
          {isOther
            ? file.bytes > OTHER_PREVIEW_BYTES
              ? "\n\nOnly the first 256 bytes are shown, as numbers and, beside them, as characters where they can be printed."
              : ""
            : file.bytes > PREVIEW_BYTES
              ? "\n\nOnly the first part is shown."
              : ""}
        </pre>
      )}
      {failed && (
        <div role="alert" className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-900">
          {failed}
        </div>
      )}
      {open.error && !failed && (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={open.error} fallback="That was not accepted." />
        </div>
      )}
    </li>
  );
}

export function RecordFilesSection({ versionId, title, description }: { versionId: string; title?: string; description?: string }) {
  const list = useRecordList(versionId);
  if (list.isLoading) return null;
  if (list.error) return list.error instanceof ApiError && list.error.status === 404 ? null : <Failure error={list.error} what="the files of this version" />;
  const data = list.data;
  const ended = data?.basis === "expired";
  if (!data || (data.total === 0 && !ended)) return null;
  return (
    <Section
      title={title ?? "Files"}
      description={description ?? "The files this version holds. You open one through the platform, which unlocks it with the key of the person it is about. You are never given the key or where the file is stored, and every opening is written down."}
    >
      <div data-testid="record-files" className="space-y-3">
        {ended && (
          <p data-testid="record-files-ended" className="rounded border border-slate-200 bg-slate-50 p-3 text-sm text-slate-800">
            This version was made for development and has ended{data.development?.ends_at ? ` (${when(data.development.ends_at)})` : ""}. Its files are
            deleted. The version itself stays on record.
          </p>
        )}
        {!data.may_open && !ended && (
          <p data-testid="record-files-note" className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-slate-800">
            You cannot open these files. {data.reasons.map(sentence).join(" ")}
          </p>
        )}
        {data.may_open && (
          <p className="text-xs text-slate-600">
            {data.basis === "own_sample" && "These are in a sample drawn for you, so you may open them while the sample is in force."}
            {data.basis === "synthetic" && "These files were declared synthetic: no real person is in them."}
            {data.basis === "own_development_output" && "A run you started made these from development data, so you may open them while that data is in force for you."}
            {data.basis === "review" && data.review && `You have opened ${data.review.opened.length} of the ${data.review.limit} files you may open to judge this result. What you open is kept with your decision.`}
          </p>
        )}
        {data.may_open && data.development?.ends_at && !ended && (
          <p data-testid="record-files-ends" className="text-xs text-slate-600">These files are deleted on {when(data.development.ends_at)}, or sooner if the sample they came from ends.</p>
        )}
        <ul className="space-y-2">
          {data.records.map((f) => (
            <Row key={f.record_id} versionId={versionId} file={f} mayOpen={data.may_open} />
          ))}
        </ul>
        {data.total > data.records.length && <p className="text-xs text-slate-500">Showing {data.records.length} of {data.total}.</p>}
      </div>
    </Section>
  );
}
