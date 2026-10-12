/**
 * The frame around every screen once somebody is signed in: a fixed dark sidebar that collapses, a header with search, the theme switch, the bell and the
 * profile menu, and the trail above the page.
 *
 * It is mounted once, around all the routes, so the theme, the collapsed sidebar and the search persist as a person moves between screens.
 *
 * What the sidebar shows on every screen: who is signed in and in what role, which organisation (and a chip when it is for testing or closing down).
 * The banner while the organisation is closing is at the top of the content. The header search belongs to the page that is open (`HeaderSearch.tsx`): a
 * page that lists things by name registers it, and a page without one shows none.
 *
 * Nothing here is access control. Navigation varies with what the platform's policy says the person may open, for relevance only: a hidden link was never what stood between anyone and
 * any data, and the policy engine refuses the same way for somebody typing the address.
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import "./shell.css";
import "./buttons.css";
import { GROUPS, crumbsFor } from "./nav";
import { Ico, PATHS } from "./Ico";
import { HeaderSearchProvider, useHeaderSearchConfig } from "./HeaderSearch";
import { ToastHost } from "./toast";
import { useStored, useTheme } from "./useStored";
import { useIdentity } from "../identity/IdentityContext";
import { roleLabel } from "../api/roles";
import { useScreens } from "../api/screens";
import { useClosing } from "../api/lifecycle";
import { useLeases } from "../api/locking";
import { useFileLeases } from "../api/fileLeases";
import { joined } from "../lib/joined";
import { when } from "../lib/when";

const initials = (label: string) =>
  label
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("") || "?";

export function Shell({ children }: { children: ReactNode }) {
  return (
    <HeaderSearchProvider>
      <Frame>{children}</Frame>
    </HeaderSearchProvider>
  );
}

function Frame({ children }: { children: ReactNode }) {
  const { principal, clear } = useIdentity();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const searchConfig = useHeaderSearchConfig();

  const [theme, setTheme] = useTheme();
  const [collapsed, setCollapsed] = useStored<"yes" | "no">("munitas-nav-collapsed", () => "no");
  const [menu, setMenu] = useState(false);
  const search = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);


  // Cmd or Ctrl and K go to the search.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        search.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menu]);
  useEffect(() => setMenu(false), [pathname]);

  const screens = useScreens(Boolean(principal));
  const closing = useClosing(undefined, Boolean(principal)).data;
  const leases = useLeases();
  const fileLeases = useFileLeases();
  const waiting = (leases.data?.to_decide.length ?? 0) + (fileLeases.data?.to_decide.length ?? 0);

  if (!principal) return null;
  const waitingText = waiting === 1 ? "1 request is waiting for you" : `${waiting} requests are waiting for you`;
  // Until the platform has said what this person may open, the menu lists only Home.
  const listed = new Set(Object.values(screens.data ?? {}).filter((x) => x.menu).map((x) => x.path));
  const groups = GROUPS.map((g) => ({ ...g, items: g.items.filter((item) => item.to === "/" || listed.has(item.to)) })).filter((g) => g.items.length > 0);
  const crumbs = crumbsFor(pathname);

  return (
    <div className="ds" data-theme={theme} data-collapsed={collapsed === "yes"} data-testid="ds-page">
      {/* ------------------------------------------------------------ sidebar */}
      <aside className="ds-side" data-testid="ds-sidebar">
        <div className="ds-brand">
          <span className="ds-logo">M</span>
          <div>
            <b>Munitas</b>
            <small>Governance console</small>
          </div>
        </div>
        <nav className="ds-nav" aria-label="Main">
          {groups.map((g) => (
            <div key={g.title || "root"}>
              {g.title && <div className="ds-label">{g.title}</div>}
              {g.items.map((item) => (
                <NavLink key={item.to} to={item.to} end={item.end} title={item.label}>
                  <item.icon />
                  <span className="txt">{item.label}</span>
                  {item.to === "/locked-column-access" && waiting > 0 && (
                    <>
                      <span className="ds-badge" data-testid="ds-nav-badge">
                        {waiting}
                      </span>
                      <span className="ds-pulse ds-pip" style={{ display: collapsed === "yes" ? "block" : "none" }} />
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="ds-foot">
          <div className="ds-me">
            <span className="ds-avatar">{initials(principal.label)}</span>
            <div className="who">
              <b data-testid="current-persona">{principal.label}</b>
              <small data-testid="current-roles">
                {principal.roles.map(roleLabel).join(", ")}
                {principal.approver_of.length > 0 && ` for ${joined(principal.approver_of)}`}
              </small>
            </div>
          </div>
          <div className="ds-org" title="You see only what this organisation owns.">
            <span className="ds-orglabel">Organisation</span>
            <span data-testid="current-tenant">{principal.tenant_id}</span>
            {principal.tenant_purpose !== "production" && (
              <span className="ds-purpose" data-testid="tenant-purpose">
                {principal.tenant_purpose === "canary" ? "for testing" : closing?.phase === "retiring" ? "closing down" : "closed"}
              </span>
            )}
          </div>
          <button type="button" className="ds-fold" data-testid="ds-collapse" onClick={() => setCollapsed(collapsed === "yes" ? "no" : "yes")} aria-expanded={collapsed === "no"}>
            <Ico name="fold" />
            <span className="lbl">{collapsed === "yes" ? "Expand" : "Collapse"}</span>
          </button>
        </div>
      </aside>

      {/* ------------------------------------------------------------ header and trail */}
      <header className="ds-header">
        {searchConfig && (
          <div className="ds-search">
            <Ico name="search" />
            <input
              ref={search}
              data-testid={searchConfig.testid}
              value={searchConfig.value}
              onChange={(e) => searchConfig.onChange(e.target.value)}
              placeholder={searchConfig.placeholder}
              aria-label={searchConfig.placeholder}
            />
            <span className="ds-kbd">⌘K</span>
          </div>
        )}
        <div className="ds-right">
          <button
            type="button"
            className="ds-switch"
            data-testid="ds-theme-toggle"
            role="switch"
            aria-checked={theme === "dark"}
            aria-label="Dark theme"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            <span className="ds-thumb">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                {theme === "dark" ? PATHS.moon : PATHS.sun}
              </svg>
            </span>
          </button>
          <Link to="/locked-column-access" className="ds-iconbtn" aria-label={waiting > 0 ? waitingText : "Nothing is waiting for you"} data-testid="ds-bell" title={waiting > 0 ? waitingText : "Nothing is waiting for you"}>
            <Ico name="bell" />
            {waiting > 0 && <span className="ds-reddot" data-testid="ds-bell-dot" />}
          </Link>
          <div className="ds-profile" ref={menuRef}>
            <button type="button" className="ds-profilebtn" data-testid="ds-profile" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu((m) => !m)}>
              <span className="ds-avatar">{initials(principal.label)}</span>
              <Ico name="chevdown" />
            </button>
            {menu && (
              <div className="ds-menu" role="menu">
                <div className="head">
                  <b>{principal.label}</b>
                  <small>{principal.roles.map(roleLabel).join(", ")}</small>
                  <small>Organisation: {principal.tenant_id}. You see only what it owns.</small>
                </div>
                <button
                  type="button"
                  role="menuitem"
                  data-testid="switch-persona"
                  onClick={async () => {
                    // Awaited on purpose: navigating before the real logout finishes server-side left a window where a reload landed back on the
                    // still-valid old session instead of the sign-in screen.
                    await clear();
                    navigate("/auth/login");
                  }}
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>
      <nav className="ds-crumbs" aria-label="Breadcrumb" data-testid="ds-crumbs">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <span key={`${c.label}-${i}`} className="ds-crumb">
              {i > 0 && <Ico name="chevron" />}
              {last ? (
                <b aria-current="page">{c.label}</b>
              ) : c.to ? (
                <Link to={c.to}>{c.label}</Link>
              ) : (
                <span>{c.label}</span>
              )}
            </span>
          );
        })}
      </nav>

      {/* ------------------------------------------------------------ the page */}
      <main className="ds-main">
        <div className="ds-content">
          {closing?.phase === "retiring" && (
            <div role="status" data-testid="closing-banner" className="ds-banner">
              <strong>This organisation is closing down.</strong> Nothing can be added or changed. You can still read, and it can be cancelled until{" "}
              {when(closing.retiring_until)} ({closing.days_left} days left).{" "}
              <Link to="/closing" style={{ textDecoration: "underline" }}>
                See where it stands
              </Link>
            </div>
          )}
          {children}
        </div>
      </main>
      <ToastHost />
    </div>
  );
}
