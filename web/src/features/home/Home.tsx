/**
 * What you see when you arrive. Every role's home answers the same two questions in the same order: what is waiting for me, and what is
 * happening with my things.
 *
 * The roles whose work is a queue of their own (a reviewer of results, an approver of the hosts an agent may call, a data engineer, a
 * privacy auditor, an administrator) are in RoleHomes.tsx. The map of the platform's parts is not here at all: it lives on the Services
 * page, because it is the shape of the system and not the shape of anybody's work.
 *
 * Nothing here is a permission boundary. What is shown follows from what somebody is trying to do; what they may read follows from the
 * policy engine, which refuses the same way whether or not a link was on screen.
 */

import { joined } from "../../lib/joined";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useDecideRequest,
  useLeaseRequests,
  useLeases,
  useOrganisation,
  usePolicyRoles,
  useRevokeLease,
} from "../../api/queries";
import { usePeople } from "../../api/people";
import { useAwaitingConfirmation, useConfirmClassification } from "../../api/ingest";
import { CLASS_LABEL } from "../../api/types";
import { ClassBadge } from "../../components/ClassBadge";
import { VersionName } from "../../components/VersionName";
import { Failure, Loading } from "../../components/states";
import { Pagination } from "../../components/Pagination";
import { useIdentity } from "../../identity/IdentityContext";
import { notify } from "../../components/toast";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Hero, Nothing, Panel, Pill, Rows, Tile, Tiles } from "./Blocks";
import { ResearcherHome } from "./ResearcherHome";
import {
  AdministratorHome,
  ArchitectHome,
  AuditorHome,
  EngineerHome,
  LockedWaiting,
  OversightHome,
  ReviewerHome,
} from "./RoleHomes";
import { when } from "../../lib/when";

const PAGE_SIZE = 15;

/** A custodian: what is waiting for you, and what you are answerable for. */
function CustodianHome({
  custodian,
  departments,
}: {
  custodian: string;
  departments: string[];
}) {
  // Narrowed to this custodian. A queue that lists requests you cannot decide
  // is worse than an empty one: it invites you to try, and the refusal comes
  // from the policy engine after you have already committed to a decision.
  const pending = useLeaseRequests("pending", custodian);
  // Everything for this department, uncapped: the "Decided, all time" and
  // "Currently granted" stats above need the real totals, not one page of
  // them. The list itself is rendered from `decidedPage` below instead.
  const everything = useLeaseRequests(undefined, custodian, { excludePending: true });
  const [decidedPageNum, setDecidedPageNum] = useState(1);
  const decidedPage = useLeaseRequests(undefined, custodian, {
    excludePending: true,
    limit: PAGE_SIZE,
    offset: (decidedPageNum - 1) * PAGE_SIZE,
  });
  const leases = useLeases();
  const organisation = useOrganisation();
  const decide = useDecideRequest();
  const revoke = useRevokeLease();
  const people = usePeople();
  const [arrivalsShown, setArrivalsShown] = useState(100);
  const arrivals = useAwaitingConfirmation(custodian, arrivalsShown);
  const confirm = useConfirmClassification();
  // One dialog, driven by which action is pending: `window.confirm`/
  // `window.prompt` are raw browser chrome sitting inside an otherwise
  // designed screen, and it happens on exactly the two actions here that
  // cannot be undone by clicking again.
  const [pendingAction, setPendingAction] = useState<
    | { type: "revoke"; leaseId: string; principal: string; standing: boolean }
    | { type: "refuse"; requestId: string }
    | null
  >(null);

  // Which shape of grant a custodian is about to approve, per request. Kept
  // here rather than on the request itself: it is a choice being made right
  // now, not something the request carries. Defaults to "strict" -- the
  // shape every lease had before this choice existed -- so granting without
  // touching this control changes nothing about what a custodian is used to.
  const [patternByRequest, setPatternByRequest] = useState<Record<string, "strict" | "simple">>(
    {},
  );

  const waiting = pending.data?.lease_requests.length ?? 0;
  const mine = (organisation.data?.departments ?? []).filter((d) => d.approvers.some((a) => a.person_id === custodian));

  // Excluded server-side now (`exclude_pending`), so every row here is
  // already decided; still re-sorted by `decided_at`, a different ordering
  // than the server's own `created_at desc`.
  const decided = (everything.data?.lease_requests ?? [])
    .slice()
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""));
  const decidedRows = (decidedPage.data?.lease_requests ?? [])
    .slice()
    .sort((a, b) => (b.decided_at ?? "").localeCompare(a.decided_at ?? ""));

  // Whether the access a decision granted is still live. The decision and its
  // consequence are different facts: a grant from six weeks ago that has since
  // expired is not the same as one somebody holds right now.
  const leaseById = new Map((leases.data?.leases ?? []).map((l) => [l.id, l]));

  const activeGrants = decided.filter(
    (r) =>
      r.state === "approved" &&
      (r.lease_id ? leaseById.get(r.lease_id)?.active : true),
  ).length;

  return (
    <>
      {!pending.isLoading && !arrivals.isLoading && !everything.isLoading && (
        <div data-testid="custodian-stats">
          <Tiles>
            <Tile tone="indigo" icon="clock" value={waiting} name="Waiting for your decision" attention={waiting > 0} />
            <Tile tone="red" icon="alert" value={arrivals.data?.total ?? 0} name="Need your confirmation" attention={Boolean(arrivals.data?.total)} />
            <Tile tone="green" icon="check" value={activeGrants} name="Currently granted" />
            <Tile tone="blue" icon="layers" value={decided.length} name="Decided, all time" hint={departments.length > 1 ? "Your own departments only" : "Your own department only"} />
          </Tiles>
        </div>
      )}

      {/*
        Separate from access requests, and shown first. An access request is
        somebody asking to read data that already has an agreed sensitivity.
        This is the sensitivity itself being unagreed, which is the more basic
        question and the one blocking everything else about that dataset.
      */}
      {Boolean(arrivals.data?.total) && (
        <Panel padded
          title="Arrivals waiting for your confirmation"
          sub="Somebody registered these and claimed a sensitivity less restrictive than the safe default. Until you agree, each one stays at the most restrictive level, and nobody can read it as anything less. Agreeing opens it to the level claimed."
        >
          <ul className="space-y-2" data-testid="awaiting-confirmation">
            {(arrivals.data?.items ?? []).map((d) => (
              <li
                key={d.id}
                data-arrival={d.id}
                className="rounded border border-amber-300 bg-amber-50 p-4 text-sm"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium">{d.name}</span>
                  <ClassBadge value={d.declared_class} />
                </div>
                <p className="mt-1 text-slate-700">
                  Claimed by {people.label(d.declared_by)} on{" "}
                  {when(d.declared_at)}.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    data-testid={`confirm-${d.id}`}
                    disabled={confirm.isPending}
                    onClick={() =>
                      confirm.mutate(
                        { datasetId: d.id, confirmedBy: custodian },
                        { onSuccess: () => notify(`Agreed with ${d.name}'s claim.`) },
                      )
                    }
                    className="ds-btn primary"
                  >
                    {confirm.isPending ? "Agreeing" : "Agree with this"}
                  </button>
                  <span className="text-xs text-slate-500">
                    Agreeing opens it to {CLASS_LABEL[d.declared_class]}, the
                    level claimed.
                  </span>
                </div>
                {confirm.error && (
                  <p className="mt-2 text-xs text-red-800" role="alert">
                    {confirm.error instanceof Error
                      ? confirm.error.message
                      : "That did not work."}
                  </p>
                )}
              </li>
            ))}
          </ul>
          {arrivals.data && arrivals.data.total > arrivals.data.items.length && (
            <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-slate-700">
              <span data-testid="arrivals-count">
                Showing {arrivals.data.items.length} of {arrivals.data.total} waiting, oldest first.
              </span>
              {arrivalsShown < 500 && (
                <button
                  type="button"
                  data-testid="arrivals-show-more"
                  onClick={() => setArrivalsShown((n) => Math.min(n + 100, 500))}
                  className="ds-btn"
                >
                  Show 100 more
                </button>
              )}
            </div>
          )}
        </Panel>
      )}

      <LockedWaiting />

      <Panel padded
        title="Waiting for your decision"
        sub={`Requests to read data owned by ${joined(departments) || "your department"}.`}
      >
        {pending.isLoading ? (
          <Loading what="requests" />
        ) : pending.error ? (
          <Failure error={pending.error} what="requests" />
        ) : !waiting ? (
          <p className="rounded border border-dashed border-slate-300 p-6 text-sm text-slate-500">
            Nothing is waiting. Requests appear here when somebody asks to read
            data you are answerable for.
          </p>
        ) : (
          <ul className="space-y-2" data-testid="pending-requests">
            {pending.data!.lease_requests.map((r) => (
              <li
                key={r.id}
                data-request={r.id}
                className="rounded border border-amber-300 bg-amber-50 p-4 text-sm"
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium">
                    {/*
                      A workload never asks for itself. When `requested_by` is
                      set, the human who made the ask is named alongside the
                      workload that will actually read, so a custodian is
                      never left wondering why a service account filed a
                      request on its own.
                    */}
                    {r.requested_by
                      ? `${people.label(r.requested_by)} asked for ${people.name(r.principal)} to read`
                      : `${people.name(r.principal)} wants to read`}
                  </span>
                  <VersionName
                    versionId={r.dataset_version_id}
                    datasetName={r.dataset_name}
                    version={r.version}
                    currentClass={r.current_class}
                  />
                </div>
                <p className="mt-1 text-slate-700">{r.justification}</p>
                {r.gives && (
                  <p data-testid={`request-gives-${r.id}`} className="mt-1 rounded border border-amber-300 bg-white p-2 text-xs text-slate-800">
                    {r.gives}
                  </p>
                )}
                <p className="mt-1 text-xs text-slate-500">
                  Asked on {when(r.created_at)}. For {r.purpose}
                  {r.standing
                    ? ", until revoked. Nothing expires this on its own."
                    : `, for ${r.requested_ttl_hours} hours.`}
                </p>

                {/*
                  Raw, unreviewed data always asks again per purpose -- the
                  database itself refuses a simple lease against it, so
                  offering the choice here would only invite a refusal after
                  the fact. Everything else is the custodian's own call.
                */}
                {r.current_class !== "RAW" && (
                  <fieldset className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-600">
                    <legend className="sr-only">
                      How much of this access should this grant cover?
                    </legend>
                    <label className="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name={`pattern-${r.id}`}
                        checked={(patternByRequest[r.id] ?? "strict") === "strict"}
                        onChange={() =>
                          setPatternByRequest((prev) => ({ ...prev, [r.id]: "strict" }))
                        }
                      />
                      This purpose only
                    </label>
                    <label className="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name={`pattern-${r.id}`}
                        checked={patternByRequest[r.id] === "simple"}
                        onChange={() =>
                          setPatternByRequest((prev) => ({ ...prev, [r.id]: "simple" }))
                        }
                      />
                      Any purpose, while this lasts
                    </label>
                  </fieldset>
                )}

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    data-testid={`approve-${r.id}`}
                    disabled={decide.isPending}
                    onClick={() =>
                      decide.mutate(
                        { id: r.id, action: "approve", pattern: patternByRequest[r.id] ?? "strict" },
                        { onSuccess: () => notify(`Access granted to ${people.name(r.principal)}.`) },
                      )
                    }
                    className="ds-btn primary"
                  >
                    {decide.isPending ? "Granting" : "Grant access"}
                  </button>
                  {/*
                    Refusing sits beside granting rather than behind a menu. A
                    queue that offers only Approve is a queue that gets approved.
                  */}
                  <button
                    type="button"
                    data-testid={`refuse-${r.id}`}
                    disabled={decide.isPending}
                    onClick={() => setPendingAction({ type: "refuse", requestId: r.id })}
                    className="ds-btn"
                  >
                    Refuse
                  </button>
                  <span className="text-xs text-slate-500">
                    {r.standing ? (
                      <>
                        Granting this lasts until somebody revokes it. Only a
                        workload can be given that; nothing expires it on its
                        own.
                      </>
                    ) : (
                      <>
                        Granting gives {people.label(r.principal)}{" "}
                        {r.requested_ttl_hours} hours
                        {patternByRequest[r.id] === "simple"
                          ? ", for any purpose while it lasts"
                          : ", for this purpose only"}
                        , and it ends by itself.
                      </>
                    )}
                  </span>
                </div>

                {decide.error && (
                  <p className="mt-2 text-xs text-red-800" role="alert">
                    {decide.error instanceof Error
                      ? decide.error.message
                      : "That did not work."}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="What you have decided" sub="Every request you have already answered, most recent first. Refusals included.">
        {decidedPage.isLoading ? (
          <Nothing>Loading</Nothing>
        ) : decidedPage.error ? (
          <Nothing>Your decisions could not be loaded.</Nothing>
        ) : !decidedRows.length ? (
          <Nothing>
            You have not decided anything yet. Once you grant or refuse a request it stays here, so you can see what you allowed and for how long.
          </Nothing>
        ) : (
          <>
            <div data-testid="decided-requests">
              <Rows
                head={["Outcome", "Request", "Decided", "What it allowed"]}
                rows={decidedRows.map((r) => {
                  const lease = r.lease_id ? leaseById.get(r.lease_id) : undefined;
                  const granted = r.state === "approved";
                  return {
                    key: r.id,
                    attrs: { "data-decided": r.id, "data-outcome": r.state },
                    cells: [
                      granted ? <Pill tone="ok">Granted</Pill> : <Pill tone="stop">Refused</Pill>,
                      <>
                        <span>
                          {r.requested_by
                            ? `${people.label(r.requested_by)} asked for ${people.name(r.principal)} to read`
                            : `${people.name(r.principal)} to read`}
                        </span>{" "}
                        <VersionName versionId={r.dataset_version_id} datasetName={r.dataset_name} version={r.version} currentClass={r.current_class} />
                        <span className="sub">For {r.purpose}</span>
                      </>,
                      <>
                        {r.decided_at ? when(r.decided_at) : ""}
                        {/*
                          Named when it was not you. Two custodians can hold the same department over time, and a list headed "what you have
                          decided" that quietly includes somebody else's decisions is the kind of small untruth this console keeps removing.
                        */}
                        {r.decided_by && r.decided_by !== custodian && <span className="sub">by {people.label(r.decided_by)}</span>}
                      </>,
                      granted ? (
                        <>
                          <span>
                            {lease
                              ? lease.active
                                ? lease.standing
                                  ? "Standing: open until somebody revokes it."
                                  : `Still open until ${when(lease.expires_at!)}.`
                                : lease.revoked
                                  ? "Withdrawn before it ran out."
                                  : "Has since run out."
                              : r.standing
                                ? "Granted, standing until revoked."
                                : `Granted for ${r.requested_ttl_hours} hours.`}
                          </span>
                          {lease?.pattern === "simple" && (
                            <span data-testid={`lease-pattern-${lease.id}`} className="sub">
                              Covers any purpose
                            </span>
                          )}
                          {/*
                            Shown for any still-open lease, not only a standing one: a bounded grant given by mistake should not have to be
                            waited out either. Standing is where this matters most, because nothing else ever ends one.
                          */}
                          {lease?.active && (
                            <button
                              type="button"
                              data-testid={`revoke-${lease.id}`}
                              disabled={revoke.isPending}
                              onClick={() =>
                                setPendingAction({
                                  type: "revoke",
                                  leaseId: lease.id,
                                  principal: lease.principal,
                                  standing: lease.standing,
                                })
                              }
                              className="btn btn-link btn-link-danger"
                              style={{ display: "block", marginTop: 4, fontSize: 12.5 }}
                            >
                              Revoke
                            </button>
                          )}
                          {revoke.error && revoke.variables === lease?.id && (
                            <span className="sub" role="alert" style={{ color: "var(--red)" }}>
                              {revoke.error instanceof Error ? revoke.error.message : "That could not be revoked."}
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="sub" style={{ display: "inline" }}>Nothing was granted.</span>
                      ),
                    ],
                  };
                })}
              />
            </div>
            <div className="ds-pad" style={{ paddingTop: 0 }}>
              <Pagination page={decidedPageNum} pageSize={PAGE_SIZE} total={decidedPage.data?.total ?? 0} onPageChange={setDecidedPageNum} />
            </div>
          </>
        )}
      </Panel>

      <Panel padded
        title="What you are answerable for"
        sub="Data owned by your department. You decide who reads it."
      >
        {organisation.isLoading ? (
          <Loading what="your department" />
        ) : (
          <p className="text-sm text-slate-600">
            {/*
              Your department's count, not the platform's. This said "76
              datasets are held in total" under a heading claiming they were
              yours, which is a lie by juxtaposition rather than by statement.
            */}
            {mine.length
              ? mine
                  .map((d) => `${d.datasets} ${d.datasets === 1 ? "dataset belongs" : "datasets belong"} to ${d.name}`)
                  .join(", ") + "."
              : "No datasets have been assigned to a department you approve for yet."}{" "}
            <Link to="/datasets" className="text-sky-700 underline">
              Look through them
            </Link>
            .
          </p>
        )}
      </Panel>

      <ConfirmDialog
        open={pendingAction !== null}
        title={
          pendingAction?.type === "revoke"
            ? "Revoke access?"
            : "Refuse this request?"
        }
        description={
          pendingAction?.type === "revoke"
            ? pendingAction.standing
              ? `Revoke ${people.name(pendingAction.principal)}'s standing access? This cannot be undone; a fresh request would be needed to grant it again.`
              : `Revoke ${people.name(pendingAction.principal)}'s access now, ahead of when it would expire on its own?`
            : "The person who asked will see the reason you give below."
        }
        confirmLabel={pendingAction?.type === "revoke" ? "Revoke" : "Refuse"}
        destructive={pendingAction?.type === "revoke"}
        reasonLabel={
          pendingAction?.type === "refuse" ? "Why are you refusing?" : undefined
        }
        onCancel={() => setPendingAction(null)}
        onConfirm={(reason) => {
          if (pendingAction?.type === "revoke") {
            revoke.mutate(pendingAction.leaseId, {
              onSuccess: () =>
                notify(`${people.name(pendingAction.principal)}'s access revoked.`),
            });
          } else if (pendingAction?.type === "refuse") {
            decide.mutate(
              { id: pendingAction.requestId, action: "reject", reason },
              // "success" tone deliberately: refusing is the custodian's
              // own correct decision, completed without a hitch, not a
              // failure of anything.
              { onSuccess: () => notify("Request refused.") },
            );
          }
          setPendingAction(null);
        }}
      />
    </>
  );
}

export function Home() {
  const { principal } = useIdentity();
  const policy = usePolicyRoles();

  if (!principal) return null;

  const roles = principal.roles;
  const mayApprove =
    policy.data &&
    roles.some((r) => policy.data!.approver_roles.includes(r));

  return (
    <>
      <Hero
        name={principal.label}
        where={joined(principal.approver_of) || undefined}
        sub={principal.landing}
        action={
          roles.includes("platform_admin") ? (
            <Link to="/services" className="ds-btn">
              Open the services
            </Link>
          ) : undefined
        }
      />

      {mayApprove && (
        <CustodianHome
          custodian={principal.id}
          departments={principal.approver_of}
        />
      )}
      {roles.includes("notebook_explore") && (
        <ResearcherHome principal={principal.id} />
      )}
      {roles.includes("deid_reviewer") && <ReviewerHome />}
      {roles.includes("network_architect") && <ArchitectHome />}
      {roles.includes("pipeline_operator") && <EngineerHome />}
      {roles.includes("privacy_auditor") && <AuditorHome />}
      {roles.includes("dpo") && <OversightHome />}
      {roles.includes("platform_admin") && <AdministratorHome />}
    </>
  );
}
