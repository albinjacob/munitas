/**
 * The home pages for the roles whose work is a queue: a reviewer of results, an approver of the hosts an agent may call, a data engineer
 * following what they asked for, a privacy auditor, an administrator.
 *
 * Every home has the same shape: tiles that count what matters and open it, then panels that answer what is waiting for me and what is
 * happening with my things. Nothing here is a permission boundary; the platform refuses the same way whether or not a row was on screen.
 */

import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useActionRuns, useDatasets, useDecisions, useOrganisation, useSummary } from "../../api/queries";
import { useEgressApprovals } from "../../api/egress";
import { useFileLeases } from "../../api/fileLeases";
import { useGateDecisions } from "../../api/gates";
import { useLeases as useLockedLeases, useLockingAudit } from "../../api/locking";
import { usePeople } from "../../api/people";
import { useIdentity } from "../../identity/IdentityContext";
import { joined } from "../../lib/joined";
import { when } from "../../lib/when";
import { Go, Named, Nothing, Panel, Pill, Rows, Tile, Tiles, count } from "./Blocks";

/** What a panel shows while its list is still coming, or when it could not come. */
function Body({ loading, error, what, empty, children }: { loading: boolean; error: unknown; what: string; empty: boolean; children: ReactNode }) {
  if (loading) return <Nothing>Loading</Nothing>;
  if (error) return <Nothing>The {what} could not be loaded.</Nothing>;
  if (empty) return <Nothing>{`No ${what}.`}</Nothing>;
  return <>{children}</>;
}

function requestPill(status: string) {
  if (status === "requested") return <Pill tone="wait">Waiting</Pill>;
  if (status === "approved") return <Pill tone="ok">Approved</Pill>;
  if (status === "declined") return <Pill tone="stop">Declined</Pill>;
  return <Pill tone="none">Ended</Pill>;
}

/** A reviewer of de-identification results: the results waiting on them, and what was decided lately. */
export function ReviewerHome() {
  const { principal, tenant } = useIdentity();
  const people = usePeople();
  const waiting = useGateDecisions(tenant, principal?.id, 5, 0, "pending");
  const released = useGateDecisions(tenant, undefined, 1, 0, "promoted");
  const refused = useGateDecisions(tenant, undefined, 1, 0, "refused");
  const lately = useGateDecisions(tenant, undefined, 15);
  const decided = (lately.data?.gate_decisions ?? []).filter((g) => g.state !== "pending").slice(0, 5);
  const nWaiting = waiting.data?.total;

  return (
    <>
      <Tiles three>
        <Tile to="/gates" tone="indigo" icon="clock" value={nWaiting} name="Waiting for your decision" attention={(nWaiting ?? 0) > 0} testid="reviewer-waiting-count" />
        <Tile to="/gates" tone="green" icon="check" value={released.data?.total} name="Released so far" />
        <Tile to="/gates" tone="red" icon="x" value={refused.data?.total} name="Refused so far" />
      </Tiles>

      <Panel title="Waiting for your decision" sub="A result stays hidden from everybody but the reviewers until one of them releases it or refuses it." to="/gates" toLabel="See all results">
        <Body loading={waiting.isLoading} error={waiting.error} what="results waiting for you" empty={!waiting.data?.gate_decisions.length}>
          <Rows
            testid="reviewer-waiting"
            head={["Result", "Made by", "Made", ""]}
            rows={(waiting.data?.gate_decisions ?? []).map((g) => ({
              key: g.id,
              cells: [<Named name={g.dataset_name} version={g.version} />, people.label(g.triggered_by), when(g.created_at), <Go to={`/gates/${g.id}`}>Review</Go>],
            }))}
          />
        </Body>
      </Panel>

      <Panel title="Decided lately" sub="The most recent decisions in this organisation, by anybody." to="/gates" toLabel="See all results">
        <Body loading={lately.isLoading} error={lately.error} what="decided results" empty={!decided.length}>
          <Rows
            testid="reviewer-decided"
            head={["Result", "Outcome", "Decided by", "Decided"]}
            rows={decided.map((g) => ({
              key: g.id,
              cells: [
                <Named name={g.dataset_name} version={g.version} to={`/gates/${g.id}`} />,
                g.state === "promoted" ? <Pill tone="ok">Released</Pill> : <Pill tone="stop">Refused</Pill>,
                people.label(g.decided_by),
                when(g.decided_at),
              ],
            }))}
          />
        </Body>
      </Panel>
    </>
  );
}

/** An approver of the hosts agents may call: the requests waiting on them, and what was decided lately. */
export function ArchitectHome() {
  const people = usePeople();
  const waiting = useEgressApprovals("pending", 5, 0);
  const approved = useEgressApprovals("approved", 1, 0);
  const refused = useEgressApprovals("refused", 1, 0);
  const lately = useEgressApprovals(undefined, 15, 0);
  const decided = (lately.data?.egress_approvals ?? []).filter((a) => a.state !== "pending").slice(0, 5);
  const nWaiting = waiting.data?.total;

  return (
    <>
      <Tiles three>
        <Tile to="/egress-approvals" tone="indigo" icon="clock" value={nWaiting} name="Waiting for your decision" attention={(nWaiting ?? 0) > 0} testid="architect-waiting-count" />
        <Tile to="/egress-approvals" tone="green" icon="check" value={approved.data?.total} name="Approved so far" />
        <Tile to="/egress-approvals" tone="red" icon="x" value={refused.data?.total} name="Refused so far" />
      </Tiles>

      <Panel title="Waiting for your decision" sub="An agent may reach only the external hosts somebody has approved for that version." to="/egress-approvals" toLabel="See all approvals">
        <Body loading={waiting.isLoading} error={waiting.error} what="approvals waiting for you" empty={!waiting.data?.egress_approvals.length}>
          <Rows
            testid="architect-waiting"
            head={["Agent", "Hosts it asks to call", "Submitted by", "Submitted", ""]}
            rows={(waiting.data?.egress_approvals ?? []).map((a) => ({
              key: a.id,
              cells: [
                <Named name={a.agent_name} version={a.version} />,
                <span className="mono">{joined(a.requested_hosts)}</span>,
                a.submitted_by_label ?? people.label(a.submitted_by),
                when(a.created_at),
                <Go to={`/egress-approvals/${a.id}`}>Review</Go>,
              ],
            }))}
          />
        </Body>
      </Panel>

      <Panel title="Decided lately" sub="The most recent decisions in this organisation, by anybody." to="/egress-approvals" toLabel="See all approvals">
        <Body loading={lately.isLoading} error={lately.error} what="decided approvals" empty={!decided.length}>
          <Rows
            testid="architect-decided"
            head={["Agent", "Outcome", "Decided by", "Decided"]}
            rows={decided.map((a) => ({
              key: a.id,
              cells: [
                <Named name={a.agent_name} version={a.version} to={`/egress-approvals/${a.id}`} />,
                a.state === "approved" ? <Pill tone="ok">Approved</Pill> : <Pill tone="stop">Refused</Pill>,
                a.decided_by_label ?? "An architect",
                when(a.decided_at),
              ],
            }))}
          />
        </Body>
      </Panel>
    </>
  );
}

/** A data engineer: what they asked for and where each request stands, and what their own work is doing. */
export function EngineerHome() {
  const { principal, tenant } = useIdentity();
  const me = principal?.id;
  const files = useFileLeases();
  const locked = useLockedLeases();
  const results = useGateDecisions(tenant, undefined, 20, 0, "pending", me);
  const approvals = useEgressApprovals("pending", 50, 0);
  const runs = useActionRuns(50, 0);

  const asked = [
    ...(files.data?.mine ?? []).map((l) => ({
      key: `file-${l.id}`,
      at: l.requested_at,
      name: l.dataset_name ?? "A table",
      version: l.dataset_version,
      to: `/versions/${l.dataset_version_id}`,
      what: "Some of its files",
      status: l.status as string,
    })),
    ...(locked.data?.mine ?? []).map((l) => ({
      key: `rows-${l.id}`,
      at: l.requested_at,
      name: l.dataset_name ?? "A table",
      version: l.dataset_version,
      to: `/versions/${l.dataset_version_id}`,
      what: "Some of its rows",
      status: l.status as string,
    })),
  ].sort((a, b) => b.at.localeCompare(a.at));

  const mineApprovals = (approvals.data?.egress_approvals ?? []).filter((a) => a.submitted_by === me);
  const myRuns = (runs.data?.action_runs ?? []).filter((r) => r.triggered_by === me).slice(0, 5);
  const waitingOn = (results.data?.gate_decisions ?? []).filter((g) => g.triggered_by === me);
  const nAskedWaiting = asked.filter((a) => a.status === "requested").length;
  const nOpen = asked.filter((a) => a.status === "approved").length;
  const loadingAsked = files.isLoading || locked.isLoading;
  const stuck = waitingOn.length + mineApprovals.length;

  return (
    <>
      <Tiles>
        <Tile to="/locked-column-access" tone="indigo" icon="clock" value={loadingAsked ? null : nAskedWaiting} name="Requests waiting for an approver" attention={nAskedWaiting > 0} />
        <Tile to="/locked-column-access" tone="green" icon="check" value={loadingAsked ? null : nOpen} name="Requests approved and open" />
        <Tile to="/gates" tone="blue" icon="eye" value={results.isLoading ? null : waitingOn.length} name="Results waiting on a reviewer" />
        <Tile to="/egress-approvals" tone="indigo" icon="globe" value={approvals.isLoading ? null : mineApprovals.length} name="Agents waiting on a host approval" />
      </Tiles>

      <Panel title="What you asked for" sub="Your requests to read files or rows of locked data, newest first." to="/locked-column-access" toLabel="See all requests" testid="engineer-asked-panel">
        <Body loading={loadingAsked} error={files.error ?? locked.error} what="requests from you" empty={!asked.length}>
          <Rows
            testid="engineer-asked"
            head={["Table", "You asked to read", "State", "Asked"]}
            rows={asked.slice(0, 5).map((a) => ({
              key: a.key,
              cells: [<Named name={a.name} version={a.version} to={a.to} />, a.what, requestPill(a.status), when(a.at)],
            }))}
          />
        </Body>
      </Panel>

      <Panel title="Waiting on somebody else" sub="Your work that cannot move until another person decides.">
        <Body loading={results.isLoading || approvals.isLoading} error={results.error ?? approvals.error} what="work waiting on somebody else" empty={!stuck}>
          <Rows
            testid="engineer-waiting"
            head={["What", "Waiting on", "Since"]}
            rows={[
              ...waitingOn.map((g) => ({
                key: `gate-${g.id}`,
                cells: [
                  <>
                    <Named name={g.dataset_name} version={g.version} to={`/gates/${g.id}`} />
                    <span className="sub">A result to release</span>
                  </>,
                  g.waiting_on.length ? joined(g.waiting_on.map((w) => w.label)) : "A reviewer",
                  when(g.created_at),
                ],
              })),
              ...mineApprovals.map((a) => ({
                key: `egress-${a.id}`,
                cells: [
                  <>
                    <Named name={a.agent_name} version={a.version} to={`/egress-approvals/${a.id}`} />
                    <span className="sub">Hosts to approve: {joined(a.requested_hosts)}</span>
                  </>,
                  "A network architect",
                  when(a.created_at),
                ],
              })),
            ]}
          />
        </Body>
      </Panel>

      <Panel title="Your recent runs" sub="Pipeline steps that you started, newest first." to="/action-runs" toLabel="See all runs">
        <Body loading={runs.isLoading} error={runs.error} what="runs started by you" empty={!myRuns.length}>
          <Rows
            testid="engineer-runs"
            head={["Step", "Result", "Started"]}
            rows={myRuns.map((r) => ({
              key: r.id,
              cells: [
                <span className="mono">{r.action_name}</span>,
                r.status === "succeeded" ? <Pill tone="ok">Finished</Pill> : r.status === "failed" ? <Pill tone="stop">Failed</Pill> : <Pill tone="wait">Running</Pill>,
                when(r.started_at),
              ],
            }))}
          />
        </Body>
      </Panel>
    </>
  );
}

/** A privacy auditor: the latest openings of locked files, and the latest requests to open them. The files' contents are never shown. */
export function AuditorHome() {
  const audit = useLockingAudit(true);
  const opened = (audit.data?.file_reads ?? []).slice(0, 6);
  const asked = (audit.data?.file_leases ?? []).slice(0, 5);

  return (
    <>
      <Panel title="Latest openings of locked files" sub="Who opened which file, and whether the platform allowed it. The personal data in the files is never shown here." to="/locking-audit" toLabel="Open the whole audit">
        <Body loading={audit.isLoading} error={audit.error} what="openings of locked files" empty={!opened.length}>
          <Rows
            testid="auditor-latest"
            head={["Who", "Result", "File", "Table", "When"]}
            rows={opened.map((r, i) => ({
              key: `${r.at}-${i}`,
              cells: [
                r.principal_label,
                r.allowed ? <Pill tone="ok">Opened</Pill> : <Pill tone="stop">Refused</Pill>,
                <span className="mono">{r.file ?? "A locked file"}</span>,
                r.table ? <span className="mono">{r.table}</span> : "",
                when(r.at),
              ],
            }))}
          />
        </Body>
      </Panel>

      <Panel title="Latest requests to open locked files" sub="Each request needs an approver of the department that owns the files." to="/locking-audit" toLabel="Open the whole audit">
        <Body loading={audit.isLoading} error={audit.error} what="requests to open locked files" empty={!asked.length}>
          <Rows
            testid="auditor-requests"
            head={["Who asked", "How", "State", "Asked"]}
            rows={asked.map((l) => ({
              key: l.id,
              cells: [l.principal_label, l.kind === "notebook" ? "From a notebook" : "In the page", requestPill(l.status), when(l.requested_at)],
            }))}
          />
        </Body>
      </Panel>
    </>
  );
}

/** An administrator: what the platform holds, anything that needs an owner, and the latest refusals. */
export function AdministratorHome() {
  const summary = useSummary();
  const organisation = useOrganisation();
  const unowned = organisation.data?.datasets_without_a_department ?? 0;
  const s = summary.data;
  // The datasets tile counts what the Datasets page lists by default, so the number agrees with the page it opens. Deleted datasets are left out of it and
  // named underneath.
  const listed = useDatasets({ limit: 1 });
  const deleted = listed.data?.deleted ?? 0;

  return (
    <>
      {unowned > 0 && (
        <div className="ds-banner" data-testid="unowned-home">
          <strong>{count(unowned, "dataset has", "datasets have")} no owning department.</strong> Nobody can approve access to {unowned === 1 ? "it" : "them"} until a
          department owns {unowned === 1 ? "it" : "them"}. <Link to="/departments">Open the departments</Link>
        </div>
      )}

      {/* The test id is on the figures only once they have arrived, so a reader (or a test) that waits for it never sees the placeholders. */}
      <div data-testid={s && listed.data ? "overview-stats" : undefined}>
        <Tiles>
          <Tile to="/datasets" tone="blue" icon="database" value={listed.data?.total} name="Datasets" hint={deleted > 0 ? `${deleted} deleted, record kept` : undefined} />
          <Tile to="/datasets" tone="indigo" icon="layers" value={s?.versions} name="Sealed versions" hint="None can be altered once sealed" />
          <Tile to="/audit" tone="green" icon="check" value={s?.decisions} name="Requests recorded" />
          <Tile to="/audit" tone="red" icon="alert" value={s?.denials} name="Refused" />
        </Tiles>
      </div>

      <LatestRefusals />
    </>
  );
}

/** For an approver: requests to read locked files or rows wait on a different page from the requests for a dataset, so say so. Shown only when something is waiting. */
export function LockedWaiting() {
  const files = useFileLeases();
  const rows = useLockedLeases();
  const n = (files.data?.to_decide.length ?? 0) + (rows.data?.to_decide.length ?? 0);
  if (!n) return null;
  return (
    <div className="ds-banner" data-testid="locked-waiting">
      <strong>{count(n, "request to read locked files or rows is", "requests to read locked files or rows are")} waiting for you.</strong>{" "}
      <Link to="/locked-column-access">Open the locked data requests</Link>
    </div>
  );
}

/** The most recent requests the platform turned down, and why. Shown to the people who watch the whole organisation. */
function LatestRefusals() {
  const people = usePeople();
  const refusals = useDecisions({ allowed: false, limit: 5 });
  return (
    <Panel title="Latest refusals" sub="The most recent requests the platform turned down, and why." to="/audit" toLabel="See every decision">
      <Body loading={refusals.isLoading} error={refusals.error} what="refusals" empty={!refusals.data?.decisions.length}>
        <Rows
          testid="admin-refusals"
          head={["Who", "Asked for", "Why it was refused", "When"]}
          rows={(refusals.data?.decisions ?? []).map((d) => ({
            key: String(d.id),
            cells: [people.label(d.principal), d.purpose ?? "No purpose stated", d.reasons[0] ?? "", when(d.at)],
          }))}
        />
      </Body>
    </Panel>
  );
}

/** Oversight: everything that happened, refusals first. */
export function OversightHome() {
  const { data } = useSummary();
  return (
    <>
      <Tiles>
        <Tile to="/audit" tone="blue" icon="eye" value={data?.decisions} name="Requests recorded" hint="Granted or not, every one is on record" />
        <Tile
          to="/audit"
          tone="red"
          icon="alert"
          value={data?.denials}
          name="Refused"
          hint={data?.decisions ? `${Math.round((data.denials / data.decisions) * 100)}% of everything asked` : undefined}
          attention={(data?.denials ?? 0) > 0}
        />
        <Tile to="/audit" tone="green" icon="check" value={data?.active_leases} name="Access currently granted" hint="Each one expires on its own" />
        <Tile to="/audit" tone="indigo" icon="lock" value={data?.tombstones} name="Records erased on request" hint="Unreadable everywhere, and the request is on record" />
      </Tiles>
      <LatestRefusals />
    </>
  );
}
