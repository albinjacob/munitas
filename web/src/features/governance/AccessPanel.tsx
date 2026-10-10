/**
 * The person's access to one version, and the way to ask for more, on the version's page.
 *
 * What can be had depends on what the version holds, and the platform says so (`useReadability`): a version of locked files can be had only a few files
 * at a time, opened by the platform; a table with locked columns can be had in its readable columns, and a few rows of the locked ones can be asked for
 * separately; anything else can be had whole. This panel offers only the asks that can work for that, so nobody fills in a form that cannot lead anywhere.
 * It is the one place on the page where somebody asks. The files, the columns and the rows they open are shown in their own sections.
 */

import { useReadability } from "../../api/readability";
import type { LeaseRequest, VersionAccess } from "../../api/types";
import { AccessMark } from "../../components/AccessMark";
import { FileLeasePanel } from "../locking/FileLeasePanel";
import { LockedValuesAccess } from "../locking/LockedColumns";
import { RequestAccess } from "./RequestAccess";

export function AccessPanel({
  versionId,
  principalId,
  mark,
  markFailed,
  existing,
}: {
  versionId: string;
  principalId: string;
  mark: VersionAccess | undefined;
  markFailed: boolean;
  /** This person's requests for this version. */
  existing: LeaseRequest[];
}) {
  const readability = useReadability(versionId);
  if (readability.isLoading) return null;
  const found = readability.data;

  // Asking for the whole version is offered only where asking can change something: nothing asked yet, access that ended, or a request waiting.
  const offerForm = mark !== undefined && (mark.mark === "ask" || mark.mark === "ended" || mark.mark === "pending");

  if (found?.shape === "files") {
    return (
      <div data-testid="access-panel" data-shape="files">
        <FileLeasePanel versionId={versionId} says={found.says} />
      </div>
    );
  }

  const whole = (of?: string) => (
    <>
      {/* A waiting request is described by the form itself, once. */}
      {mark?.mark !== "pending" && (
        <p className="rounded border border-slate-200 bg-white p-4 text-sm">
          <AccessMark access={mark} failed={markFailed} detailed of={of} />
        </p>
      )}
      {offerForm && (
        <div className={mark?.mark === "pending" ? undefined : "mt-3"}>
          <RequestAccess versionId={versionId} principal={principalId} existing={existing} />
        </div>
      )}
    </>
  );

  if (found?.shape === "columns") {
    const readable = found.reads === "readable_columns";
    return (
      <div data-testid="access-panel" data-shape="columns" className="space-y-6">
        {found.says && <p className="text-sm text-slate-800">{found.says}</p>}
        {readable && (
          <div>
            <h3 className="mb-2 text-base font-semibold text-slate-900">The readable columns</h3>
            {whole("the readable columns")}
          </div>
        )}
        <div>
          <h3 className="mb-2 text-base font-semibold text-slate-900">The locked values</h3>
          <LockedValuesAccess versionId={versionId} />
        </div>
      </div>
    );
  }

  return (
    <div data-testid="access-panel" data-shape="plain">
      {whole()}
    </div>
  );
}
