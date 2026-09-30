/**
 * Uploading a sealed version of a pipeline: one YAML file naming the steps
 * in a DAG, one zip holding every script those steps reference. Sealed on
 * arrival, the same as an agent's code version: there is no editing a
 * version in place, only registering the next one.
 */

import { useRef, useState } from "react";
import { useUploadPipelineVersion } from "../../api/dag_pipelines";
import { useIdentity } from "../../identity/IdentityContext";
import { ApiError } from "../../api/client";

export function UploadPipelineVersion({ pipelineId }: { pipelineId: string }) {
  const { principal } = useIdentity();
  const upload = useUploadPipelineVersion(pipelineId);
  const configInput = useRef<HTMLInputElement>(null);
  const scriptsInput = useRef<HTMLInputElement>(null);

  const [configFile, setConfigFile] = useState<File | null>(null);
  const [scripts, setScripts] = useState<File | null>(null);

  // Shown on blur, not every keystroke: the submit button is disabled until
  // the form is already valid, so a click never fires to hang a "show
  // errors now" trigger off of. Blur is the substitute, including for a
  // file picker, which fires blur once its own dialog closes.
  const [configTouched, setConfigTouched] = useState(false);
  const [scriptsTouched, setScriptsTouched] = useState(false);

  const ready = Boolean(configFile && scripts && principal);

  return (
    <form
      data-testid="upload-pipeline-version-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready || !configFile || !scripts || !principal) return;
        upload.mutate(
          { configFile, scripts, registeredBy: principal.id },
          {
            onSuccess: () => {
              setConfigFile(null);
              setScripts(null);
              if (configInput.current) configInput.current.value = "";
              if (scriptsInput.current) scriptsInput.current.value = "";
            },
          },
        );
      }}
      className="space-y-3 rounded border border-slate-200 bg-white p-4"
    >
      <p className="text-xs text-slate-500">
        A YAML file naming each step in order, and a .zip holding every
        script those steps reference by relative path. Every step's
        dependencies, and every script it names, are checked before this
        version can be sealed.
      </p>

      <div>
        <label htmlFor="pipeline-version-config" className="block text-sm font-medium text-slate-700">
          Pipeline config (.yaml)
        </label>
        <input
          id="pipeline-version-config"
          data-testid="pipeline-version-config"
          ref={configInput}
          type="file"
          accept=".yaml,.yml"
          onChange={(e) => setConfigFile(e.target.files?.[0] ?? null)}
          onBlur={() => setConfigTouched(true)}
          className="mt-1.5 block text-sm text-slate-600 file:mr-3 file:rounded file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
        />
        {configTouched && !configFile && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            A pipeline config is required.
          </p>
        )}
        <a
          href="/pipeline-templates/redact-without-scoring/redact_without_scoring.yaml"
          download
          data-testid="pipelines-download-template-link"
          className="mt-1.5 inline-block text-xs text-sky-700 underline"
        >
          Download an example config (redact-without-scoring)
        </a>
      </div>

      <div>
        <label htmlFor="pipeline-version-scripts" className="block text-sm font-medium text-slate-700">
          Scripts (.zip)
        </label>
        <input
          id="pipeline-version-scripts"
          data-testid="pipeline-version-scripts"
          ref={scriptsInput}
          type="file"
          accept=".zip"
          onChange={(e) => setScripts(e.target.files?.[0] ?? null)}
          onBlur={() => setScriptsTouched(true)}
          className="mt-1.5 block text-sm text-slate-600 file:mr-3 file:rounded file:border-0 file:bg-indigo-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
        />
        {scriptsTouched && !scripts && (
          <p className="mt-1 text-xs text-red-800" role="alert">
            A scripts .zip is required.
          </p>
        )}
      </div>

      <button
        type="submit"
        data-testid="upload-pipeline-version-submit"
        disabled={!ready || upload.isPending}
        className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
      >
        {upload.isPending ? "Uploading" : "Upload version"}
      </button>

      {upload.error && (
        <div className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800" role="alert">
          <p className="font-medium">Nothing was sealed. Fix these first:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {(upload.error instanceof ApiError && upload.error.reasons.length
              ? upload.error.reasons
              : [
                  upload.error instanceof Error
                    ? upload.error.message
                    : "That did not work.",
                ]
            ).map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      )}
    </form>
  );
}
