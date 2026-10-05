/**
 * Banner, left navigation, and the current persona.
 *
 * Unbuilt areas appear disabled with the stage that unblocks them, rather than
 * being hidden. A console that hides what it cannot do looks finished, and this
 * one should report its own gaps the same way its test suite does.
 *
 * Nothing here is access control. Navigation varies with the persona for
 * relevance only, and the note in the sidebar says so, because a viewer who
 * mistakes a hidden link for a permission boundary has drawn a conclusion
 * this platform does not back up: a session (identity/IdentityContext.tsx)
 * proves who somebody is, but nothing downstream of that yet checks it
 * before answering a request, so a hidden link was never the thing standing
 * between anyone and any data.
 */

import { joined } from "../lib/joined";
import type { ComponentType, ReactNode, SVGProps } from "react";
import { useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { useIdentity } from "../identity/IdentityContext";
import { UnauthenticatedBanner } from "../identity/IdentityBar";
import { roleLabel } from "../api/roles";
import { ToastHost } from "./toast";
import { useClosing } from "../api/lifecycle";
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

interface Item {
  to: string;
  label: string;
  end?: boolean;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** Roles this is useful to. Absent means everyone. */
  roles?: string[];
}

/**
 * Screens that exist, grouped by what somebody is trying to do.
 *
 * Nothing here names a build stage. An earlier version marked unbuilt screens
 * "stage 4", which is the project's own tracker leaking onto a page somebody is
 * trying to work on. What is not built is not in the navigation, and what the
 * project still owes belongs in its status file.
 *
 * `roles` decides relevance, not permission. Hiding a link enforces nothing:
 * the policy engine refuses regardless of what is on screen, and it refuses the
 * same way for somebody typing the URL directly.
 */
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "", items: [{ to: "/", label: "Home", end: true, icon: HomeIcon }] },
  {
    title: "Data",
    items: [
      { to: "/datasets", label: "Datasets", end: true, icon: DatasetIcon },
      { to: "/departments", label: "Departments", icon: PeopleIcon },
      {
        to: "/gates",
        label: "De-identification results",
        icon: ShieldCheckIcon,
        roles: ["deid_reviewer", "dpo", "pipeline_operator", "platform_admin"],
      },
    ],
  },
  {
    title: "The platform",
    items: [
      {
        to: "/agents",
        label: "Agents",
        icon: AgentIcon,
        roles: ["pipeline_operator", "platform_admin"],
      },
      {
        to: "/pipelines",
        label: "Pipelines",
        icon: PipelineIcon,
        roles: ["pipeline_operator", "platform_admin"],
      },
      {
        to: "/action-runs",
        label: "Action runs",
        icon: RunsIcon,
        roles: ["pipeline_operator", "platform_admin"],
      },
      {
        to: "/egress-approvals",
        label: "Egress approvals",
        icon: EgressIcon,
        roles: ["network_architect", "platform_admin"],
      },
      { to: "/services", label: "Services", icon: ServicesIcon, roles: ["platform_admin"] },
      { to: "/legal-holds", label: "Legal holds", icon: HoldIcon, roles: ["platform_admin"] },
      {
        to: "/housekeeping",
        label: "Storage housekeeping",
        icon: HousekeepingIcon,
        // Support and reliability and the administrator see every
        // organisation's storage; a custodian and a data protection
        // officer see their own organisation's deletions. The policy
        // engine decides which, and refuses the rest whatever this says.
        roles: ["hybridops", "platform_admin", "data_custodian", "dpo"],
      },
    ],
  },
  {
    title: "The organisation",
    items: [
      {
        to: "/directory",
        label: "People and departments",
        icon: PeopleIcon,
        roles: ["dpo", "platform_admin", "data_custodian"],
      },
      { to: "/roles", label: "What each role can do", icon: KeyIcon },
      {
        to: "/closing",
        label: "Closing down the organisation",
        icon: ClosingIcon,
        roles: ["data_custodian", "dpo", "platform_admin"],
      },
    ],
  },
  {
    title: "Audit",
    items: [
      {
        to: "/audit",
        label: "Who accessed what",
        icon: AuditIcon,
        roles: ["dpo", "data_custodian", "platform_admin"],
      },
    ],
  },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { principal, authenticated, clear } = useIdentity();
  const closing = useClosing(undefined, Boolean(principal)).data;
  const navigate = useNavigate();
  const location = useLocation();
  // Closed by default on mobile, where the identity card plus the full nav
  // list used to sit permanently above the page -- on a phone that meant
  // scrolling past your own name and every link just to reach the screen
  // you were already on. Desktop is unaffected: the sidebar there was never
  // gated by this, only the mobile toggle below reads it.
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname]);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      {/*
        `authenticated` is a real, Kratos-verified session (IdentityContext.tsx),
        not a locally-picked identity -- that mode was retired. False here
        means genuinely nobody is signed in yet (session still resolving, or
        signed out), so the banner belongs on every screen until a real
        session exists, the same reach App.tsx's own unresolved-principal
        branch already gives it.
      */}
      {!authenticated && <UnauthenticatedBanner />}

      <div className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 md:hidden">
        <div>
          <div className="text-base font-semibold">Munitas</div>
          <div className="text-xs text-slate-500">Governance console</div>
        </div>
        <button
          type="button"
          data-testid="mobile-nav-toggle"
          aria-expanded={navOpen}
          aria-label={navOpen ? "Close menu" : "Open menu"}
          onClick={() => setNavOpen((open) => !open)}
          className="rounded border border-slate-300 p-2 text-slate-700"
        >
          {navOpen ? (
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-5 w-5">
              <path strokeLinecap="round" d="M5 5l10 10M15 5L5 15" />
            </svg>
          ) : (
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} className="h-5 w-5">
              <path strokeLinecap="round" d="M3 5h14M3 10h14M3 15h14" />
            </svg>
          )}
        </button>
      </div>

      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-4 py-6 md:flex-row">
        <aside
          className={`w-full shrink-0 md:block md:w-56 ${navOpen ? "block" : "hidden"}`}
        >
          {/* Desktop only: the mobile top bar above already names the
              product and shows the toggle that controls this element. */}
          <div className="mb-6 hidden md:block">
            <div className="text-base font-semibold">Munitas</div>
            <div className="mt-0.5 text-xs text-slate-500">
              Governance console
            </div>
          </div>

          {principal && (
            <div className="mb-6 rounded border border-slate-200 bg-white p-4">
              <div className="text-xs uppercase tracking-wide text-slate-500">
                Signed in as
              </div>
              <div
                data-testid="current-persona"
                className="mt-1 text-sm font-medium"
              >
                {principal.label}
              </div>
              {/*
                The role, next to the name. What you can do here follows from
                it, so a screen that names the person and not the role leaves
                somebody to work out from the navigation why an option they
                expected is missing.
              */}
              <div data-testid="current-roles" className="mt-0.5 text-sm text-slate-500">
                {principal.roles.map(roleLabel).join(", ")}
                {principal.approver_of.length > 0 && (
                  <span className="text-slate-400">
                    {" "}
                    for {joined(principal.approver_of)}
                  </span>
                )}
              </div>
              {/*
                Which organisation, on every screen.

                Every list in the console is filtered to this one, so leaving it
                unnamed means the console quietly decides what you can see and
                never says on what basis. An empty dataset list would then be
                indistinguishable from a platform holding nothing.
              */}
              <div className="mt-4 text-xs uppercase tracking-wide text-slate-500">
                Organisation
              </div>
              <div
                data-testid="current-tenant"
                className="mt-1 flex items-center gap-1.5 text-sm"
              >
                {principal.tenant_id}
                {principal.tenant_purpose !== "production" && (
                  <span
                    data-testid="tenant-purpose"
                    className="rounded bg-amber-100 px-1.5 py-0.5 text-xs font-medium text-amber-900"
                  >
                    {principal.tenant_purpose === "canary"
                      ? "for testing"
                      : closing?.phase === "retiring"
                        ? "closing down"
                        : "closed"}
                  </span>
                )}
              </div>
              <p className="mt-1.5 text-xs text-slate-400">
                You see only what this organisation owns.
              </p>
              <button
                type="button"
                data-testid="switch-persona"
                onClick={async () => {
                  // There is no "switch" short of signing out and back in as
                  // whoever you can actually log in as, so this button does
                  // that rather than pretend a lighter option exists.
                  //
                  // Awaited on purpose: navigating before the real Kratos
                  // logout finishes server-side left a window where a
                  // reload landed back on the still-valid old session
                  // instead of the sign-in screen.
                  await clear();
                  navigate("/auth/login");
                }}
                className="mt-3 text-sm text-sky-700 underline"
              >
                Sign out
              </button>
            </div>
          )}

          <nav className="space-y-5">
            {GROUPS.map((group) => {
              const items = group.items.filter(
                (item) =>
                  !item.roles ||
                  item.roles.some((r) => principal?.roles.includes(r)),
              );
              if (!items.length) return null;

              return (
                <div key={group.title || "root"}>
                  {group.title && (
                    <div className="mb-1.5 text-xs uppercase tracking-wide text-slate-400">
                      {group.title}
                    </div>
                  )}
                  <ul className="space-y-0.5">
                    {items.map((item) => (
                      <li key={item.to}>
                        <NavLink
                          to={item.to}
                          end={item.end}
                          className={({ isActive }) =>
                            `flex items-center gap-2 rounded px-2 py-1.5 text-sm transition-colors ${
                              isActive
                                ? "bg-indigo-500 hover:bg-indigo-800 font-medium text-white"
                                : "text-slate-600 hover:bg-slate-200"
                            }`
                          }
                        >
                          <item.icon />
                          {item.label}
                        </NavLink>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </nav>
        </aside>

        <main className="min-w-0 flex-1">
          {closing?.phase === "retiring" && (
            <div
              role="status"
              data-testid="closing-banner"
              className="mb-6 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
            >
              <strong>This organisation is closing down.</strong> Nothing can be added or changed. You can still
              read, and it can be cancelled until {new Date(closing.retiring_until ?? "").toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })} ({closing.days_left} days left).{" "}
              <NavLink to="/closing" className="underline">
                See where it stands
              </NavLink>
            </div>
          )}
          {children}
        </main>
      </div>
      <ToastHost />
    </div>
  );
}
