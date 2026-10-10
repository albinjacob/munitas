/**
 * A page that is only useful to some roles.
 *
 * The menu already hides a link from the roles a page is not for, and that enforces nothing (see nav.tsx): somebody who types the address still reaches
 * the page. This is the other half. It says who the page is for, in one sentence, instead of drawing the whole page around refusals from the platform. The
 * platform still refuses the data itself whatever this shows, so this is for the reader, not for security.
 *
 * Who the page is for, and whether the signed-in person may open it, come from the platform's policy (`GET /screens`), the same answer the menu uses and the
 * same answer the API refuses with. Until that answer arrives the page shows nothing rather than flashing a refusal.
 */

import type { ReactNode } from "react";
import { useIdentity } from "../identity/IdentityContext";
import { useScreens, type Screen } from "../api/screens";
import { Section } from "./states";

export function RoleOnly({ path, title, children }: { path: string; title: string; children: ReactNode }) {
  const { principal } = useIdentity();
  const screens = useScreens(Boolean(principal));
  if (!screens.data) return null;
  const screen: Screen | undefined = Object.values(screens.data).find((s) => s.path === path);
  if (screen && !screen.open) {
    return (
      <div>
        <Section level="page" title={title}>
          <p className="text-sm text-slate-700" data-testid="role-only-notice">This page is for {screen.who}.</p>
        </Section>
      </div>
    );
  }
  return <>{children}</>;
}
