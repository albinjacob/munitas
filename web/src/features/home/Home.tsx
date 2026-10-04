/**
 * What you see when you arrive, which depends on why you came.
 *
 * The previous version opened with a map of ten services, naming the policy
 * engine, the object store and the telemetry collector. That is the shape of
 * the system rather than the shape of anybody's work. A custodian deciding
 * whether to release cardiology recordings has no use for the fact that spans
 * are stamped with a visibility class.
 *
 * So the map is now one section, shown to the people who run the platform. The
 * others get the thing they came for.
 *
 * Nothing here is a permission boundary. What is shown follows from what
 * somebody is trying to do; what they may read follows from the policy engine,
 * which refuses the same way whether or not a link was on screen.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import {
  useDecideRequest,
  useLeaseRequests,
  useLeases,
  useOrganisation,
  usePolicyRoles,
  useRevokeLease,
  useSummary,
} from "../../api/queries";
import { usePeople } from "../../api/people";
import { useAwaitingConfirmation, useConfirmClassification } from "../../api/ingest";
import { CLASS_LABEL } from "../../api/types";
import { ClassBadge } from "../../components/ClassBadge";
import { VersionName } from "../../components/VersionName";
import { Failure, Loading, Section } from "../../components/states";
import { Pagination } from "../../components/Pagination";
import { useIdentity } from "../../identity/IdentityContext";
import { notify } from "../../components/toast";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { COMPONENTS } from "./components";

const PAGE_SIZE = 15;

function Stat({
  label,
  value,
  hint,
  tone = "normal",
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: "normal" | "warn";
}) {
  return (
    <div
      className={`rounded border p-4 ${
        tone === "warn"
          ? "border-amber-300 bg-amber-50"
          : "border-slate-200 bg-white"
      }`}
    >
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="mt-1 text-sm font-medium text-slate-700">{label}</div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

/** A custodian: what is waiting for you, and what you are answerable for. */
function CustodianHome({
  custodian,
  department,
}: {
  custodian: string;
  department: string | null;
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
  const mine = organisation.data?.departments.find((d) => d.custodian === custodian);

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
        <Section
          title="At a glance"
          description="Counted for your own department only."
        >
          <div
            data-testid="custodian-stats"
            className="grid grid-cols-2 gap-3 md:grid-cols-4"
          >
            <Stat
              label="Waiting for your decision"
              value={waiting}
              tone={waiting > 0 ? "warn" : "normal"}
            />
            <Stat
              label="Needs your confirmation"
              value={arrivals.data?.total ?? 0}
              tone={arrivals.data?.total ? "warn" : "normal"}
            />
            <Stat label="Currently granted" value={activeGrants} />
            <Stat label="Decided, all time" value={decided.length} />
          </div>
        </Section>
      )}

      {/*
        Separate from access requests, and shown first. An access request is
        somebody asking to read data that already has an agreed sensitivity.
        This is the sensitivity itself being unagreed, which is the more basic
        question and the one blocking everything else about that dataset.
      */}
      {Boolean(arrivals.data?.total) && (
        <Section
          title="Arrivals waiting for your confirmation"
          description="Somebody registered these and claimed a sensitivity less restrictive than the safe default. Nothing above that claim can be granted until you agree with it."
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
                  {new Date(d.declared_at).toLocaleDateString()}.
                </p>
                {d.declared_by === d.custodian && (
                  <p className="mt-1 text-xs text-slate-600">
                    The custodian of {d.department_name ?? "the owning department"} made this claim, so another data custodian confirms it.
                  </p>
                )}
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
                    className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
                  >
                    {confirm.isPending ? "Agreeing" : "Agree with this"}
                  </button>
                  <span className="text-xs text-slate-500">
                    Confirms {CLASS_LABEL[d.declared_class]} is right. It does
                    not grant anybody access.
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
                  className="rounded border border-slate-300 px-3 py-1 text-sm font-medium hover:bg-slate-100"
                >
                  Show 100 more
                </button>
              )}
            </div>
          )}
        </Section>
      )}

      <Section
        title="Waiting for your decision"
        description={`Requests to read data owned by ${department ?? "your department"}.`}
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
                <p className="mt-1 text-xs text-slate-500">
                  For {r.purpose}
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
                    className="rounded bg-indigo-500 hover:bg-indigo-800 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-50"
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
                    className="rounded border border-slate-400 px-3 py-1.5 text-sm text-slate-700 disabled:opacity-50"
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
      </Section>

      <Section
        title="What you have decided"
        description="Every request you have already answered, most recent first. Refusals included."
      >
        {decidedPage.isLoading ? (
          <Loading what="your decisions" />
        ) : decidedPage.error ? (
          <Failure error={decidedPage.error} what="your decisions" />
        ) : !decidedRows.length ? (
          <p className="rounded border border-dashed border-slate-300 p-6 text-sm text-slate-500">
            You have not decided anything yet. Once you grant or refuse a
            request it stays here, so you can see what you allowed and for how
            long.
          </p>
        ) : (
          <>
          <ul className="space-y-2" data-testid="decided-requests">
            {decidedRows.map((r) => {
              const lease = r.lease_id ? leaseById.get(r.lease_id) : undefined;
              const granted = r.state === "approved";
              return (
                <li
                  key={r.id}
                  data-decided={r.id}
                  data-outcome={r.state}
                  className="rounded border border-slate-200 bg-white p-4 text-sm"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        granted
                          ? "bg-teal-100 text-teal-900"
                          : "bg-slate-200 text-slate-700"
                      }`}
                    >
                      {granted ? "Granted" : "Refused"}
                    </span>
                    <span className="font-medium">
                      {r.requested_by
                        ? `${people.label(r.requested_by)} asked for ${people.name(r.principal)} to read`
                        : `${people.name(r.principal)} to read`}
                    </span>
                    <VersionName
                      versionId={r.dataset_version_id}
                      datasetName={r.dataset_name}
                      version={r.version}
                      currentClass={r.current_class}
                    />
                    {/*
                      Named when it was not you. Two custodians can hold the
                      same department over time, and a list headed "what you
                      have decided" that quietly includes somebody else's
                      decisions is the kind of small untruth this console keeps
                      removing.
                    */}
                    {r.decided_by && r.decided_by !== custodian && (
                      <span className="text-xs text-slate-500">
                        decided by {people.label(r.decided_by)}
                      </span>
                    )}
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    For {r.purpose}
                    {r.decided_at && `, on ${new Date(r.decided_at).toLocaleDateString()}`}
                    .
                  </p>
                  {granted && (
                    <p className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span>
                        {lease
                          ? lease.active
                            ? lease.standing
                              ? "Standing: open until somebody revokes it."
                              : `Still open until ${new Date(lease.expires_at!).toLocaleString()}.`
                            : lease.revoked
                              ? "Withdrawn before it ran out."
                              : "Has since run out."
                          : r.standing
                            ? "Granted, standing until revoked."
                            : `Granted for ${r.requested_ttl_hours} hours.`}
                      </span>
                      {lease?.pattern === "simple" && (
                        <span
                          data-testid={`lease-pattern-${lease.id}`}
                          className="rounded bg-indigo-50 px-1.5 py-0.5 text-indigo-800"
                        >
                          covers any purpose
                        </span>
                      )}
                      {/*
                        Shown for any still-open lease, not only a standing
                        one: a bounded grant given by mistake should not have
                        to be waited out either. Standing is where this
                        matters most, because nothing else ever ends one.
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
                          className="text-red-700 underline hover:text-red-900 disabled:opacity-50"
                        >
                          Revoke
                        </button>
                      )}
                    </p>
                  )}
                  {revoke.error && revoke.variables === lease?.id && (
                    <p className="mt-1 text-xs text-red-800" role="alert">
                      {revoke.error instanceof Error
                        ? revoke.error.message
                        : "That could not be revoked."}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          <Pagination
            page={decidedPageNum}
            pageSize={PAGE_SIZE}
            total={decidedPage.data?.total ?? 0}
            onPageChange={setDecidedPageNum}
          />
          </>
        )}
      </Section>

      <Section
        title="What you are answerable for"
        description="Data owned by your department. You decide who reads it."
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
            {mine
              ? `${mine.datasets} ${mine.datasets === 1 ? "dataset belongs" : "datasets belong"} to ${mine.name}.`
              : "No datasets have been assigned to your department yet."}{" "}
            <Link to="/datasets" className="text-sky-700 underline">
              Look through them
            </Link>
            .
          </p>
        )}
      </Section>

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

/** Oversight: everything that happened, refusals first. */
function OversightHome() {
  const { data, isLoading, error } = useSummary();

  if (isLoading) return <Loading what="the summary" />;
  if (error) return <Failure error={error} what="the summary" />;
  if (!data) return null;

  return (
    <Section
      title="What has been asked for, and what was refused"
      description="Every request is recorded, whether it was granted or not."
    >
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Requests recorded" value={data.decisions} />
        <Stat
          label="Refused"
          value={data.denials}
          hint={
            data.decisions
              ? `${Math.round((data.denials / data.decisions) * 100)}% of everything asked`
              : undefined
          }
          tone={data.denials > 0 ? "warn" : "normal"}
        />
        <Stat
          label="Access currently granted"
          value={data.active_leases}
          hint="Each one expires on its own"
        />
        <Stat
          label="Records erased on request"
          value={data.tombstones}
          hint="Unreadable everywhere, and the request is on record"
        />
      </div>
      <p className="mt-3 text-sm">
        <Link to="/audit" className="text-sky-700 underline">
          Read who accessed what
        </Link>
      </p>
    </Section>
  );
}

/** A researcher: what you can read today, and how to ask for more. */
function ResearcherHome({ principal }: { principal: string }) {
  const { data, isLoading } = useSummary();
  // Their own, not the organisation's. A researcher asking "what was I given"
  // does not want a list of everybody's access, and showing them one would be
  // both unhelpful and a disclosure nobody asked for.
  const [page, setPage] = useState(1);
  const mine = useLeases(principal, { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE });
  // Scoped to this principal server-side now, not fetched for the whole
  // tenant and filtered down here: the old approach could undercount both
  // stats below the moment a tenant has more than 500 lease requests total,
  // since this researcher's own rows are not guaranteed to be among
  // whichever 500 happened to come back.
  // Fetched in full (default limit), not just for a count: both are also
  // rendered as lists further down this same section.
  const waiting = useLeaseRequests("pending", undefined, { principal });
  const refused = useLeaseRequests("rejected", undefined, { principal });
  const people = usePeople();

  return (
    <>
    {!mine.isLoading && !waiting.isLoading && !refused.isLoading && (
      <Section title="At a glance" description="Your own access, not the organisation's.">
        <div
          data-testid="researcher-stats"
          className="grid grid-cols-3 gap-3"
        >
          <Stat label="Access held" value={mine.data?.total ?? 0} />
          <Stat
            label="Waiting on a decision"
            value={waiting.data?.total ?? 0}
            tone={(waiting.data?.total ?? 0) > 0 ? "warn" : "normal"}
          />
          <Stat
            label="Refused"
            value={refused.data?.total ?? 0}
            tone={(refused.data?.total ?? 0) > 0 ? "warn" : "normal"}
          />
        </div>
      </Section>
    )}

    <Section
      title="Access you have been given"
      description="Each one was granted by the person answerable for that data, for one stated purpose, and ends by itself."
    >
      {mine.isLoading ? (
        <Loading what="your access" />
      ) : mine.error ? (
        <Failure error={mine.error} what="your access" />
      ) : !mine.data?.leases.length ? (
        <p className="rounded border border-dashed border-slate-300 p-6 text-sm text-slate-500">
          Nothing has been granted to you. Open a dataset below and ask for what
          you need.
        </p>
      ) : (
        <>
        <ul className="space-y-2" data-testid="my-access">
          {mine.data.leases.map((l) => (
            <li
              key={l.id}
              data-lease={l.id}
              data-active={l.active}
              className={`rounded border p-4 text-sm ${
                l.active
                  ? "border-teal-300 bg-teal-50"
                  : "border-slate-200 bg-white"
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`rounded px-2 py-0.5 text-xs font-medium ${
                    l.active
                      ? "bg-teal-200 text-teal-900"
                      : "bg-slate-200 text-slate-600"
                  }`}
                >
                  {l.active ? "Open" : l.revoked ? "Withdrawn" : "Ended"}
                </span>
                <VersionName
                  versionId={l.dataset_version_id}
                  datasetName={l.dataset_name}
                  version={l.version}
                  currentClass={l.current_class}
                />
              </div>
              <p className="mt-1 text-xs text-slate-600">
                For {l.purpose}. Granted by {people.label(l.approved_by)} on{" "}
                {new Date(l.created_at).toLocaleString()}.
              </p>
              <p className="mt-0.5 text-xs text-slate-500">
                {l.expires_at
                  ? `${l.active ? "Ends" : "Ended"} ${new Date(l.expires_at).toLocaleString()}.`
                  : "Standing: open until somebody revokes it."}
              </p>
            </li>
          ))}
        </ul>
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={mine.data.total}
          onPageChange={setPage}
        />
        </>
      )}

      {((waiting.data?.total ?? 0) > 0 || (refused.data?.total ?? 0) > 0) && (
        <div className="mt-4 space-y-1 text-sm" data-testid="my-requests">
          {(waiting.data?.lease_requests ?? []).map((r) => (
            <p key={r.id} className="flex flex-wrap items-center gap-1.5 text-slate-600">
              <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900">
                Waiting
              </span>
              <VersionName
                versionId={r.dataset_version_id}
                datasetName={r.dataset_name}
                version={r.version}
                currentClass={r.current_class}
              />
              <span>asked on {new Date(r.created_at).toLocaleDateString()}.</span>
            </p>
          ))}
          {/*
            Refusals are shown, not swallowed. Somebody who asked and heard
            nothing back assumes the platform lost it and asks again.
          */}
          {(refused.data?.lease_requests ?? []).map((r) => (
            <p key={r.id} className="flex flex-wrap items-center gap-1.5 text-slate-600">
              <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700">
                Refused
              </span>
              <VersionName
                versionId={r.dataset_version_id}
                datasetName={r.dataset_name}
                version={r.version}
                currentClass={r.current_class}
              />
              <span>by {people.label(r.decided_by)}.</span>
            </p>
          ))}
        </div>
      )}
    </Section>

    <Section
      title="What you can read without asking"
      description="Everything else needs permission from whoever is answerable for it."
    >
      {isLoading ? (
        <Loading what="your access" />
      ) : (
        <>
          <div className="flex flex-wrap gap-3">
            <div className="flex items-center gap-2 rounded border border-slate-200 bg-white px-3 py-2">
              <ClassBadge value="PUBLISHED" />
              <span className="text-sm text-slate-600">
                Ready for general use
              </span>
              <span className="text-lg font-semibold tabular-nums">
                {data?.versions_by_class.PUBLISHED ?? 0}
              </span>
            </div>
          </div>
          <p className="mt-4 max-w-2xl text-sm text-slate-600">
            To read anything more sensitive, open it and ask. Access is granted
            for one version of one dataset, one stated purpose and a limited
            time, and it ends by itself.{" "}
            <Link to="/roles" className="text-sky-700 underline">
              How that works
            </Link>
            .
          </p>
          <p className="mt-3 text-sm">
            <Link to="/datasets" className="text-sky-700 underline">
              Look through the datasets
            </Link>
          </p>
        </>
      )}
    </Section>
    </>
  );
}

/** Running the platform: the parts, and what it currently holds. */
function PlatformHome() {
  const { data, isLoading, error } = useSummary();

  return (
    <>
      <Section
        title="The parts of the system"
        description="Where a part has an interface of its own, this opens it rather than repeating it."
      >
        <ul
          data-testid="component-map"
          className="grid gap-3 md:grid-cols-2 lg:grid-cols-3"
        >
          {COMPONENTS.map((c) => (
            <li
              key={c.service}
              data-service={c.service}
              className="rounded border border-slate-200 bg-white p-4"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="font-medium">{c.label}</span>
                {c.url && (
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noreferrer"
                    className="shrink-0 text-xs text-sky-700 underline"
                  >
                    open
                  </a>
                )}
              </div>
              <code className="mt-0.5 block font-mono text-[11px] text-slate-400">
                {c.service}
              </code>
              <p className="mt-2 text-sm text-slate-600">{c.purpose}</p>
              <p className="mt-2 text-xs text-slate-400">
                If it is down: {c.ifDown}
              </p>
            </li>
          ))}
        </ul>
      </Section>

      {isLoading ? (
        <Loading what="the summary" />
      ) : error ? (
        <Failure error={error} what="the summary" />
      ) : data ? (
        <Section
          title="What the platform holds"
          description="Counted from the platform itself."
        >
          <div
            data-testid="overview-stats"
            className="grid grid-cols-2 gap-3 md:grid-cols-4"
          >
            <Stat label="Datasets" value={data.datasets} />
            <Stat
              label="Sealed versions"
              value={data.versions}
              hint="None can be altered once sealed"
            />
            <Stat
              label="Requests recorded" value={data.decisions}
            />
            <Stat
              label="Refused"
              value={data.denials}
              tone={data.denials > 0 ? "warn" : "normal"}
            />
          </div>
        </Section>
      ) : null}
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
      <div className="mb-6 rounded border border-slate-200 bg-white p-4">
        <h1 className="text-lg font-semibold">
          {principal.label}
          {principal.department_name && (
            <span className="ml-2 text-sm font-normal text-slate-500">
              {principal.department_name}
            </span>
          )}
        </h1>
        <p className="mt-1 text-sm text-slate-600">{principal.landing}</p>
      </div>

      {mayApprove && (
        <CustodianHome
          custodian={principal.id}
          department={principal.department_name}
        />
      )}
      {roles.includes("notebook_explore") && (
        <ResearcherHome principal={principal.id} />
      )}
      {roles.includes("dpo") && <OversightHome />}
      {roles.includes("platform_admin") && <PlatformHome />}
      {roles.includes("pipeline_operator") && <OperatorHome />}
    </>
  );
}

/** Running the de-identification work: what ran, and what was held back. */
function OperatorHome() {
  const { data, isLoading, error } = useSummary();

  if (isLoading) return <Loading what="recent work" />;
  if (error) return <Failure error={error} what="recent work" />;
  if (!data) return null;

  return (
    <Section
      title="The de-identification work"
      description="Recordings are transcribed, identifiers are found and replaced, and the result is checked before anybody can see it."
    >
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Datasets" value={data.datasets} />
        <Stat
          label="Sealed versions"
          value={data.versions}
          hint="None can be altered once sealed"
        />
        <Stat
          label="Released for wider use"
          value={data.promotions}
          hint="Each one had to pass a check"
        />
        <Stat
          label="Held back"
          value={data.versions_by_class.RAW ?? 0}
          hint="Still identifiable, so only the pipeline sees them"
        />
      </div>
      <p className="mt-3 text-sm">
        <Link to="/datasets" className="text-sky-700 underline">
          Look through the datasets
        </Link>
      </p>
    </Section>
  );
}
