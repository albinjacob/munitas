/**
 * Development samples, on the page of a version of locked files.
 *
 * A pipeline version in development runs only on development data. For files that means a sample the platform draws for the person who asks, after an
 * approver of the department that owns them agrees, or files declared synthetic. A sample names the same locked files and copies none of them.
 * Nothing here shows a file or says who a patient is: counts by length, the people who asked and decided, and dates.
 */

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  useAskForSample,
  useDeclareSynthetic,
  useDecideSample,
  useDevelopmentSamples,
  useEndSample,
  type DevelopmentSample,
  type DevelopmentSamples,
  type Strata,
} from "../../api/developmentSamples";
import { Failure, Section } from "../../components/states";
import { Reasons } from "./LockedColumns";
import { when } from "../../lib/when";


const STATUS_WORDS: Record<DevelopmentSample["status"], string> = {
  requested: "Waiting for an approver",
  approved: "In force",
  declined: "Declined",
  ended: "Ended",
};

function StatusChip({ sample }: { sample: DevelopmentSample }) {
  const over = sample.status === "approved" && sample.expires_at !== null && new Date(sample.expires_at) < new Date();
  const word = over ? "Ran out" : STATUS_WORDS[sample.status];
  const tone =
    sample.status === "approved" && !over ? "bg-teal-100 text-teal-900" : sample.status === "requested" ? "bg-amber-100 text-amber-900" : "bg-slate-200 text-slate-800";
  return <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${tone}`}>{word}</span>;
}

/** "7 short, 7 medium and 6 long": how many files came from each length. A debug sample names its files, so it has one band. */
function bands(strata: Strata[] | null): string {
  if (!strata?.length) return "";
  const parts = strata.map((s) => `${s.chosen} ${s.band}`);
  return parts.length === 1 ? parts[0] : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function kindWords(kind: DevelopmentSample["kind"]): string {
  return kind === "debug" ? "to find a fault in the files named" : "to build and test with";
}

function EndButton({ sample }: { sample: DevelopmentSample }) {
  const end = useEndSample();
  return (
    <>
      <button
        type="button"
        data-testid={`sample-end-${sample.id}`}
        disabled={end.isPending}
        onClick={() => end.mutate(sample.id)}
        className="btn btn-secondary btn-sm"
      >
        {sample.status === "requested" ? "Withdraw this request" : "End this sample"}
      </button>
      {end.error ? (
        <div role="alert" className="mt-1 text-xs text-red-800">
          <Reasons error={end.error} fallback="That was not accepted." />
        </div>
      ) : null}
    </>
  );
}

function MySample({ sample }: { sample: DevelopmentSample }) {
  const live = sample.status === "requested" || (sample.status === "approved" && (!sample.expires_at || new Date(sample.expires_at) > new Date()));
  return (
    <li data-testid={`sample-row-${sample.id}`} className="space-y-1 rounded border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip sample={sample} />
        <span>
          {sample.status === "approved" || sample.status === "ended"
            ? `${sample.granted_count ?? sample.requested_count} files, ${kindWords(sample.kind)}`
            : `Asked for ${sample.requested_count} files for ${sample.days} days, ${kindWords(sample.kind)}`}
        </span>
      </div>
      <div className="text-xs text-slate-600">{sample.purpose}</div>
      {sample.status === "approved" && sample.expires_at ? (
        <div className="text-xs text-slate-600">
          {sample.granted_count} files: {bands(sample.strata)}. {new Date(sample.expires_at) > new Date() ? "Ends" : "Ended"} {when(sample.expires_at)}. Approved by{" "}
          {sample.decided_by_label}{sample.decided_at ? ` on ${when(sample.decided_at)}` : ""}.
        </div>
      ) : null}
      {sample.status === "declined" ? (
        <div className="text-xs text-slate-600">
          Declined by {sample.decided_by_label}
          {sample.decided_at ? ` on ${when(sample.decided_at)}` : ""}
          {sample.decision_note ? `: ${sample.decision_note}` : "."}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        {sample.sample_version_id && sample.status === "approved" && live ? (
          <Link data-testid={`sample-open-${sample.id}`} to={`/versions/${sample.sample_version_id}`} className="text-xs text-sky-700 underline">
            Open the sample
          </Link>
        ) : null}
        {live ? <EndButton sample={sample} /> : null}
      </div>
    </li>
  );
}

function Ask({ versionId, data }: { versionId: string; data: DevelopmentSamples }) {
  const ask = useAskForSample(versionId);
  const kinds = data.may_ask_kinds;
  const [chosen, setKind] = useState<"develop" | "debug">(kinds[0] ?? "develop");
  // The form follows what can be asked for now: when a kind is taken or becomes free again, it moves to the first kind that is open.
  useEffect(() => setKind(kinds[0] ?? "develop"), [kinds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const kind = kinds.includes(chosen) ? chosen : (kinds[0] ?? "develop");
  const [records, setRecords] = useState(Math.min(20, data.limits.records));
  const [days, setDays] = useState(Math.min(7, data.limits.days));
  const [purpose, setPurpose] = useState("");
  const [named, setNamed] = useState("");
  return (
    <div data-testid="sample-ask-form" className="space-y-3 rounded border border-slate-200 bg-white p-4 text-sm">
      <h3 className="font-medium">Ask for a sample</h3>
      <fieldset className="space-y-1">
        <legend className="sr-only">What the sample is for</legend>
        {kinds.includes("develop") && (
          <label className="flex items-start gap-2">
            <input type="radio" name="sample-kind" data-testid="sample-kind-develop" checked={kind === "develop"} onChange={() => setKind("develop")} className="mt-1" />
            <span>
              <span className="font-medium">Draw a sample for me.</span>
              <span className="block text-xs text-slate-600">The platform picks files about different people, spread across short, medium and long ones.</span>
            </span>
          </label>
        )}
        {kinds.includes("debug") && (
          <label className="flex items-start gap-2">
            <input type="radio" name="sample-kind" data-testid="sample-kind-debug" checked={kind === "debug"} onChange={() => setKind("debug")} className="mt-1" />
            <span>
              <span className="font-medium">Name the files that went wrong.</span>
              <span className="block text-xs text-slate-600">For finding a fault that appeared in production. You give the identifier of each file.</span>
            </span>
          </label>
        )}
      </fieldset>
      <div className="flex flex-wrap gap-6">
        <label className="block">
          <span className="block text-slate-600">How many files (at most {data.limits.records})</span>
          <input
            data-testid="sample-records"
            type="number"
            min={1}
            max={data.limits.records}
            value={records}
            onChange={(e) => setRecords(Number(e.target.value))}
            className="w-28 rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="block">
          <span className="block text-slate-600">For how many days (at most {data.limits.days})</span>
          <input
            data-testid="sample-days"
            type="number"
            min={1}
            max={data.limits.days}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="w-28 rounded border border-slate-300 px-2 py-1"
          />
        </label>
      </div>
      {kind === "debug" && (
        <label className="block">
          <span className="block text-slate-600">The files, one identifier on each line</span>
          <textarea data-testid="sample-named" rows={3} value={named} onChange={(e) => setNamed(e.target.value)} className="w-full max-w-xl rounded border border-slate-300 px-2 py-1 font-mono text-xs" />
        </label>
      )}
      <label className="block">
        <span className="block text-slate-600">What it is for</span>
        <textarea
          data-testid="sample-purpose"
          rows={2}
          value={purpose}
          onChange={(e) => setPurpose(e.target.value)}
          placeholder="Building a step that silences the speaker's name"
          className="w-full max-w-xl rounded border border-slate-300 px-2 py-1"
        />
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="sample-ask"
          disabled={ask.isPending || !purpose.trim() || (kind === "debug" && !named.trim())}
          onClick={() =>
            ask.mutate(
              {
                kind,
                purpose,
                days,
                records,
                named_records: kind === "debug" ? named.split(/\r?\n/).map((n) => n.trim()).filter(Boolean) : undefined,
              },
              {
                onSuccess: () => {
                  setPurpose("");
                  setNamed("");
                },
              },
            )
          }
          className="btn btn-primary"
        >
          Ask for a sample
        </button>
        <span className="text-xs text-slate-600">An approver of the department that owns these files, other than you, decides. They may lower the number or the days.</span>
      </div>
      {ask.error ? (
        <div role="alert" className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-900">
          <Reasons error={ask.error} fallback="That was not accepted." />
        </div>
      ) : null}
    </div>
  );
}

function Decide({ sample }: { sample: DevelopmentSample }) {
  const decide = useDecideSample();
  const [records, setRecords] = useState(sample.requested_count);
  const [days, setDays] = useState(sample.days);
  const [note, setNote] = useState("");
  return (
    <li data-testid={`sample-row-${sample.id}`} className="space-y-2 rounded border border-amber-200 bg-amber-50 p-3 text-sm">
      <div>
        <span className="font-medium">{sample.principal_label}</span> asks for {sample.requested_count} files for {sample.days} days, {kindWords(sample.kind)}.
      </div>
      <div className="text-xs text-slate-700">{sample.purpose}</div>
      {sample.kind === "debug" && sample.named_records ? <div className="font-mono text-xs text-slate-700">{sample.named_records.join(", ")}</div> : null}
      <div className="flex flex-wrap items-end gap-4 text-xs">
        <label className="block">
          <span className="block text-slate-600">Files (you may lower this)</span>
          <input
            data-testid={`sample-grant-records-${sample.id}`}
            type="number"
            min={1}
            max={sample.requested_count}
            value={records}
            onChange={(e) => setRecords(Number(e.target.value))}
            className="w-24 rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <label className="block">
          <span className="block text-slate-600">Days (you may lower this)</span>
          <input
            data-testid={`sample-grant-days-${sample.id}`}
            type="number"
            min={1}
            max={sample.days}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="w-24 rounded border border-slate-300 px-2 py-1"
          />
        </label>
        <input
          data-testid={`sample-note-${sample.id}`}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="A note, recorded with your decision"
          className="w-64 rounded border border-slate-300 px-2 py-1"
        />
      </div>
      <p className="text-xs text-slate-600">{sample.kind === "debug" ? "You cannot choose files: the ones named are the ones given." : "You cannot choose the files: the platform draws them."}</p>
      <div className="flex gap-2">
        <button
          type="button"
          data-testid={`sample-approve-${sample.id}`}
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: sample.id, verb: "approve", note, records, days })}
          className="btn btn-primary btn-sm"
        >
          Approve
        </button>
        <button
          type="button"
          data-testid={`sample-decline-${sample.id}`}
          disabled={decide.isPending}
          onClick={() => decide.mutate({ id: sample.id, verb: "decline", note })}
          className="btn btn-secondary btn-sm"
        >
          Decline
        </button>
      </div>
      {decide.error ? (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={decide.error} fallback="That was not accepted." />
        </div>
      ) : null}
    </li>
  );
}

function Synthetic({ versionId, data }: { versionId: string; data: DevelopmentSamples }) {
  const declare = useDeclareSynthetic(versionId);
  const [note, setNote] = useState("");
  if (data.synthetic) {
    return (
      <div data-testid="sample-synthetic" className="space-y-1 rounded border border-teal-200 bg-teal-50 p-3 text-sm">
        <p>
          Declared synthetic by {data.synthetic.declared_by_label} on {when(data.synthetic.declared_at)}.
        </p>
        <p>Reason given: {data.synthetic.note}</p>
        <p>No real person is in these files, so a pipeline version in development may run on them.</p>
      </div>
    );
  }
  if (!data.may_declare_synthetic) return null;
  return (
    <div className="space-y-2 rounded border border-slate-200 bg-white p-3 text-sm">
      <p>If these files hold no real person, declare them synthetic. Then any pipeline version in development may run on them, and nobody needs a sample.</p>
      <div className="flex flex-wrap items-center gap-2">
        <input
          data-testid="sample-synthetic-note"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Why: for example, read aloud by volunteers from invented scripts"
          className="w-full max-w-xl rounded border border-slate-300 px-2 py-1 text-xs"
        />
        <button
          type="button"
          data-testid="sample-synthetic-declare"
          disabled={declare.isPending || !note.trim()}
          onClick={() => declare.mutate(note)}
          className="btn btn-secondary btn-sm"
        >
          Declare these files synthetic
        </button>
      </div>
      {declare.error ? (
        <div role="alert" className="text-xs text-red-800">
          <Reasons error={declare.error} fallback="That was not accepted." />
        </div>
      ) : null}
    </div>
  );
}

export function DevelopmentSamplesSection({ versionId }: { versionId: string }) {
  const samples = useDevelopmentSamples(versionId);
  if (samples.isLoading) return null;
  if (samples.error) return <Failure error={samples.error} what="the development samples of this version" />;
  const data = samples.data;
  if (!data) return null;

  if (data.is_sample) {
    const s = data.is_sample;
    return (
      <Section title="Development sample">
        <div data-testid="sample-banner" className="space-y-1 rounded border border-teal-200 bg-teal-50 p-3 text-sm">
          <p>
            This is a development sample of {s.records} files, {s.mine ? "drawn for you" : "drawn for somebody else"}.{" "}
            {s.status === "approved" && s.expires_at && new Date(s.expires_at) > new Date() ? `It ends ${when(s.expires_at)}.` : "It has ended."} It names the same locked files as{" "}
            <Link to={`/versions/${s.source_version_id}`} className="text-sky-700 underline">
              the files it was drawn from
            </Link>{" "}
            and copies none of them.
          </p>
          <p className="text-xs text-slate-600">A pipeline version in development may run on it, for the person it was drawn for and nobody else.</p>
        </div>
      </Section>
    );
  }

  const lists = [...data.mine];
  const nothingToShow = !data.may_ask && !data.may_decide && !data.may_declare_synthetic && !lists.length && !data.synthetic;
  if (nothingToShow) return null;
  return (
    <Section
      title="Development samples"
      description="A pipeline version in development runs only on development data. A sample is a few of these files, drawn by the platform to build and test it on. It names the same locked files and copies none. It is for the person it was drawn for, and it ends on its own."
    >
      <div data-testid="development-samples" className="space-y-4">
        <Synthetic versionId={versionId} data={data} />
        {data.may_ask && data.may_ask_kinds.length > 0 ? <Ask versionId={versionId} data={data} /> : null}
        {lists.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Your samples</h3>
            <ul className="space-y-2">
              {lists.map((s) => (
                <MySample key={s.id} sample={s} />
              ))}
            </ul>
          </div>
        )}
        {data.to_decide.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">Waiting for your decision</h3>
            <ul className="space-y-2">
              {data.to_decide.map((s) => (
                <Decide key={s.id} sample={s} />
              ))}
            </ul>
          </div>
        )}
        {data.granted.length > 0 && (
          <div className="space-y-2">
            <h3 className="text-sm font-medium">In force for these files</h3>
            <ul className="space-y-1 text-sm">
              {data.granted.map((s) => (
                <li key={s.id} data-testid={`sample-granted-${s.id}`} className="flex flex-wrap items-center gap-2">
                  <span>
                    {s.principal_label}: {s.granted_count} files ({bands(s.strata)}), ends {when(s.expires_at)}.
                  </span>
                  <EndButton sample={s} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Section>
  );
}
