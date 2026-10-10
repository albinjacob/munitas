/**
 * What the left navigation lists, and the trail shown above every page.
 *
 * Screens that exist, grouped by what somebody is trying to do. Nothing here names a build stage: what is not built is not in the navigation, and what the
 * project still owes belongs in its status file.
 *
 * Who sees which link, and who may open which page, is not decided here. It comes from the platform's policy (`GET /screens`, from
 * platform/policy/screens.rego), which is also what the API refuses with. This file holds only the layout: the groups, the order, the icons and the trail.
 * Hiding a link enforces nothing; the API and the policy engine refuse what a role may not do, whatever is on screen.
 */

import type { ComponentType, SVGProps } from "react";
import {
  AgentIcon,
  AuditIcon,
  ClosingIcon,
  DatasetIcon,
  EgressIcon,
  HoldIcon,
  HomeIcon,
  HousekeepingIcon,
  KeyIcon,
  PeopleIcon,
  PipelineIcon,
  RunsIcon,
  ServicesIcon,
  ShieldCheckIcon,
} from "./NavIcons";

export interface Item {
  to: string;
  label: string;
  end?: boolean;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}

export const GROUPS: { title: string; items: Item[] }[] = [
  { title: "", items: [{ to: "/", label: "Home", end: true, icon: HomeIcon }] },
  {
    title: "Data",
    items: [
      { to: "/datasets", label: "Datasets", end: true, icon: DatasetIcon },
      { to: "/departments", label: "Departments", icon: PeopleIcon },
      { to: "/gates", label: "De-identification results", icon: ShieldCheckIcon },
      { to: "/locked-column-access", label: "Locked data requests", icon: KeyIcon },
      { to: "/notebook-sign-ins", label: "Your notebooks", icon: KeyIcon },
    ],
  },
  {
    title: "The platform",
    items: [
      { to: "/agents", label: "Agents", icon: AgentIcon },
      { to: "/pipelines", label: "Pipelines", icon: PipelineIcon },
      { to: "/action-runs", label: "Action runs", icon: RunsIcon },
      { to: "/egress-approvals", label: "Egress approvals", icon: EgressIcon },
      { to: "/services", label: "Services", icon: ServicesIcon },
      { to: "/legal-holds", label: "Legal holds", icon: HoldIcon },
      { to: "/housekeeping", label: "Storage housekeeping", icon: HousekeepingIcon },
    ],
  },
  {
    title: "The organisation",
    items: [
      { to: "/directory", label: "People and roles", icon: PeopleIcon },
      { to: "/roles", label: "What each role can do", icon: KeyIcon },
      { to: "/access-rules", label: "Access rules", icon: ShieldCheckIcon },
      { to: "/closing", label: "Closing down the organisation", icon: ClosingIcon },
    ],
  },
  {
    title: "Audit",
    items: [
      { to: "/audit", label: "Access decisions", icon: AuditIcon },
      { to: "/locking-audit", label: "Locked data audit", icon: AuditIcon },
    ],
  },
];

export interface Crumb {
  label: string;
  to?: string;
}

const DATASETS: Crumb[] = [{ label: "Data" }, { label: "Datasets", to: "/datasets" }];
const PLATFORM = { label: "The platform" };

/** Screens that are one step below a navigation entry: the trail above them, and what they are called. */
const BELOW: { path: RegExp; trail: (m: RegExpMatchArray) => Crumb[] }[] = [
  { path: /^\/datasets\/register$/, trail: () => [...DATASETS, { label: "Bring a dataset in" }] },
  { path: /^\/datasets\/[^/]+\/ingest$/, trail: () => [...DATASETS, { label: "Bring data in" }] },
  { path: /^\/versions\/([^/]+)$/, trail: () => [...DATASETS, { label: "Version" }] },
  { path: /^\/versions\/([^/]+)\/locked-rows$/, trail: (m) => [...DATASETS, { label: "Version", to: `/versions/${m[1]}` }, { label: "Locked rows" }] },
  { path: /^\/gates\/[^/]+$/, trail: () => [{ label: "Data" }, { label: "De-identification results", to: "/gates" }, { label: "Decision" }] },
  { path: /^\/notebook-sign-in\//, trail: () => [{ label: "Data" }, { label: "Your notebooks", to: "/notebook-sign-ins" }, { label: "Sign in" }] },
  { path: /^\/agents\/register$/, trail: () => [PLATFORM, { label: "Agents", to: "/agents" }, { label: "Register" }] },
  { path: /^\/agents\/[^/]+$/, trail: () => [PLATFORM, { label: "Agents", to: "/agents" }, { label: "Agent" }] },
  { path: /^\/pipelines\/register$/, trail: () => [PLATFORM, { label: "Pipelines", to: "/pipelines" }, { label: "Register" }] },
  { path: /^\/pipelines\/[^/]+$/, trail: () => [PLATFORM, { label: "Pipelines", to: "/pipelines" }, { label: "Pipeline" }] },
  { path: /^\/pipeline-runs\/[^/]+$/, trail: () => [PLATFORM, { label: "Pipelines", to: "/pipelines" }, { label: "Run" }] },
  { path: /^\/egress-approvals\/[^/]+$/, trail: () => [PLATFORM, { label: "Egress approvals", to: "/egress-approvals" }, { label: "Approval" }] },
];

/** The trail above a page: Home, the group, the page, and for a screen below a page, that screen. */
export function crumbsFor(pathname: string): Crumb[] {
  const home: Crumb = { label: "Home", to: "/" };
  if (pathname === "/") return [{ label: "Home" }];
  for (const rule of BELOW) {
    const m = pathname.match(rule.path);
    if (m) return [home, ...rule.trail(m)];
  }
  for (const group of GROUPS) {
    const item = group.items.find((i) => i.to !== "/" && (pathname === i.to || pathname.startsWith(`${i.to}/`)));
    if (item) return [home, ...(group.title ? [{ label: group.title }] : []), { label: item.label }];
  }
  return [home];
}
