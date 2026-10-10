/**
 * The frame around the screens shown before anybody is signed in, and the notice for an organisation that has closed down to its people: the same look as
 * the console, the saved theme, and a plain bar with the name instead of the navigation, because none of the console's screens are open to them.
 */

import type { ReactNode } from "react";
import "./shell.css";
import { PATHS } from "./Ico";
import { UnauthenticatedBanner } from "../identity/IdentityBar";
import { useTheme } from "./useStored";

export function PublicFrame({ children, banner = true }: { children: ReactNode; banner?: boolean }) {
  const [theme, setTheme] = useTheme();
  return (
    <div className="ds ds-public" data-theme={theme} data-testid="ds-public">
      {banner && <UnauthenticatedBanner />}
      <header className="ds-publicbar">
        <span className="ds-logo">M</span>
        <div>
          <b>Munitas</b>
          <small>Governance console</small>
        </div>
        <button
          type="button"
          className="ds-switch"
          style={{ marginLeft: "auto" }}
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
      </header>
      <main className="ds-publicmain">{children}</main>
    </div>
  );
}
