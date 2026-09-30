/**
 * Uploading a project's own code as an agent's next version.
 *
 * The other way to register a version, alongside `python -m
 * agent.register_version` (see `RegisterAgent.tsx`'s own comment). That path
 * only ever describes code the platform will not run; this one is for code
 * the platform actually executes, sandboxed, once deployed. A `.zip` is
 * required rather than a picked folder, with no client-side zipping in this
 * first version, so the person uploading zips their own folder first.
 */

import { useRef, useState } from "react";
import { useUploadAgentVersion } from "../../api/agents";
import { useIdentity } from "../../identity/IdentityContext";

export function UploadAgentVersion({ agentId }: { agentId: string }) {
  const { principal } = useIdentity();
  const upload = useUploadAgentVersion(agentId);
  const fileInput = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [modelId, setModelId] = useState("");
  const [toolScope, setToolScope] = useState("");
  const [requestedHosts, setRequestedHosts] = useState("");
  // Collapsed by default: both fields are genuinely optional (empty is a
  // valid, common answer for either), unlike the project file and model
  // above them, so most people filling this in never need to open it.
  // Opens itself if either already holds a value (editing an existing
  // draft, or a browser restoring form state) so nothing gets silently
  // hidden that the person already typed.
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Shown on blur, not every keystroke: the submit button is disabled until
  // the form is already valid, so a click never fires to hang a "show
  // errors now" trigger off of. Blur is the substitute, including for the
  // file picker, which fires blur once its own dialog closes.
  const [fileTouched, setFileTouched] = useState(false);
  const [modelTouched, setModelTouched] = useState(false);

  const ready = Boolean(file && modelId.trim().length > 0 && principal);

  return (
    <form
      data-testid="upload-agent-version-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready || !file || !principal) return;
        upload.mutate(
          {
            zip: file,
            modelId: modelId.trim(),
            toolScope: toolScope.trim(),
            requestedHosts: requestedHosts.trim(),
            registeredBy: principal.id,
          },
          {
            onSuccess: () => {
              setFile(null);
              setModelId("");
              setToolScope("");
              setRequestedHosts("");
              setAdvancedOpen(false);
              if (fileInput.current) fileInput.current.value = "";
            },
          },
        );
      }}
      className="space-y-3 rounded border border-slate-200 bg-white p-4"
    >
      <p className="text-xs text-slate-500">
        Upload a .zip of your own project. It must contain a{" "}
        <code className="font-mono">munitas.json</code> naming its
        entrypoint script (defaults to <code className="font-mono">main.py</code>).
        The platform runs that script in an isolated container, with no
        network access beyond its own credential requests, whenever this
        version is deployed and a run starts.
      </p>

      <div>
        <label htmlFor="agent-version-zip" className="block text-sm font-medium text-slate-700">
          Project (.zip)
        </label>
        <input
          id="agent-version-zip"
          data-testid="agent-version-zip"
          ref={fileInput}
          type="file"
          accept=".zip"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          onBlur={() => setFileTouched(true)}
          className="mt-1.5 block text-sm text-slate-600 file:mr-3 file:rounded file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
        />
        {fileTouched && !file && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            A project .zip is required.
          </p>
        )}
      </div>

      <div>
        <label htmlFor="agent-version-model" className="block text-sm font-medium text-slate-700">
          Model
        </label>
        <input
          id="agent-version-model"
          data-testid="agent-version-model"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
          onBlur={() => setModelTouched(true)}
          placeholder="e.g. gpt-4.1, or none if this code calls no model"
          className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
        />
        {modelTouched && modelId.trim().length === 0 && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            Say what model this calls, or "none" if it calls no model.
          </p>
        )}
      </div>

      <div className="border-t border-slate-200 pt-3">
        <button
          type="button"
          data-testid="agent-version-advanced-toggle"
          onClick={() => setAdvancedOpen((open) => !open)}
          className="flex items-center gap-1.5 text-sm font-medium text-slate-700"
          aria-expanded={advancedOpen}
        >
          <span
            className={`inline-block transition-transform ${advancedOpen ? "rotate-90" : ""}`}
            aria-hidden="true"
          >
            &rsaquo;
          </span>
          Advanced: tools and hosts it may call
          {!advancedOpen && (toolScope || requestedHosts) && (
            <span className="text-xs font-normal text-slate-400">(set)</span>
          )}
        </button>

        {advancedOpen && (
          <div className="mt-3 space-y-3">
            <div>
              <label htmlFor="agent-version-tools" className="block text-sm font-medium text-slate-700">
                Tools it may call
              </label>
              <p className="mt-0.5 text-xs text-slate-500">
                Comma-separated. Declarative only: a fast pre-check inside code the
                platform controls, not a security boundary for uploaded code.
              </p>
              <input
                id="agent-version-tools"
                data-testid="agent-version-tools"
                value={toolScope}
                onChange={(e) => setToolScope(e.target.value)}
                placeholder="e.g. read_dataset_version, summarise_counts"
                className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
              />
            </div>

            <div>
              <label htmlFor="agent-version-hosts" className="block text-sm font-medium text-slate-700">
                Hosts it may call
              </label>
              <p className="mt-0.5 text-xs text-slate-500">
                Comma-separated. Leave empty if this code calls nothing outside
                the platform. A non-empty list has to be approved by a network
                architect before this version can be deployed.
              </p>
              <input
                id="agent-version-hosts"
                data-testid="agent-version-hosts"
                value={requestedHosts}
                onChange={(e) => setRequestedHosts(e.target.value)}
                placeholder="e.g. huggingface.co"
                className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
              />
            </div>
          </div>
        )}
      </div>

      <button
        type="submit"
        data-testid="upload-agent-version-submit"
        disabled={!ready || upload.isPending}
        className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {upload.isPending ? "Uploading" : "Upload version"}
      </button>

      {upload.error && (
        <p className="text-xs text-red-800" role="alert">
          {upload.error instanceof Error ? upload.error.message : "That did not work."}
        </p>
      )}
    </form>
  );
}
