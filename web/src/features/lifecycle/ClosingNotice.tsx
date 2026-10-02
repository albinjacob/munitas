/**
 * What somebody sees when their organisation is closing and they can do nothing.
 *
 * Nothing else in the console is reachable for them, because the platform refuses
 * every other request, so showing the usual navigation would offer links that all
 * fail. This says why, when it ends, and the one thing that may still be asked of
 * a person: a temporary custodian of a legal hold acknowledging that they have
 * read the notice.
 */

import { useNavigate } from "react-router-dom";
import { useAcknowledgeHold, useClosing, useHolds } from "../../api/lifecycle";
import { Failure, Loading } from "../../components/states";
import { notify } from "../../components/toast";
import { useIdentity } from "../../identity/IdentityContext";
import { PhaseBadge, Standing } from "./Closing";
import { bare, when } from "./dates";

export function ClosingNotice() {
  const { closed, clear } = useIdentity();
  const navigate = useNavigate();
  const status = useClosing(undefined, Boolean(closed));
  const holds = useHolds(Boolean(closed));

  if (!closed) return null;

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-3xl px-4 py-10">
        <div className="mb-6 text-base font-semibold">Munitas</div>
        <h1 className="text-xl font-semibold">{closed.tenant_id} is closing</h1>
        <p className="mt-1 text-sm text-slate-600">
          You are signed in as <span data-testid="current-persona">{closed.label}</span>. Nothing can be done in this organisation any more, so none of the
          usual screens are available.
        </p>

        <section data-testid="closing-notice" className="mt-6 rounded border border-slate-200 bg-white p-4 text-sm">
          {status.isLoading ? (
            <Loading what="the closing" />
          ) : status.error ? (
            <Failure error={status.error} what="where the closing stands" />
          ) : (
            <>
              <div className="mb-2 flex items-center gap-2">
                <PhaseBadge phase={status.data!.phase} />
                {status.data!.retire_reason && (
                  <span className="text-slate-500">Reason given: {status.data!.retire_reason}</span>
                )}
              </div>
              <Standing status={status.data!} />
            </>
          )}
        </section>

        {(holds.data?.holds ?? []).filter((h) => h.status === "active").map((h) => (
          <Acknowledge key={h.id} holdId={h.id} number={h.matter_number} name={h.matter_name}
            authority={h.issuing_authority} preserve={h.preserve} acknowledgedAt={h.custodian_acknowledged_at} />
        ))}

        <button
          type="button"
          data-testid="switch-persona"
          onClick={async () => {
            await clear();
            navigate("/auth/login");
          }}
          className="mt-6 text-sm text-sky-700 underline"
        >
          Sign out
        </button>
      </div>
    </div>
  );
}

function Acknowledge(props: {
  holdId: string; number: string; name: string; authority: string; preserve: string; acknowledgedAt: string | null;
}) {
  const ack = useAcknowledgeHold(props.holdId);
  return (
    <section data-testid="hold-for-custodian" className="mt-4 rounded border border-slate-200 bg-white p-4 text-sm">
      <h2 className="font-semibold">A legal hold names you as custodian</h2>
      <p className="mt-1 text-slate-600">
        {props.authority} has asked that this organisation&apos;s records be kept for matter {props.number},{" "}
        {props.name}. What must be kept: {bare(props.preserve)}. While the hold stands, nothing in the
        organisation is deleted.
      </p>
      {props.acknowledgedAt ? (
        <p className="mt-2 text-green-800">You acknowledged this hold on {when(props.acknowledgedAt)}.</p>
      ) : (
        <button
          type="button"
          data-testid="hold-acknowledge"
          disabled={ack.isPending}
          onClick={() => ack.mutate(undefined, { onSuccess: () => notify("You have acknowledged the hold.") })}
          className="mt-3 rounded bg-slate-800 px-3 py-1.5 text-sm font-medium text-white disabled:bg-slate-300"
        >
          {ack.isPending ? "Recording" : "I have read it and will answer for these records"}
        </button>
      )}
      {ack.error && (
        <div className="mt-2">
          <Failure error={ack.error} what="this hold" verb="acknowledge" />
        </div>
      )}
    </section>
  );
}
