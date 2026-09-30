/**
 * Registering a pipeline: a name and an owning department. Nothing about
 * its steps yet, since that comes later, as a version, on the pipeline's
 * own detail page (RegisterPipeline.tsx has no upload step, the same
 * reasoning RegisterAgent.tsx already gives for agents).
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIdentity } from "../../identity/IdentityContext";
import { useOrganisation } from "../../api/queries";
import { useRegisterPipeline } from "../../api/dag_pipelines";
import { Section } from "../../components/states";

export function RegisterPipeline() {
  const navigate = useNavigate();
  const { principal } = useIdentity();
  const organisation = useOrganisation();
  const register = useRegisterPipeline();

  const [name, setName] = useState("");
  const [departmentId, setDepartmentId] = useState("");

  // Shown on blur, not every keystroke: the submit button is disabled until
  // the form is already valid, so a click never fires to hang a "show
  // errors now" trigger off of. Blur is the substitute.
  const [nameTouched, setNameTouched] = useState(false);
  const [departmentTouched, setDepartmentTouched] = useState(false);

  const ready = name.trim().length > 0 && departmentId.length > 0;

  return (
    <Section
      level="page"
      title="Register a pipeline"
      description="Names an owner. No version exists yet, so nothing can run until a DAG config and its scripts are uploaded on this pipeline's own page."
    >
      <form
        data-testid="register-pipeline-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!ready || !principal) return;
          register.mutate(
            { name: name.trim(), departmentId, registeredBy: principal.id },
            { onSuccess: (result) => navigate(`/pipelines/${result.id}`) },
          );
        }}
        className="space-y-4 rounded border border-slate-200 bg-white p-4"
      >
        <div>
          <label htmlFor="pipeline-name" className="block text-sm font-medium text-slate-700">
            Name
          </label>
          <input
            id="pipeline-name"
            data-testid="pipeline-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setNameTouched(true)}
            placeholder="audio-qc-and-annotate"
            className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
          />
          {nameTouched && name.trim().length === 0 && (
            <p className="mt-1 text-xs text-red-800" role="alert">
              A name is required.
            </p>
          )}
        </div>

        <div>
          <label htmlFor="pipeline-department" className="block text-sm font-medium text-slate-700">
            Owned by
          </label>
          <select
            id="pipeline-department"
            data-testid="pipeline-department"
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

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            data-testid="register-pipeline-submit"
            disabled={!ready || register.isPending}
            className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
          >
            {register.isPending ? "Registering" : "Register"}
          </button>
        </div>

        {register.error && (
          <p className="text-xs text-red-800" role="alert">
            {register.error instanceof Error
              ? register.error.message
              : "That did not work."}
          </p>
        )}
      </form>
    </Section>
  );
}
