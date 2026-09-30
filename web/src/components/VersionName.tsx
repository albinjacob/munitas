/**
 * What a grant is actually about: one version of one dataset.
 *
 * Access is leased against a `dataset_version_id`, never a dataset. Two versions
 * of the same recordings can sit at different sensitivities, because the second
 * one went through de-identification and the first did not. So a request that
 * said only "Sam wants to read consultation-audio" asked the custodian to
 * approve something whose sensitivity was not on screen, and the answer to
 * "which one?" is the whole decision.
 *
 * The class travels with the version for the same reason. Whoever is deciding
 * needs to know how sensitive the thing is at the moment they decide, not what
 * the dataset is like in general.
 */

import { Link } from "react-router-dom";
import type { VisibilityClass } from "../api/types";
import { ClassBadge } from "./ClassBadge";

export function VersionName({
  versionId,
  datasetName,
  version,
  currentClass,
  link = true,
}: {
  versionId: string;
  datasetName: string | null;
  version: number | null;
  currentClass: VisibilityClass | null;
  /** Off where the surrounding row is already a link, so one row is one target. */
  link?: boolean;
}) {
  // Falls back rather than inventing. A version number that failed to load is
  // better shown as absent than rendered as v0, which is a real version number
  // somewhere and reads as fact.
  const name = `${datasetName ?? "a dataset"}${version === null ? "" : ` v${version}`}`;

  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {link ? (
        <Link
          to={`/versions/${versionId}`}
          data-testid="version-name"
          className="font-medium text-sky-700 underline"
        >
          {name}
        </Link>
      ) : (
        <span data-testid="version-name" className="font-medium">
          {name}
        </span>
      )}
      {currentClass && <ClassBadge value={currentClass} />}
    </span>
  );
}
