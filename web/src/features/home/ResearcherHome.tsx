/**
 * A researcher: what they can read today, what they asked for and where each request stands, and how to ask for more.
 *
 * Their own access, not the organisation's. Somebody asking "what was I given" does not want a list of everybody's access, and showing one
 * would be a disclosure nobody asked for.
 */

import { useState } from "react";
import { Link } from "react-router-dom";
import { useLeaseRequests, useLeases, useSummary } from "../../api/queries";
import { usePeople } from "../../api/people";
import { ClassBadge } from "../../components/ClassBadge";
import { Pagination } from "../../components/Pagination";
import { VersionName } from "../../components/VersionName";
import { when } from "../../lib/when";
import { Nothing, Panel, Pill, Rows, Tile, Tiles } from "./Blocks";

const PAGE_SIZE = 15;

export function ResearcherHome({ principal }: { principal: string }) {
  const summary = useSummary();
  const [page, setPage] = useState(1);
  const mine = useLeases(principal, { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE });
  // Scoped to this person on the server, not fetched for the whole organisation and cut down here: the old way undercounted both figures below
  // once an organisation had more than 500 requests in all.
  const waiting = useLeaseRequests("pending", undefined, { principal });
  const refused = useLeaseRequests("rejected", undefined, { principal });
  const people = usePeople();

  const asked = [
    ...(waiting.data?.lease_requests ?? []).map((r) => ({ r, state: "waiting" as const })),
    // Refusals are shown, not swallowed. Somebody who asked and heard nothing back assumes the platform lost it and asks again.
    ...(refused.data?.lease_requests ?? []).map((r) => ({ r, state: "refused" as const })),
  ];

  return (
    <>
      <div data-testid="researcher-stats">
        <Tiles three>
          <Tile to="/datasets" tone="green" icon="check" value={mine.isLoading ? null : (mine.data?.total ?? 0)} name="Access you hold" />
          <Tile to="/datasets" tone="indigo" icon="clock" value={waiting.isLoading ? null : (waiting.data?.total ?? 0)} name="Waiting on a decision" attention={(waiting.data?.total ?? 0) > 0} />
          <Tile to="/datasets" tone="red" icon="x" value={refused.isLoading ? null : (refused.data?.total ?? 0)} name="Refused" />
        </Tiles>
      </div>

      <Panel title="Access you have been given" sub="Each one was granted by the person answerable for that data, for one stated purpose, and ends by itself.">
        {mine.isLoading ? (
          <Nothing>Loading</Nothing>
        ) : mine.error ? (
          <Nothing>Your access could not be loaded.</Nothing>
        ) : !mine.data?.leases.length ? (
          <Nothing>Nothing has been granted to you. Open a dataset and ask for what you need.</Nothing>
        ) : (
          <>
            <div data-testid="my-access">
              <Rows
                head={["Dataset", "Purpose", "Granted by", "State", "Ends"]}
                rows={mine.data.leases.map((l) => ({
                  key: l.id,
                  attrs: { "data-lease": l.id, "data-active": l.active },
                  cells: [
                    <VersionName versionId={l.dataset_version_id} datasetName={l.dataset_name} version={l.version} currentClass={l.current_class} />,
                    l.purpose,
                    <>
                      {people.label(l.approved_by)}
                      <span className="sub">{when(l.created_at)}</span>
                    </>,
                    l.active ? <Pill tone="ok">Open</Pill> : <Pill tone="none">{l.revoked ? "Withdrawn" : "Ended"}</Pill>,
                    l.expires_at ? when(l.expires_at) : "Open until somebody revokes it",
                  ],
                }))}
              />
            </div>
            <div className="ds-pad" style={{ paddingTop: 0 }}>
              <Pagination page={page} pageSize={PAGE_SIZE} total={mine.data.total} onPageChange={setPage} />
            </div>
          </>
        )}
      </Panel>

      {asked.length > 0 && (
        <Panel title="What you asked for" sub="Requests that are waiting or were refused.">
          <div data-testid="my-requests">
            <Rows
              head={["Dataset", "State", "Detail"]}
              rows={asked.map(({ r, state }) => ({
                key: r.id,
                cells: [
                  <VersionName versionId={r.dataset_version_id} datasetName={r.dataset_name} version={r.version} currentClass={r.current_class} />,
                  state === "waiting" ? <Pill tone="wait">Waiting</Pill> : <Pill tone="stop">Refused</Pill>,
                  state === "waiting" ? `Asked ${when(r.created_at)}` : `By ${people.label(r.decided_by)}${r.decided_at ? ` on ${when(r.decided_at)}` : ""}`,
                ],
              }))}
            />
          </div>
        </Panel>
      )}

      <Panel title="What you can read without asking" sub="Everything else needs permission from whoever is answerable for it." padded>
        <div className="flex flex-wrap items-center gap-3">
          <ClassBadge value="PUBLISHED" />
          <span>Ready for general use</span>
          <strong>{summary.data?.versions_by_class.PUBLISHED ?? 0}</strong>
        </div>
        <p style={{ marginTop: 12, maxWidth: 640 }}>
          To read anything more sensitive, open it and ask. Access is granted for one version of one dataset, one stated purpose and a limited time, and it ends by
          itself. <Link to="/roles">How that works</Link>
        </p>
        <p style={{ marginTop: 8 }}>
          <Link to="/datasets">Look through the datasets</Link>
        </p>
      </Panel>
    </>
  );
}
