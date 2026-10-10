/**
 * The person's side of a notebook signing in: type the code, be signed in, approve. Hydra (the service that issues the notebook's pass) sends the browser to these
 * pages one after the other, and each page asks the platform for the next address and goes there.
 *
 *   /notebook-sign-in/code      type the code the notebook is showing
 *   /notebook-sign-in/login     nothing to do: the console session is the sign-in, so this goes straight on
 *   /notebook-sign-in/consent   what the notebook will and will not be able to do; Approve or Refuse
 *   /notebook-sign-in/done      the notebook is signed in
 *
 * A person who is not signed in to the console is sent to the sign-in page first and brought back here (App.tsx keeps where they were going).
 */

import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAcceptCode, useConsentDetails, useDecideSignIn, useSignInStep } from "../../api/notebookSignIn";
import { Failure, Loading, Section } from "../../components/states";
import { when } from "../../lib/when";
import { Reasons } from "../locking/LockedColumns";

export function NotebookCode() {
  const [params] = useSearchParams();
  const challenge = params.get("device_challenge");
  const [code, setCode] = useState("");
  const accept = useAcceptCode();
  if (!challenge) {
    return (
      <Section level="page" title="Sign a notebook in">
        <p data-testid="notebook-code-missing" className="text-sm text-slate-800">
          This page opens from the address the notebook shows. Run the sign-in in the notebook again and open the address it prints.
        </p>
      </Section>
    );
  }
  return (
    <Section level="page" title="Sign a notebook in" description="A notebook is asking to sign in as you. Type the code the notebook is showing, so that only the notebook in front of you is signed in.">
      <form
        data-testid="notebook-code-form"
        className="max-w-md space-y-3 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          accept.mutate({ device_challenge: challenge, user_code: code.trim() }, { onSuccess: (r) => window.location.assign(r.redirect_to) });
        }}
      >
        <label className="block">
          <span className="font-medium text-slate-800">The code in the notebook</span>
          <input
            data-testid="notebook-code-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="off"
            autoFocus
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2 font-mono text-lg uppercase tracking-widest"
          />
        </label>
        <button
          type="submit"
          data-testid="notebook-code-submit"
          disabled={accept.isPending || code.trim().length < 4}
          className="rounded bg-indigo-500 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-800 disabled:opacity-50"
        >
          {accept.isPending ? "Checking" : "Continue"}
        </button>
        <p className="text-xs text-slate-600">If you did not start a sign-in in a notebook just now, close this page: nothing has been given to anybody.</p>
        {accept.error && (
          <div role="alert" data-testid="notebook-code-error" className="text-xs text-red-800">
            <Reasons error={accept.error} fallback="That code was not accepted." />
          </div>
        )}
      </form>
    </Section>
  );
}

export function NotebookLogin() {
  const [params] = useSearchParams();
  const challenge = params.get("login_challenge");
  const step = useSignInStep();
  const started = useRef(false);
  useEffect(() => {
    if (!challenge || started.current) return;
    started.current = true;
    // Not `mutate(..., { onSuccess })`: while developing, React runs this effect, tears the page down and sets it up again, and a callback tied to the page that
    // was torn down never fires. A promise does not care which copy of the page asked.
    step.mutateAsync(challenge).then((r) => window.location.assign(r.redirect_to)).catch(() => {});
  }, [challenge, step]);
  return (
    <Section level="page" title="Signing in">
      {step.error ? (
        <div role="alert" data-testid="notebook-login-error" className="text-sm text-red-800">
          <Reasons error={step.error} fallback="The sign-in did not go through." />
        </div>
      ) : (
        <p className="text-sm text-slate-700" role="status">
          Signing the notebook in as you.
        </p>
      )}
    </Section>
  );
}

export function NotebookConsent() {
  const [params] = useSearchParams();
  const challenge = params.get("consent_challenge");
  const details = useConsentDetails(challenge);
  const decide = useDecideSignIn();
  const [refused, setRefused] = useState(false);
  if (!challenge) return <Failure error={new Error("This page opens from the sign-in. Start again from the notebook.")} what="the approval" />;
  if (details.isLoading) return <Loading what="the approval" />;
  if (details.error) return <Failure error={details.error} what="the approval" />;
  const d = details.data!;
  if (refused) {
    return (
      <Section level="page" title="Refused">
        <p data-testid="notebook-refused" className="text-sm text-slate-800">
          You refused the sign-in. The notebook has been told, and nothing was given to it. You can close this page.
        </p>
      </Section>
    );
  }
  return (
    <Section level="page" title="Approve this notebook?" description="Check that this is the notebook you just started.">
      <div data-testid="notebook-consent" className="max-w-2xl space-y-4 text-sm">
        <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1">
          <dt className="text-slate-500">The notebook calls itself</dt>
          <dd data-testid="notebook-consent-label" className="font-medium">{d.label}</dd>
          <dt className="text-slate-500">Asked from</dt>
          <dd>{d.address ?? "an address that was not recorded"}</dd>
          <dt className="text-slate-500">Program</dt>
          <dd className="break-all">{d.program || "not recorded"}</dd>
          <dt className="text-slate-500">Asked</dt>
          <dd>{when(d.asked_at)}</dd>
          <dt className="text-slate-500">Lasts at most</dt>
          <dd>
            {d.hours} {d.hours === 1 ? "hour" : "hours"}. You can end it sooner under{" "}
            <Link className="text-sky-700 underline" to="/notebook-sign-ins">
              Your notebooks
            </Link>
            .
          </dd>
        </dl>
        <div>
          <p className="font-medium text-slate-900">Once approved, the notebook can, as you:</p>
          <ul className="mt-1 list-disc pl-5 text-slate-800">
            {d.may.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="font-medium text-slate-900">It cannot:</p>
          <ul className="mt-1 list-disc pl-5 text-slate-800">
            {d.may_not.map((m) => (
              <li key={m}>{m}</li>
            ))}
          </ul>
        </div>
        <div className="flex gap-3">
          <button
            type="button"
            data-testid="notebook-approve"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ consent_challenge: challenge, approve: true }, { onSuccess: (r) => window.location.assign(r.redirect_to) })}
            className="rounded bg-indigo-500 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-800 disabled:opacity-50"
          >
            Approve
          </button>
          <button
            type="button"
            data-testid="notebook-refuse"
            disabled={decide.isPending}
            onClick={() => decide.mutate({ consent_challenge: challenge, approve: false }, { onSuccess: () => setRefused(true) })}
            className="rounded border border-slate-700 px-3 py-2 text-sm font-medium text-slate-900 disabled:opacity-50"
          >
            Refuse
          </button>
        </div>
        {decide.error && (
          <div role="alert" className="text-xs text-red-800">
            <Reasons error={decide.error} fallback="That was not recorded." />
          </div>
        )}
      </div>
    </Section>
  );
}

export function NotebookDone() {
  return (
    <Section level="page" title="The notebook is signed in">
      <p data-testid="notebook-done" className="max-w-xl text-sm text-slate-800">
        The notebook is signed in as you and will carry on by itself. You can close this page. To end it sooner, open{" "}
        <Link className="text-sky-700 underline" to="/notebook-sign-ins">
          Your notebooks
        </Link>
        .
      </p>
    </Section>
  );
}
