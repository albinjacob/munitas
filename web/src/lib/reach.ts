/**
 * What a role can read without asking, in words. A role's "floor" is the most sensitive access level it reaches without a lease; the levels from raw
 * (most sensitive) to published (least) are ordered, so a floor of "open for training" reaches that level and everything less sensitive.
 */
import type { VisibilityClass } from "../api/types";

const REACH: Record<VisibilityClass, string> = {
  PUBLISHED: "Published data only",
  OPEN_FOR_TRAINING: "Open for training and published data",
  OPEN_FOR_ANNOTATION: "Open for annotation, open for training and published data",
  UNDER_REVIEW: "Everything except raw data",
  RAW: "Everything, including raw data",
};

export function reachWithoutLease(floor: VisibilityClass): string {
  return REACH[floor] ?? REACH.PUBLISHED;
}
