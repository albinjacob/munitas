/**
 * Bringing a dataset in: registering it, then handing over the files.
 *
 * Two steps on purpose, not one form. Registering exposes nothing and needs
 * nobody's approval, so it happens first and immediately, and it fixes the
 * owning department and the provenance before a single byte arrives. Only
 * once that exists does the upload area appear, because a file with nowhere
 * to belong is the state this platform's schema refuses to allow.
 *
 * Declaring anything less sensitive than the safe default is a claim, not a
 * fact, and the form says so before it is made rather than after.
 */

import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useIdentity } from "../../identity/IdentityContext";
import { useDataset, useOrganisation } from "../../api/queries";
import {
  useCancelHuggingFaceFetch,
  useConnectHuggingFace,
  useDisconnectHuggingFace,
  useFetchHuggingFace,
  useHuggingFaceAccount,
  useHuggingFaceFetchJobs,
  useRegisterDataset,
  useSealAudio,
  useSealDataset,
  useUploadFile,
  useWithdrawUpload,
  type HuggingFaceFetchJob,
  type RegisteredDataset,
} from "../../api/ingest";
import { ApiError } from "../../api/client";
import { PROVENANCE } from "../../api/provenance";
import { CLASS_LABEL, CLASS_ORDER, type VisibilityClass } from "../../api/types";
import { Failure, Loading, Section } from "../../components/states";

const MODALITIES = ["audio", "text", "image", "tabular"];

const MOST_RESTRICTIVE: VisibilityClass = "RAW";

function RegisterForm({
  onRegistered,
}: {
  onRegistered: (result: RegisteredDataset, name: string, provenance: string) => void;
}) {
  const { principal } = useIdentity();
  const organisation = useOrganisation();
  const register = useRegisterDataset();

  const [name, setName] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  // Set on blur, not on every keystroke: a required field reads as an
  // accusation if it turns red while somebody is still in the middle of
  // typing into the field next to it. The submit button stays disabled
  // regardless (nothing here changes what "ready" means), but a disabled
  // button gives no click to hang a validation message off of, so the
  // message has to appear some other way -- leaving a field empty is that
  // way.
  const [nameTouched, setNameTouched] = useState(false);
  const [departmentTouched, setDepartmentTouched] = useState(false);
  const [provenance, setProvenance] = useState(PROVENANCE[2].value);
  const [declaredClass, setDeclaredClass] =
    useState<VisibilityClass>(MOST_RESTRICTIVE);
  const [modality, setModality] = useState<string[]>([]);

  const claiming = declaredClass !== MOST_RESTRICTIVE;
  const ready = name.trim().length > 0 && departmentId.length > 0;

  return (
    <form
      data-testid="register-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready || !principal) return;
        register.mutate(
          {
            name: name.trim(),
            department_id: departmentId,
            registered_by: principal.id,
            provenance: provenance as
              | "external_public"
              | "external_licensed"
              | "internal_regulated",
            declared_class: declaredClass,
            modality,
          },
          { onSuccess: (result) => onRegistered(result, name.trim(), provenance) },
        );
      }}
      className="space-y-4 rounded border border-slate-200 bg-white p-4"
    >
      <div>
        <label htmlFor="name" className="block text-sm font-medium text-slate-700">
          Name
        </label>
        <input
          id="name"
          data-testid="register-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={() => setNameTouched(true)}
          placeholder="triage-call-recordings"
          className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
        />
        {nameTouched && name.trim().length === 0 && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            A name is required.
          </p>
        )}
      </div>

      <div>
        <label htmlFor="department" className="block text-sm font-medium text-slate-700">
          Owned by
        </label>
        <p className="mt-0.5 text-xs text-slate-500">
          Required. From the moment this exists, somebody is answerable for
          deciding who may read it, and this is who.
        </p>
        <select
          id="department"
          data-testid="register-department"
          value={departmentId}
          onChange={(e) => setDepartmentId(e.target.value)}
          onBlur={() => setDepartmentTouched(true)}
          className="mt-1.5 rounded border border-slate-300 px-3 py-1.5 text-sm"
        >
          <option value="">Choose a department</option>
          {(organisation.data?.departments ?? []).map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        {departmentTouched && departmentId.length === 0 && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            Choose which department is answerable for this.
          </p>
        )}
      </div>

      <fieldset>
        <legend className="text-sm font-medium text-slate-700">
          Where did it come from?
        </legend>
        <p className="mt-0.5 text-xs text-slate-500">
          Decides whether this may ever leave the platform. Separate from how
          sensitive it is: de-identified clinical data can be low sensitivity
          and still not be redistributable.
        </p>
        <div className="mt-1.5 space-y-2">
          {PROVENANCE.map((p) => (
            <label key={p.value} className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="provenance"
                data-testid={`provenance-${p.value}`}
                checked={provenance === p.value}
                onChange={() => setProvenance(p.value)}
                className="mt-0.5"
              />
              <span>
                <span className="font-medium">{p.label}</span>
                <span className="block text-xs text-slate-500">{p.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div>
        <label htmlFor="class" className="block text-sm font-medium text-slate-700">
          What access level?
        </label>
        <p className="mt-0.5 text-xs text-slate-500">
          {/*
            The asymmetry is the whole point of this field, and it is stated
            here rather than left to be discovered from a later refusal.
            Asking for more restriction than the default costs nothing to be
            wrong about. Asking for less is a claim, attributed to whoever
            makes it, and the owning custodian has to agree before this can
            be released beyond it.
          */}
          Leaving this at the most restrictive setting makes no claim about the
          data. Choosing anything else is a claim, recorded against your name,
          and {organisation.data ? "the department's custodian" : "a custodian"}{" "}
          must agree with it before the data can be released beyond it.
        </p>
        <select
          id="class"
          data-testid="register-class"
          value={declaredClass}
          onChange={(e) => setDeclaredClass(e.target.value as VisibilityClass)}
          className="mt-1.5 rounded border border-slate-300 px-3 py-1.5 text-sm"
        >
          {CLASS_ORDER.map((c) => (
            <option key={c} value={c}>
              {CLASS_LABEL[c]}
              {c === MOST_RESTRICTIVE ? " (the safe default)" : ""}
            </option>
          ))}
        </select>
        {claiming && (
          <p
            data-testid="register-claim-notice"
            className="mt-1.5 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900"
          >
            You are claiming this is {CLASS_LABEL[declaredClass]}. That claim
            will be recorded under your name and must be confirmed by the
            owning custodian before anyone can be granted more than the
            default allows.
          </p>
        )}
      </div>

      <fieldset>
        <legend className="text-sm font-medium text-slate-700">
          What kind of data is it?
        </legend>
        <p className="mt-0.5 text-xs text-slate-500">
          Declared, not guessed from file names. Optional.
        </p>
        <div className="mt-1.5 flex flex-wrap gap-3">
          {MODALITIES.map((m) => (
            <label key={m} className="flex items-center gap-1.5 text-sm">
              <input
                type="checkbox"
                data-testid={`modality-${m}`}
                checked={modality.includes(m)}
                onChange={(e) =>
                  setModality((prev) =>
                    e.target.checked ? [...prev, m] : prev.filter((x) => x !== m),
                  )
                }
              />
              {m}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          data-testid="register-submit"
          disabled={!ready || register.isPending}
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {register.isPending ? "Registering" : "Register"}
        </button>
        <span className="text-xs text-slate-500">
          Nothing is readable yet. This only creates the record and its owner.
        </span>
      </div>

      {register.error && (
        <p className="text-xs text-red-800" role="alert">
          {register.error instanceof Error
            ? register.error.message
            : "That did not work."}
        </p>
      )}
    </form>
  );
}

type SourceMode = "upload" | "huggingface";

/**
 * A registered human's own HuggingFace account, connected or not.
 *
 * Inline here rather than on a separate settings screen, so nobody has to
 * find a page before they know they need one: this is the one place a
 * gated repo actually comes up.
 */
function HuggingFaceAccountPanel({
  directoryId,
  openByDefault,
}: {
  directoryId: string;
  openByDefault: boolean;
}) {
  const account = useHuggingFaceAccount(directoryId);
  const connect = useConnectHuggingFace();
  const disconnect = useDisconnectHuggingFace();
  const [editing, setEditing] = useState(openByDefault);
  const [token, setToken] = useState("");

  // A fetch can fail with "connect an account" after this panel already
  // mounted closed, so opening it has to react to that happening, not just
  // seed the initial state.
  useEffect(() => {
    if (openByDefault) setEditing(true);
  }, [openByDefault]);

  if (account.isLoading) return null;

  if (account.data?.connected && !editing) {
    return (
      <p className="text-xs text-slate-600">
        Fetching as {account.data.hf_username} on HuggingFace.{" "}
        <button
          type="button"
          data-testid="hf-account-disconnect"
          onClick={() => disconnect.mutate(directoryId)}
          disabled={disconnect.isPending}
          className="text-sky-700 underline"
        >
          {disconnect.isPending ? "Disconnecting" : "Disconnect"}
        </button>
      </p>
    );
  }

  if (!editing) {
    return (
      <p className="text-xs text-slate-600">
        No HuggingFace account connected. Public repositories work without
        one; a gated repository needs your own account, granted access on
        HuggingFace's side.{" "}
        <button
          type="button"
          data-testid="hf-account-connect-open"
          onClick={() => setEditing(true)}
          className="text-sky-700 underline"
        >
          Connect your HuggingFace account
        </button>
      </p>
    );
  }

  return (
    <div
      data-testid="hf-account-connect-form"
      className="space-y-1.5 rounded border border-slate-200 bg-slate-50 p-3"
    >
      <label htmlFor="hf-token" className="block text-xs font-medium text-slate-700">
        HuggingFace access token
      </label>
      <p className="text-xs text-slate-500">
        A <span className="font-medium">read</span> token is enough; this
        platform only ever downloads files, never writes to HuggingFace. Get
        one at{" "}
        <a
          href="https://huggingface.co/settings/tokens"
          target="_blank"
          rel="noreferrer"
          className="text-sky-700 underline"
        >
          huggingface.co/settings/tokens
        </a>
        .
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input
          id="hf-token"
          data-testid="hf-token-input"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="hf_..."
          className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
        />
        <button
          type="button"
          data-testid="hf-token-save"
          disabled={!token.trim() || connect.isPending}
          onClick={() =>
            connect.mutate(
              { directoryId, token: token.trim() },
              { onSuccess: () => { setToken(""); setEditing(false); } },
            )
          }
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-2 py-1 text-xs font-medium text-white disabled:opacity-50"
        >
          {connect.isPending ? "Checking" : "Connect"}
        </button>
        <button
          type="button"
          onClick={() => { setEditing(false); setToken(""); }}
          className="text-xs text-slate-500 underline"
        >
          Cancel
        </button>
      </div>
      {connect.error && (
        <p className="text-xs text-red-800" role="alert">
          {connect.error instanceof ApiError && connect.error.reasons.length
            ? connect.error.reasons.join("; ")
            : "That token did not work."}
        </p>
      )}
    </div>
  );
}

/** A count of bytes, shown the way a person reads file sizes, not the way a
 * database stores them. */
function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = n / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${units[unit]}`;
}

function HuggingFaceFetch({
  datasetId,
  latestJob,
  sealed,
}: {
  datasetId: string;
  latestJob: HuggingFaceFetchJob | undefined;
  sealed: boolean;
}) {
  const { principal } = useIdentity();
  const fetchHf = useFetchHuggingFace();
  const cancelHf = useCancelHuggingFaceFetch();

  const [repoId, setRepoId] = useState("");
  const [revision, setRevision] = useState("main");
  const [path, setPath] = useState("");

  const ready = repoId.trim().length > 0;
  const running = latestJob?.status === "running";

  // Two different failures both end in "go open the connect-account form":
  // no account connected at all, or one connected whose token has since
  // been revoked or expired on HuggingFace's own side (worker/
  // hf_ingest_activities.py's `_token_still_works` is what tells that apart
  // from a token that still works but simply lacks access to this one
  // repo, which is a different failure with a different fix and must not
  // reopen this form). Detected here so either failure becomes a next
  // step, not a dead end.
  const needsAccount =
    latestJob?.status === "failed" &&
    Boolean(
      latestJob.error?.includes("connect a HuggingFace account") ||
        latestJob.error?.includes("token no longer works"),
    );

  return (
    <div className="space-y-3">
      {principal && (
        <HuggingFaceAccountPanel directoryId={principal.id} openByDefault={needsAccount} />
      )}

      <div>
        <label htmlFor="hf-repo" className="block text-sm font-medium text-slate-700">
          Repository
        </label>
        <p className="mt-0.5 text-xs text-slate-500">
          The part of the URL after huggingface.co/datasets/, e.g.{" "}
          <code className="font-mono">xhluca/publichealth-qa</code>.
        </p>
        <input
          id="hf-repo"
          data-testid="hf-repo-id"
          value={repoId}
          onChange={(e) => setRepoId(e.target.value)}
          placeholder="xhluca/publichealth-qa"
          className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
        />
      </div>

      <div className="flex flex-wrap gap-3">
        <div>
          <label htmlFor="hf-revision" className="block text-sm font-medium text-slate-700">
            Revision
          </label>
          <p className="mt-0.5 text-xs text-slate-500">
            The branch, tag or commit to fetch from. Leave as{" "}
            <code className="font-mono">main</code> unless you need a
            specific one.
          </p>
          <input
            id="hf-revision"
            data-testid="hf-revision"
            value={revision}
            onChange={(e) => setRevision(e.target.value)}
            className="mt-1.5 w-32 rounded border border-slate-300 px-3 py-1.5 text-sm"
          />
        </div>
        <div>
          <label htmlFor="hf-path" className="block text-sm font-medium text-slate-700">
            Folder within the repo
          </label>
          <p className="mt-0.5 text-xs text-slate-500">Optional. Blank means everything.</p>
          <input
            id="hf-path"
            data-testid="hf-path"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="data"
            className="mt-1.5 w-40 rounded border border-slate-300 px-3 py-1.5 text-sm"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="hf-fetch"
          disabled={!ready || !principal || fetchHf.isPending || running || sealed}
          onClick={() =>
            fetchHf.mutate({
              datasetId,
              fetchedBy: principal!.id,
              repoId: repoId.trim(),
              revision: revision.trim() || "main",
              path: path.trim(),
            })
          }
          className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
        >
          {fetchHf.isPending || running ? "Fetching" : "Fetch"}
        </button>
        {running && (
          <button
            type="button"
            data-testid="hf-cancel"
            disabled={cancelHf.isPending}
            onClick={() => cancelHf.mutate({ datasetId, jobId: latestJob!.id })}
            className="rounded border border-red-300 px-3 py-1.5 text-sm font-medium text-red-800 disabled:opacity-50"
          >
            {cancelHf.isPending ? "Cancelling" : "Cancel"}
          </button>
        )}
        <span className="text-xs text-slate-500">
          The platform requests these itself, in the background. Because it
          is the one making the request, a sensitivity claim here does not
          need the custodian's confirmation the way an upload's does.
        </span>
      </div>

      {cancelHf.error && (
        <p className="text-xs text-red-800" role="alert">
          {cancelHf.error instanceof Error
            ? cancelHf.error.message
            : "Could not cancel the fetch."}
        </p>
      )}

      {fetchHf.error && (
        <p className="text-xs text-red-800" role="alert">
          {fetchHf.error instanceof Error
            ? fetchHf.error.message
            : "Could not start the fetch."}
        </p>
      )}

      {latestJob && (
        <div
          data-testid="hf-job-status"
          className={`rounded border px-3 py-2 text-xs ${
            latestJob.status === "failed"
              ? "border-red-300 bg-red-50 text-red-900"
              : latestJob.status === "cancelled"
                ? "border-slate-300 bg-slate-50 text-slate-700"
                : latestJob.status === "running"
                  ? "border-sky-300 bg-sky-50 text-sky-900"
                  : "border-teal-300 bg-teal-50 text-teal-900"
          }`}
        >
          <p className="font-medium">
            {latestJob.repo_id}
            {latestJob.path ? `/${latestJob.path}` : ""} @ {latestJob.revision}
          </p>
          {latestJob.status === "running" && (
            <p>
              Ingestion in progress:{" "}
              {latestJob.files_total != null
                ? `${latestJob.files_done} of ${latestJob.files_total} files`
                : "listing the repository"}
              {latestJob.bytes_total
                ? `, ${formatBytes(latestJob.bytes_done)} of ${formatBytes(latestJob.bytes_total)}`
                : ""}
              . This page can be left and reopened; the fetch keeps running.
            </p>
          )}
          {latestJob.status === "succeeded" && (
            <p>
              Fetched {latestJob.files_done} file
              {latestJob.files_done === 1 ? "" : "s"} (
              {formatBytes(latestJob.bytes_done)}). The licence HuggingFace
              lists for this repository now decides where this dataset may
              go.
            </p>
          )}
          {/* An alert, like every other refusal on this console. A failed
              fetch is the one thing on this panel somebody has to act on,
              and it was reachable by sight only. */}
          {latestJob.status === "failed" && (
            <p role="alert">{latestJob.error}</p>
          )}
          {latestJob.status === "cancelled" && (
            <p>
              Cancelled before it finished. Files already fetched before the
              cancellation are kept; fetching this repository again picks up
              from there rather than starting over.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function UploadStep({
  datasetId,
  datasetName,
  note,
}: {
  datasetId: string;
  datasetName: string;
  note: string;
}) {
  const navigate = useNavigate();
  const { principal } = useIdentity();
  const upload = useUploadFile();
  const seal = useSealDataset();
  const sealAudio = useSealAudio();
  const withdraw = useWithdrawUpload();
  const jobs = useHuggingFaceFetchJobs(datasetId);
  const latestJob = jobs.data?.[0];
  const [source, setSource] = useState<SourceMode>("upload");
  const [uploaded, setUploaded] = useState<
    {
      sourceId?: string;
      filename: string;
      bytes: number;
      source: SourceMode;
      seconds?: number;
      sampleRate?: number;
      hasHazard?: boolean;
      withdrawn?: boolean;
    }[]
  >([]);

  const sealed = seal.isSuccess || sealAudio.isSuccess;
  // Whose job it is to start a run. The console hides the button from anyone
  // else as a courtesy; POST .../deidentify asks the policy engine and is what
  // actually refuses them.
  const maySeal = principal?.roles.includes("pipeline_operator") ?? false;
  const pending = uploaded.filter((f) => !f.withdrawn);
  // Offered only once a recording is actually present, so the choice appears
  // when it means something rather than sitting greyed out on every dataset.
  const hasAudio = pending.some((f) => f.filename.endsWith(".wav"));

  // A summary row once a fetch succeeds, so the file list is not empty
  // while the actual files exist only as `dataset_source` rows the
  // background job wrote, not as anything the browser ever saw.
  const [summarisedJobId, setSummarisedJobId] = useState<string | null>(null);
  useEffect(() => {
    if (latestJob?.status === "succeeded" && latestJob.id !== summarisedJobId) {
      setUploaded((prev) => [
        ...prev,
        {
          filename: `${latestJob.files_done} file${latestJob.files_done === 1 ? "" : "s"} from ${latestJob.repo_id}`,
          bytes: latestJob.bytes_done,
          source: "huggingface",
        },
      ]);
      setSummarisedJobId(latestJob.id);
    }
  }, [latestJob, summarisedJobId]);

  return (
    <div
      data-testid="upload-step"
      className="space-y-4 rounded border border-slate-200 bg-white p-4"
    >
      <p className="text-sm text-slate-700">
        <span className="font-medium">{datasetName}</span> is registered,
        owned by the department you chose. {note}
      </p>

      <div className="flex gap-4 border-b border-slate-200 pb-3 text-sm">
        {(
          [
            ["upload", "Upload files"],
            ["huggingface", "Fetch from HuggingFace"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            data-testid={`source-${value}`}
            disabled={sealed}
            onClick={() => setSource(value)}
            className={`rounded px-2 py-1 font-medium ${
              source === value
                ? "bg-indigo-500 hover:bg-indigo-800 text-white"
                : "text-slate-600 hover:bg-slate-100"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {source === "upload" ? (
        <div>
          <label
            htmlFor="files"
            className="block text-sm font-medium text-slate-700"
          >
            Add the files
          </label>
          <input
            id="files"
            type="file"
            multiple
            data-testid="upload-files"
            disabled={sealed}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? []);
              // upload is one useMutation() hook shared by every file in this
              // selection, so it is one MutationObserver underneath. Calling
              // upload.mutate() a second time before the first has settled
              // detaches that observer from the first upload's mutation and
              // reattaches it to the second, which silently drops the first
              // upload's onSuccess callback even though the upload itself
              // still completes on the server. mutateAsync() sidesteps this:
              // each call still shares the observer, but reading the result
              // off the promise mutateAsync() returns (rather than off an
              // onSuccess passed into the call) reads the state of that
              // specific upload's own request, not whatever the observer is
              // currently attached to.
              for (const file of files) {
                upload
                  .mutateAsync({ datasetId, file })
                  .then((r) =>
                    setUploaded((prev) => [
                      ...prev,
                      {
                        sourceId: r.source_id,
                        filename: r.filename,
                        bytes: r.bytes,
                        source: "upload",
                        seconds: r.audio_duration_seconds,
                        sampleRate: r.audio_sample_rate,
                        hasHazard: r.truth_has_hazard,
                      },
                    ]),
                  )
                  .catch(() => {
                    // Surfaced already: upload.error reflects whichever of
                    // this selection's uploads the shared mutation state
                    // last settled on.
                  });
              }
              e.target.value = "";
            }}
            className="mt-1.5 block text-sm text-slate-600 file:mr-3 file:rounded file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100 disabled:opacity-50"
          />
        </div>
      ) : (
        <HuggingFaceFetch
          datasetId={datasetId}
          latestJob={latestJob}
          sealed={seal.isSuccess}
        />
      )}

      {uploaded.length > 0 && (
        <ul data-testid="uploaded-files" className="space-y-1 text-sm text-slate-700">
          {uploaded.map((f) => (
            <li
              key={`${f.source}-${f.filename}`}
              className={`flex flex-wrap items-center gap-2 ${f.withdrawn ? "opacity-50" : ""}`}
            >
              <span className="rounded bg-teal-100 px-1.5 py-0.5 text-xs font-medium text-teal-900">
                {f.source === "upload" ? "Uploaded" : "Fetched"}
              </span>
              <span className={f.withdrawn ? "line-through" : ""}>{f.filename}</span>
              <span className="text-xs text-slate-400">{f.bytes} bytes</span>
              {/* What the platform read out of the file itself, so it is
                  visible that the recording was read and not only stored. */}
              {f.sampleRate ? (
                <span className="text-xs text-slate-500">
                  {f.seconds?.toFixed(1)}s at {f.sampleRate} Hz
                </span>
              ) : null}
              {/* Named, not explained. Somebody who meant to include the flag
                  can see it is missing; what it is used for belongs where that
                  report is read, not in an upload list. */}
              {f.hasHazard === false ? (
                <span className="text-xs text-amber-700">no hazard flag</span>
              ) : null}
              {f.withdrawn ? (
                <span className="text-xs text-slate-500">withdrawn</span>
              ) : f.sourceId && !sealed ? (
                <button
                  type="button"
                  data-testid={`withdraw-${f.filename}`}
                  onClick={() =>
                    withdraw.mutate(
                      { datasetId, sourceId: f.sourceId as string },
                      {
                        onSuccess: () =>
                          setUploaded((prev) =>
                            prev.map((row) =>
                              row.sourceId === f.sourceId
                                ? { ...row, withdrawn: true }
                                : row,
                            ),
                          ),
                      },
                    )
                  }
                  className="text-xs text-slate-500 underline hover:text-slate-900"
                >
                  Withdraw
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {withdraw.error && (
        <p className="text-xs text-red-800" role="alert">
          {withdraw.error instanceof Error
            ? withdraw.error.message
            : "That file could not be withdrawn."}
        </p>
      )}

      {upload.error && (
        <p className="text-xs text-red-800" role="alert">
          {upload.error instanceof Error
            ? upload.error.message
            : "That file did not upload."}
        </p>
      )}

      {sealed ? (
        <div className="space-y-3">
          <p
            data-testid="seal-done"
            className="rounded border border-teal-300 bg-teal-50 p-3 text-sm text-teal-900"
          >
            {sealAudio.isSuccess
              ? `Sealed as version 1, ${sealAudio.data?.records} recording${sealAudio.data?.records === 1 ? "" : "s"} the de-identification pipeline can read.`
              : `Sealed as version 1, ${seal.data?.files} file${seal.data?.files === 1 ? "" : "s"}.`}{" "}
            Nothing about it can change from here; a later upload becomes a new
            version.
          </p>

          {/* Starting a pipeline lives on the version's own page, not here:
              this screen exists only for the moment the upload closes, and
              a version somebody opens later has no upload session to land
              in. Only to the role whose job it is; the endpoint is what
              actually refuses anyone else. */}
          {maySeal && (sealAudio.isSuccess ? sealAudio.data : seal.data) && (
            <Link
              to={`/versions/${sealAudio.isSuccess ? sealAudio.data.id : seal.data!.id}`}
              className="inline-block text-sm text-sky-700 underline"
            >
              Run a pipeline against this version
            </Link>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              data-testid="seal-submit"
              disabled={
                !pending.length || seal.isPending || latestJob?.status === "running"
              }
              onClick={() =>
                seal.mutate(datasetId, {
                  onSuccess: (r) => {
                    // A moment to read the confirmation before leaving, rather
                    // than being dropped onto another page mid-sentence.
                    setTimeout(() => navigate(`/versions/${r.id}`), 1200);
                  },
                })
              }
              className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
            >
              {seal.isPending ? "Sealing" : "Seal this as version 1"}
            </button>
            <span className="text-xs text-slate-500">
              Closes the upload. Add every file first; nothing can be added to
              this version afterwards.
            </span>
          </div>

          {hasAudio && (
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                data-testid="seal-audio-submit"
                disabled={sealAudio.isPending || latestJob?.status === "running"}
                onClick={() =>
                  sealAudio.mutate(datasetId, {
                    onSuccess: (r) => {
                      setTimeout(() => navigate(`/versions/${r.id}`), 1200);
                    },
                  })
                }
                className="rounded border border-slate-900 px-3 py-1.5 text-sm font-medium text-slate-900 disabled:opacity-50"
              >
                {sealAudio.isPending ? "Sealing" : "Seal as recordings"}
              </button>
              <span className="text-xs text-slate-500">
                Use this if these are recordings to be de-identified. Each
                <code className="mx-1">.wav</code> becomes one record, and a
                matching <code className="mx-1">.truth.json</code> is its answer
                key.
              </span>
            </div>
          )}
        </div>
      )}

      {seal.error && (
        <p className="text-xs text-red-800" role="alert">
          {seal.error instanceof Error ? seal.error.message : "Sealing failed."}
        </p>
      )}

      {/* Every reason, not the first. A set with four problems should take one
          look to understand rather than four attempts. */}
      {sealAudio.error && (
        <div
          data-testid="seal-audio-refused"
          className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800"
          role="alert"
        >
          <p className="font-medium">Nothing was sealed. Fix these first:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {(sealAudio.error instanceof ApiError && sealAudio.error.reasons.length
              ? sealAudio.error.reasons
              : [
                  sealAudio.error instanceof Error
                    ? sealAudio.error.message
                    : "Sealing failed.",
                ]
            ).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function RegisterDataset() {
  const [registered, setRegistered] = useState<
    { result: RegisteredDataset; name: string; provenance: string } | null
  >(null);

  return (
    <Section
      level="page"
      title="Bring a dataset in"
      description="Registering names an owner and where the data came from. Nothing is readable until it is sealed, and, if you claimed less sensitivity than the default, until the owner agrees."
    >
      <RegisterForm
        onRegistered={(result, name, provenance) =>
          setRegistered({ result, name, provenance })
        }
      />

      {registered && (
        <div className="mt-4">
          <UploadStep
            datasetId={registered.result.id}
            datasetName={registered.name}
            note={registered.result.note}
          />
        </div>
      )}
    </Section>
  );
}

/**
 * Resuming a dataset that was registered and then left.
 *
 * Registering and bringing data in were always two separate steps
 * (`RegisterDataset` above only ever showed the second one immediately
 * after the first, in the same page load), but until this existed there
 * was no way back to the second step once you had navigated away. The
 * `Datasets` list links here for any dataset with nothing in it yet.
 */
export function ResumeIngest() {
  const { datasetId } = useParams<{ datasetId: string }>();
  const dataset = useDataset(datasetId);

  if (dataset.isLoading) return <Loading what="the dataset" />;
  if (dataset.error) return <Failure error={dataset.error} what="the dataset" />;
  if (!dataset.data) return <Failure error={new Error("not found")} what="the dataset" />;

  return (
    <Section
      level="page"
      title={`Bring data into ${dataset.data.name}`}
      description="Registered, and still empty. Upload files or fetch from HuggingFace, the same as right after registering."
    >
      <UploadStep
        datasetId={dataset.data.id}
        datasetName={dataset.data.name}
        note="Nothing is readable yet. It becomes readable when the data is sealed."
      />
    </Section>
  );
}
