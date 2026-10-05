/**
 * U11, U13 and U14: the persona changes what the console says.
 *
 * U9 and U10 used to live here too: "a fresh visitor lands on the chooser"
 * and "the choice is remembered and reversible". Both were claims about the
 * picker itself (a locally-picked identity in localStorage, proving
 * nothing) and stopped being true, not just differently implemented, once
 * every session-gated endpoint required a real Kratos session and the
 * picker was retired. real-auth.spec.ts's U53 already covers what
 * replaced them: an unresolved visitor lands on `/auth/login`, not a
 * chooser, and a real session survives a reload the same way the picker's
 * choice did.
 *
 * U14 is deliberately mechanical. It reads docker-compose.yml and fails when a
 * service exists that the home page does not name, so the map cannot quietly go
 * stale. A map that drifts from the system it describes is worse than no map,
 * because it is believed.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { bearerFor, loginAs } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const API = API_BASE;

test.describe("U11: the persona changes what the console says", () => {
  test("each persona lands on the thing they came for", async ({ page }) => {
    // A custodian is here to decide. That is the first thing on their page --
    // the section heading, not the stats card that also happens to repeat
    // its wording, which is why this checks the heading role specifically.
    await loginAs(page, "cust-hartley");
    await expect(
      page.getByRole("heading", { name: "Waiting for your decision" }),
    ).toBeVisible();

    // A researcher is here to get data, and is told how to ask for the rest.
    await loginAs(page, "sam-researcher");
    await expect(page.getByText("What you can read")).toBeVisible();
    expect(await page.getByText("Waiting for your decision").count()).toBe(0);
  });
});

test.describe("U13: the administrator sees everything and reads nothing extra", () => {
  test("the services view lists every workload", async ({ page }) => {
    await loginAs(page, "ops-priya");
    await page.goto("/services");
    for (const id of ["health-pipeline", "svc-trainer", "agent-triage-1"]) {
      await expect(page.getByTestId(`service-${id}`)).toBeVisible();
    }
  });

  test("the administrator is refused raw data by the policy engine", async () => {
    // In Priya's own organisation. Any raw version would do for the class rule,
    // but one belonging to somebody else is refused for the tenant instead, and
    // the assertion below is specifically about the class.
    const priya = await bearerFor("ops-priya");
    const versions = await fetch(
      `${API}/dataset-versions?current_class=RAW&limit=1&tenant_id=health`,
      { headers: priya },
    ).then((r) => r.json());
    test.skip(!versions.length, "no RAW version exists in health");

    const response = await fetch(`${API}/credentials`, {
      method: "POST",
      headers: { "content-type": "application/json", ...priya },
      body: JSON.stringify({
        principal: "ops-priya",
        principal_kind: "human",
        roles: ["platform_admin"],
        tenant_id: versions[0].tenant_id,
        dataset_version_id: versions[0].dataset_version_id,
        purpose: "investigating a failure",
      }),
    });
    // Running the system is not a licence to read what is in it.
    expect(response.status).toBe(403);
    const body = await response.json();
    expect(body.detail.reasons.join(" ")).toContain("no role reaches class RAW");
  });
});

test.describe("U14: the component map is complete", () => {
  test("every Compose service appears on the home page", async ({ page }) => {
    const compose = readFileSync(
      join(process.cwd(), "..", "docker-compose.yml"),
      "utf8",
    );

    // Service names are the two-space-indented keys under `services:`, stopping
    // at the next top-level key. Without that bound this also collects the
    // entries under `volumes:` and `networks:`, which are not components and
    // would make the test fail for the wrong reason.
    const afterServices = compose.split(/^services:$/m)[1] ?? "";
    const servicesBlock = afterServices.split(/^[a-z]/m)[0];
    const declared = [...servicesBlock.matchAll(/^ {2}([a-z][a-z0-9-]*):$/gm)].map(
      (m) => m[1],
    );
    expect(declared.length).toBeGreaterThan(5);

    await loginAs(page, "ops-priya");
    const mapped = await page
      .getByTestId("component-map")
      .locator("[data-service]")
      .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-service")));

    const missing = declared.filter((s) => !mapped.includes(s));
    expect(missing, `not on the home page: ${missing.join(", ")}`).toEqual([]);
  });
});
