/**
 * Registering an agent: a name, an owning department, and a purpose.
 * Nothing about its code yet, since that comes later, as a version, which is
 * why there is no upload step here the way `RegisterDataset.tsx` has one.
 * A version is registered afterward, on the agent's own detail page, one of
 * two ways: `python -m agent.register_version` from its own checkout for
 * code the platform only describes, or the upload form
 * (`UploadAgentVersion.tsx`) for code the platform will actually execute,
 * sandboxed.
 *
 * Registering also creates the runtime identity this agent will act as,
 * automatically: a dedicated workload account, named after this agent, that
 * nothing else runs as. There is nothing to choose here, so the form does
 * not ask.
 */

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIdentity } from "../../identity/IdentityContext";
import { useOrganisation } from "../../api/queries";
import { useRegisterAgent } from "../../api/agents";
import { Section } from "../../components/states";

export function RegisterAgent() {
  const navigate = useNavigate();
  const { principal } = useIdentity();
  const organisation = useOrganisation();
  const register = useRegisterAgent();

  const [name, setName] = useState("");
  const [departmentId, setDepartmentId] = useState("");
  const [purpose, setPurpose] = useState("");

  // Shown on blur, not every keystroke: the submit button is disabled until
  // the form is already valid, so a click never fires to hang a "show
  // errors now" trigger off of. Blur is the substitute.
  const [nameTouched, setNameTouched] = useState(false);
  const [departmentTouched, setDepartmentTouched] = useState(false);
  const [purposeTouched, setPurposeTouched] = useState(false);

  const ready =
    name.trim().length > 0 && departmentId.length > 0 &&
    purpose.trim().length > 0;

  return (
    <Section
      level="page"
      title="Register an agent"
      description="Names an owner and a purpose. No version exists yet, so nothing can run as this agent until one is registered from its own codebase."
    >
      <form
        data-testid="register-agent-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (!ready || !principal) return;
          register.mutate(
            {
              name: name.trim(),
              department_id: departmentId,
              registered_by: principal.id,
              purpose: purpose.trim(),
            },
            { onSuccess: (result) => navigate(`/agents/${result.id}`) },
          );
        }}
        className="space-y-4 rounded border border-slate-200 bg-white p-4"
      >
        <div>
          <label htmlFor="agent-name" className="block text-sm font-medium text-slate-700">
            Name
          </label>
          <input
            id="agent-name"
            data-testid="agent-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => setNameTouched(true)}
            placeholder="radiology-intake-triage"
            className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
          />
          {nameTouched && name.trim().length === 0 && (
            <p className="mt-1 text-xs text-red-800" role="alert">
              A name is required.
            </p>
          )}
        </div>

        <div>
          <label htmlFor="agent-department" className="block text-sm font-medium text-slate-700">
            Owned by
          </label>
          <select
            id="agent-department"
            data-testid="agent-department"
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

        <div>
          <label htmlFor="agent-purpose" className="block text-sm font-medium text-slate-700">
            Purpose
          </label>
          <p className="mt-0.5 text-xs text-slate-500">
            What it does, in one sentence. Recorded against every version.
          </p>
          <input
            id="agent-purpose"
            data-testid="agent-purpose"
            value={purpose}
            onChange={(e) => setPurpose(e.target.value)}
            onBlur={() => setPurposeTouched(true)}
            placeholder="Ranks radiology intake documents for human review"
            className="mt-1.5 w-full max-w-md rounded border border-slate-300 px-3 py-1.5 text-sm"
          />
          {purposeTouched && purpose.trim().length === 0 && (
            <p className="mt-1 text-xs text-red-800" role="alert">
              A purpose is required.
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            data-testid="register-agent-submit"
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
