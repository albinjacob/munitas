/**
 * U7: the identity seam is real.
 *
 * Checked by reading the source rather than by exercising the app, because the
 * claim is structural: identity must live in exactly one place, so that
 * replacing the fake with Keycloak or another provider is a change to one
 * directory. That replacement has now happened (Ory Kratos,
 * identity/IdentityContext.tsx): both tests below still hold. A previous
 * version of this file checked a second mode, a locally-picked id in
 * localStorage kept for local exploration without Kratos running; it was
 * retired once every session-gated endpoint required a real session
 * regardless, so there is only one mode to check now.
 *
 * A runtime test cannot catch either claim. A component reading the principal
 * from localStorage directly would behave correctly today and break silently
 * the moment identity moved to a different provider, because it would keep
 * serving a stale value instead of a real session; a component that quietly
 * assumed `authenticated` could be true without a verified session would
 * misreport the one claim this whole mechanism exists to make honestly.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { expect, test } from "@playwright/test";

const SRC = join(process.cwd(), "src");
const SEAM = join("src", "identity");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) return sourceFiles(full);
    return /\.(ts|tsx)$/.test(full) ? [full] : [];
  });
}

test("only the identity seam knows who is acting", () => {
  const offenders: string[] = [];

  for (const file of sourceFiles(SRC)) {
    const rel = relative(process.cwd(), file);
    if (rel.startsWith(SEAM + sep) || rel === SEAM) continue;

    const text = readFileSync(file, "utf8");

    // Reaching into storage for the acting principal bypasses the seam.
    if (/localStorage\s*\.\s*(get|set)Item\s*\(\s*["'`]munitas\.actingAs/.test(text)) {
      offenders.push(`${rel}: reads the acting principal from localStorage`);
    }
    // Importing the fixture list outside the seam means a second source of who
    // exists, which will drift from the first.
    if (/from\s+["'][^"']*identity\/principals["']/.test(text)) {
      offenders.push(`${rel}: imports the principal fixtures directly`);
    }
  }

  expect(offenders, offenders.join("\n")).toEqual([]);
});

test("the seam is honest about authentication", () => {
  // This assertion used to be `expect(context).toMatch(/authenticated:\s*false/)`,
  // written when the answer was unconditionally false and meant to force a
  // conscious rewrite, not a silent deletion, the moment that stopped being
  // true. Real authentication (Ory Kratos, platform/api/app/auth.py) exists
  // now, so `authenticated` has to come from a real, server-verified session,
  // not be asserted.
  const context = readFileSync(join(SRC, "identity", "IdentityContext.tsx"), "utf8");

  // Checking the import rather than only the assignment means a future
  // refactor that renamed the call still has to keep sourcing it from the
  // real session-check module, not invent a second source of truth.
  expect(context).toMatch(/import\s*\{[^}]*currentSession[^}]*\}\s*from\s*["']\.\/kratos["']/);

  expect(context).toMatch(/authenticated:\s*Boolean\(session\.data\)/);
});
