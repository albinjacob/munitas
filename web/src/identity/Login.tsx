/**
 * The front door. The only one now.
 *
 * Success here does not render anything of its own: it calls
 * `refreshSession()` (IdentityContext.tsx) so the seam re-checks
 * `/auth/whoami`, then navigates to `/`.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useIdentity } from "./IdentityContext";
import { LoginFailed, startLoginFlow, submitLogin, type LoginFlow } from "./kratos";

export function Login() {
  const { refreshSession } = useIdentity();
  const navigate = useNavigate();

  const [flow, setFlow] = useState<LoginFlow | null>(null);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    startLoginFlow()
      .then(setFlow)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!flow) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitLogin(flow, identifier, password);
      refreshSession();
      navigate("/");
    } catch (e) {
      setError(e instanceof LoginFailed ? e.message : (e as Error).message);
      setSubmitting(false);
      // A failed attempt invalidates the flow's csrf token; fetch a fresh
      // one rather than letting a second attempt fail on that instead of on
      // the password.
      startLoginFlow().then(setFlow).catch(() => {});
    }
  }

  return (
    <div className="mx-auto max-w-md px-4 py-10">
      <h1 className="text-2xl font-semibold">Sign in</h1>
      <p className="mt-2 text-sm text-slate-600">
        Munitas holds recordings and the record of who may read them. What
        you can do here depends on who you are, and you will see only what
        your organisation owns.
      </p>

      <form onSubmit={onSubmit} className="mt-8 space-y-4" data-testid="login-form">
        <div>
          <label className="block text-sm font-medium text-slate-700">Email</label>
          <input
            type="text"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            disabled={!flow || submitting}
            data-testid="login-identifier"
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={!flow || submitting}
            data-testid="login-password"
            className="mt-1 w-full rounded border border-slate-300 px-3 py-2"
          />
        </div>
        {error && (
          <p className="text-sm text-red-700" role="alert" data-testid="login-error">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={!flow || submitting}
          data-testid="login-submit"
          className="w-full rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}
