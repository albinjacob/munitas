/**
 * U1 to U3, U6 and U7: does the console tell the truth about the platform?
 *
 * Every assertion compares what a screen shows against what the API says, so a
 * screen that renders confidently from stale or invented data fails. Per
 * assertion, never in aggregate.
 *
 * U4, U5 and U8 need the review screens and the deep links, which are stage 3
 * and later. They are declared here as skipped with a stated reason rather than
 * omitted, because a suite that silently lacks a test looks identical to one
 * where the test passes.
 */

import { expect, test } from "@playwright/test";
import { actingHeaders, bearerFor, loginAs } from "./auth-helpers";
import { CLASS_LABEL } from "../src/api/types";
import { API_BASE } from "../config/ports";

const API = API_BASE;

/**
 * The organisation the personas below belong to.
 *
 * Every screen scopes what it shows to the acting person's tenant, so a test
 * comparing a screen against the whole platform is comparing two different
 * questions. It used to pass only because there was one tenant, and it started
 * failing the moment verification moved into its own, which is the test working.
 */
const TENANT = "health";

/**
 * Every route is behind a real login now, so each test signs in first. The
 * administrator is used because these assertions are about whether the
 * console reports the platform truthfully, and that persona has a reason to
 * look at all of it.
 */
test.beforeEach(async ({ page }) => {
  await loginAs(page, "ops-priya");
});

// Every read endpoint now requires a real session and answers only for the
// caller's own tenant (a client-supplied tenant_id in the query string is
// ignored). These bare fetches used to work unauthenticated against any
// tenant_id; now each one needs a session that actually belongs to the
// tenant it is reading. Cached rather than logged in on every call, since a
// fresh Kratos login per assertion would make this suite far slower for no
// benefit -- the identity is fixed for the whole file.
let healthAuthPromise: Promise<Record<string, string>> | null = null;
function healthAuth(): Promise<Record<string, string>> {
  healthAuthPromise ??= bearerFor("ops-priya");
  return healthAuthPromise;
}
let canaryAuthPromise: Promise<Record<string, string>> | null = null;
function canaryAuth(): Promise<Record<string, string>> {
  canaryAuthPromise ??= bearerFor("canary-engineer");
  return canaryAuthPromise;
}

async function api<T>(path: string, headers?: Record<string, string>): Promise<T> {
  const response = await fetch(API + path, { headers: headers ?? (await healthAuth()) });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}

/**
 * Set up a precondition through the API, rather than waiting for one to exist.
 *
 * A test that only runs when the platform happens to be in the right state
 * reports a pass it has not earned on every other run, and a named skip is not
 * much better when the skip is the normal case.
 */
async function post<T>(path: string, body: unknown, headers?: Record<string, string>): Promise<T> {
  const response = await fetch(API + path, {
    method: "POST",
    headers: { "content-type": "application/json", ...(headers ?? (await actingHeaders("POST", path, body))) },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`${path} returned ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

interface VersionRow {
  dataset_version_id: string;
  sealed_class: string;
  current_class: string;
  storage_prefix: string;
  content_hash: string;
  dataset_name: string;
}

test.describe("U1: the console renders real platform state", () => {
  test("overview counts match the API", async ({ page }) => {
    const summary = await api<{ datasets: number; versions: number; denials: number }>(
      `/summary?tenant_id=${TENANT}`,
    );
    await page.goto("/");

    // Wait for content that only exists once the data has loaded. An earlier
    // version of this waited for the text "Datasets", which is also the nav
    // link, so it was satisfied by the header while the page still said
    // "Loading" and the assertions below ran against nothing.
    await expect(page.getByTestId("overview-stats")).toBeVisible();

    // Compare the rendered figures with a fresh call, so a cached or invented
    // number fails rather than looking plausible.
    const body = await page.textContent("main");
    expect(body).toContain(String(summary.datasets));
    expect(body).toContain(String(summary.versions));
    expect(body).toContain(String(summary.denials));
  });

  test("version detail matches the API for the same version", async ({ page }) => {
    const versions = await api<VersionRow[]>(
      `/dataset-versions?limit=50&tenant_id=${TENANT}`,
    );
    const promoted = versions.find((v) => v.sealed_class !== v.current_class);
    test.skip(!promoted, "no promoted version exists yet; run the pipeline first");

    const full = await api<VersionRow>(`/dataset-versions/${promoted!.dataset_version_id}`);
    await page.goto(`/versions/${promoted!.dataset_version_id}`);

    // Mapped through CLASS_LABEL rather than compared raw: the badge shows the
    // reader's words. The exact class the API returned is still asserted
    // literally, by the data-testid the badge carries.
    await expect(page.getByTestId("sealed-class")).toHaveText(CLASS_LABEL[full.sealed_class]);
    await expect(page.getByTestId("current-class")).toHaveText(CLASS_LABEL[full.current_class]);
    await expect(
      page.getByTestId("sealed-class").getByTestId(`class-badge-${full.sealed_class}`),
    ).toBeVisible();
    await expect(
      page.getByTestId("current-class").getByTestId(`class-badge-${full.current_class}`),
    ).toBeVisible();
    await expect(page.getByTestId("storage-prefix")).toHaveText(full.storage_prefix);
  });
});

test.describe("U2: promotion is shown as a mask, not a move", () => {
  test("sealed class, current class and an unchanged prefix are all visible", async ({
    page,
  }) => {
    const versions = await api<VersionRow[]>(
      `/dataset-versions?limit=50&tenant_id=${TENANT}`,
    );
    const promoted = versions.find((v) => v.sealed_class !== v.current_class);
    test.skip(!promoted, "no promoted version exists yet; run the pipeline first");

    await page.goto(`/versions/${promoted!.dataset_version_id}`);

    const sealed = await page.getByTestId("sealed-class").textContent();
    const current = await page.getByTestId("current-class").textContent();
    const prefix = await page.getByTestId("storage-prefix").textContent();

    // The two classes differ, which is what makes this a promotion.
    expect(sealed).not.toEqual(current);

    // And the prefix is the one the API reports, unchanged by the promotion.
    // If promotion moved bytes, the prefix would encode the new class.
    expect(prefix).toBe(promoted!.storage_prefix);
    expect(prefix).not.toContain(current!);

    await expect(page.getByText("The data was never copied.")).toBeVisible();
  });
});

test.describe("U3: denials are visible by default", () => {
  test("the audit log shows denied decisions with no filter applied", async ({
    page,
  }) => {
    // The most recent denial only: the audit log now paginates at 15 rows,
    // so a denial buried further back than that in the unfiltered "all"
    // view would not be the one this test happens to check for -- the
    // newest-first sort both this fetch and the page itself use means the
    // single most recent denial is always on page 1, filtered or not.
    const { decisions } = await api<{ decisions: { allowed: boolean; reasons: string[] }[] }>(
      `/access-decisions?limit=1&allowed=false`,
    );
    test.skip(!decisions.length, "no denials recorded yet; run verify/v4_class_enforced.py");
    const denial = decisions[0];

    await page.goto("/audit");
    const deniedRows = page.locator('[data-denied="true"]');
    await expect(deniedRows.first()).toBeVisible();
    expect(await deniedRows.count()).toBeGreaterThan(0);

    // A denial without its reason is indistinguishable from a bug.
    const firstReason = denial.reasons?.[0];
    if (firstReason) {
      await expect(page.getByText(firstReason, { exact: false }).first()).toBeVisible();
    }
  });
});

test.describe("U6: identity is honestly labelled", () => {
  for (const route of ["/", "/datasets", "/audit"]) {
    test(`the unauthenticated banner is present on ${route}`, async ({ page }) => {
      await page.goto(route);
      const banner = page.getByTestId("unauthenticated-banner");
      await expect(banner).toBeVisible();
      await expect(banner).toContainText("Nobody is signed in");
      // There must be no way to dismiss it.
      expect(await banner.locator("button").count()).toBe(0);
    });
  }

  test("the sidebar names who you are acting as", async ({ page }) => {
    await page.goto("/");
    // Choosing and switching are covered by U10. This asserts the narrower
    // thing: whichever route you are on, the console says whose name your
    // requests would carry.
    await expect(page.getByTestId("current-persona")).toContainText("Priya");
    await page.goto("/audit");
    await expect(page.getByTestId("current-persona")).toContainText("Priya");
  });

  test("and which organisation you are seeing, on every route", async ({ page }) => {
    // Every list is filtered to this organisation. A console that filters by
    // something it never names lets an empty screen read as an empty platform,
    // which is the same failure as a screen that overstates what it holds.
    for (const route of ["/", "/datasets", "/audit"]) {
      await page.goto(route);
      await expect(page.getByTestId("current-tenant")).toHaveText(
        new RegExp(TENANT),
      );
    }
  });

  test("and which role you hold, since that is what decides what you can do", async ({
    page,
  }) => {
    await page.goto("/");
    // Priya holds two. Both are named rather than the first one standing in for
    // the rest, because the second is why the platform screens are reachable.
    await expect(page.getByTestId("current-roles")).toContainText(
      "Platform administrator",
    );
    await expect(page.getByTestId("current-roles")).toContainText(
      "Support and reliability",
    );
  });

});

test.describe("U34: a decision does not disappear once it is made", () => {
  test("a custodian can see what they already granted or refused", async ({
    page,
  }) => {
    // The queue lists only what is waiting, correctly. But a decided request
    // used to vanish from the console entirely, so the person accountable for
    // the data had no way to see what they had allowed, to whom, or whether it
    // had run out. Accountability that cannot be reviewed is not accountability.
    // `exclude_pending=true&limit=15` matches CustodianHome's own first page
    // exactly: the console now paginates this list, so only page 1 is on
    // screen at once, not every decision Cardiology has ever made.
    const { lease_requests: decided } = await api<{ lease_requests: { state: string; decided_by: string | null }[] }>(
      `/lease-requests?custodian=cust-hartley&tenant_id=${TENANT}&exclude_pending=true&limit=15`,
    );
    test.skip(!decided.length, "nothing has been decided in Cardiology yet");

    await loginAs(page, "cust-hartley");

    const list = page.getByTestId("decided-requests");
    await expect(list).toBeVisible();
    expect(await list.locator("[data-decided]").count()).toBe(decided.length);

    // The outcome is on the row, so a refusal cannot be mistaken for a grant.
    const outcomes = await list.locator("[data-decided]").evaluateAll((rows) =>
      rows.map((r) => r.getAttribute("data-outcome")),
    );
    expect(outcomes.every((o) => o === "approved" || o === "rejected")).toBe(true);
  });
});

test.describe("U28: a deep link cannot cross organisations", () => {
  test("another organisation's version does not render for you", async ({
    page,
  }) => {
    // Deep links were the one route into this console that no list had filtered
    // on the way past, so they showed a version's dataset name, class and
    // release history regardless of who owned it, on a screen that tells you on
    // every route that you see only your own organisation's data.
    const canary = await api<{ dataset_version_id: string; dataset_name: string }[]>(
      "/dataset-versions?limit=1&tenant_id=canary",
      await canaryAuth(),
    );
    test.skip(!canary.length, "the canary organisation holds no versions");

    await page.goto(`/versions/${canary[0].dataset_version_id}`);

    await expect(page.getByTestId("failure")).toBeVisible();
    // And specifically, nothing about the version leaked onto the page first.
    await expect(page.getByTestId("sealed-class")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText(canary[0].dataset_name);
  });
});

test.describe("U32: a version whose files were freed says so", () => {
  test("the page explains what happened rather than looking broken", async ({
    page,
  }) => {
    // Reclaimed versions only exist in the canary organisation, because a
    // production tenant cannot be reclaimed at all. So this acts as somebody
    // from that organisation rather than deep-linking into it from another,
    // which the test above proves does not work. The screen is the same screen.
    const reclaimed = await api<{ dataset_version_id: string }[]>(
      "/dataset-versions?limit=200&tenant_id=canary",
      await canaryAuth(),
    ).then((rows) =>
      Promise.all(
        rows.slice(0, 40).map(async (r) =>
          api<{ reclaimed_at: string | null; dataset_version_id: string }>(
            `/dataset-versions/${r.dataset_version_id}?tenant_id=canary`,
            await canaryAuth(),
          ),
        ),
      ),
    );
    const target = reclaimed.find((v) => v.reclaimed_at);
    test.skip(!target, "nothing has been reclaimed yet; run scripts/admin/reclaim-storage.py");

    await loginAs(page, "canary-researcher");
    await page.goto(`/versions/${target!.dataset_version_id}`);
    await expect(page.getByTestId("current-tenant")).toContainText("canary");

    const notice = page.getByTestId("reclaimed-notice");
    await expect(notice).toBeVisible();

    // Two things the reader needs, and the second is the one that stops this
    // reading as a fault: the files are gone, and the record is not.
    await expect(notice).toContainText("The files are gone");
    await expect(notice).toContainText("Everything recorded about this version stays");

    // And the release history still renders, which is the claim made above.
    await expect(page.getByText("Release history")).toBeVisible();
  });
});

test.describe("U35: a grant names the version, not just the dataset", () => {
  test("the custodian's queue says which version and how sensitive", async ({
    page,
  }) => {
    // Access is leased against one version. Two versions of the same recordings
    // can sit at different sensitivities, because one went through
    // de-identification and the other did not, so a queue row naming only the
    // dataset asked a custodian to approve something whose shape was off screen.
    // This test makes the request it needs rather than hoping one is waiting.
    //
    // It was first pinned to Hartley, which skipped whenever Cardiology's queue
    // was empty. Reading the queue instead only moved the problem: it then
    // passed only when another test happened to run first and leave something
    // behind. Both versions reported a pass the suite had not earned, which is
    // the failure mode worth engineering against here.
    const versions = await api<
      { dataset_version_id: string; dataset_name: string }[]
    >(`/dataset-versions?tenant_id=${TENANT}&limit=200`);
    const audio = versions.find((v) => v.dataset_name === "consultation-audio");
    test.skip(!audio, "the health organisation holds no consultation-audio");

    // A workload asking for raw audio, which is what the pipeline's trainer
    // legitimately does, so the row reads as real work rather than as fixture.
    // Deliberately not the version U36 asks for, so the two do not collide.
    const created = await post<{ id: string }>("/leases/requests", {
      tenant_id: TENANT,
      principal: "svc-trainer",
      dataset_version_id: audio!.dataset_version_id,
      purpose: `queue rendering check ${Date.now()}`,
      justification: "asserting the queue names the version and its sensitivity",
      ttl_hours: 4,
    }, await bearerFor("eng-devi"));

    const { lease_requests: pending } = await api<{
      lease_requests: {
        id: string;
        version: number | null;
        current_class: string | null;
        custodian: string | null;
      }[];
    }>(`/lease-requests?state=pending&tenant_id=${TENANT}`);
    const row = pending.find((r) => r.id === created.id);
    expect(row, "the request this test filed is in the queue").toBeTruthy();
    expect(row!.custodian, "and a custodian owns it").toBeTruthy();

    await loginAs(page, row!.custodian!);

    const item = page
      .getByTestId("pending-requests")
      .locator(`[data-request="${row!.id}"]`);
    await expect(item).toBeVisible();
    // The version number as the API reports it, not merely some digit.
    await expect(item.getByTestId("version-name")).toContainText(
      `v${row!.version}`,
    );
    // And how sensitive it is now, which is what the decision turns on.
    if (row!.current_class) {
      await expect(
        item.getByTestId(`class-badge-${row!.current_class}`),
      ).toBeVisible();
    }
  });

  test("and so does the researcher's own list of what they hold", async ({
    page,
  }) => {
    const { leases } = await api<{ leases: { version: number | null }[] }>(
      `/leases?principal=sam-researcher&tenant_id=${TENANT}`,
    );
    test.skip(!leases.length, "Sam holds no access");

    await loginAs(page, "sam-researcher");

    const list = page.getByTestId("my-access");
    await expect(list).toBeVisible();
    const first = list.locator("[data-lease]").first();
    await expect(first.getByTestId("version-name")).toContainText(
      `v${leases[0].version}`,
    );
  });
});

test.describe("U36: a researcher can ask, and the ask reaches a custodian", () => {
  test("the form is on the version, and asking twice is refused before typing", async ({
    page,
  }) => {
    // The researcher's landing page told people to open a dataset and ask, and
    // then gave them nowhere to ask. This closes that loop end to end: the form
    // exists, submitting it creates a pending request, and that request appears
    // in the queue of the custodian who owns the data rather than nowhere.
    const versions = await api<
      { dataset_version_id: string; current_class: string }[]
    >(`/dataset-versions?tenant_id=${TENANT}&limit=200`);
    // Something Sam cannot already read and does not already hold. A version
    // they hold a lease on shows what they were granted instead of the form,
    // correctly, and a test aimed at it would be asserting the wrong screen.
    const held = await api<{ leases: { dataset_version_id: string; active: boolean }[] }>(
      `/leases?principal=sam-researcher&tenant_id=${TENANT}`,
    ).then(({ leases }) =>
      new Set(leases.filter((r) => r.active).map((r) => r.dataset_version_id)),
    );
    const target = versions.find(
      (v) => v.current_class !== "PUBLISHED" && !held.has(v.dataset_version_id),
    );
    test.skip(
      !target,
      "nothing in the health organisation is both restricted and unheld by Sam",
    );

    const { lease_requests: before } = await api<{
      lease_requests: { principal: string; dataset_version_id: string }[];
    }>(`/lease-requests?tenant_id=${TENANT}&state=pending`);
    const already = before.some(
      (r) =>
        r.dataset_version_id === target!.dataset_version_id &&
        r.principal === "sam-researcher",
    );
    // Declared as a skip rather than passing on a shorter path.
    //
    // The submit assertions below need Sam to have nothing waiting on this
    // version, and this test leaves one behind when it succeeds. Nothing here
    // can withdraw it, because the API has no route to and a custodian refusing
    // it would write that custodian's name against a decision they never made.
    // So a second run in a row cannot exercise the submit, and the honest
    // report of that is a skip naming the reason, not a green tick.
    test.skip(
      already,
      "Sam already has a request waiting on this version; clear it to run the submit path again",
    );

    await loginAs(page, "sam-researcher");
    await page.goto(`/versions/${target!.dataset_version_id}`);

    await expect(page.getByTestId("request-access")).toBeVisible();

    // Nothing can be sent empty. A justification is what the custodian reads.
    await expect(page.getByTestId("request-submit")).toBeDisabled();

    const purpose = `playwright check ${Date.now()}`;
    await page.getByTestId("request-purpose").fill(purpose);
    await page
      .getByTestId("request-justification")
      .fill("automated verification that the request reaches a custodian");
    await page.getByTestId("request-submit").click();

    await expect(page.getByTestId("request-sent")).toBeVisible();

    // It exists in the platform, under this person's name and this purpose.
    const { lease_requests: after } = await api<{
      lease_requests: { principal: string; purpose: string; custodian: string | null }[];
    }>(`/lease-requests?tenant_id=${TENANT}&state=pending`);
    const created = after.find((r) => r.purpose === purpose);
    expect(created, "the request was recorded").toBeTruthy();
    expect(created!.principal).toBe("sam-researcher");

    // And it landed in the queue of whoever owns that data, which is the whole
    // point. A request filed against nobody is indistinguishable from a request
    // that was lost.
    expect(created!.custodian, "a custodian owns what was asked for").toBeTruthy();

    // Asking again is refused before anything is typed, so a queue does not
    // fill with duplicates a custodian has to read to discover are the same.
    await page.reload();
    await expect(page.getByTestId("request-pending")).toBeVisible();
    await expect(page.getByTestId("request-access")).toHaveCount(0);
  });
});

test.describe("U37: bringing data in through the console", () => {
  // Registering a dataset is not reversible. A sealed version cannot be
  // deleted (that is V1), and a dataset cannot be deleted while a version
  // references it, so any dataset this suite registers stays forever.
  //
  // That is exactly why `canary` exists: an organisation for exactly this
  // kind of run, whose objects are reclaimed on a schedule while its rows
  // stay for good, same as everywhere else. Registering through `health`
  // instead would permanently grow the tenant that is supposed to stay a
  // clean worked example. This block acts as `canary-engineer` and
  // `canary-custodian`, never as a `health` persona.
  const CANARY = "canary";

  test("register, upload, seal, and land on the sealed version", async ({
    page,
  }) => {
    const name = `console-register-check-${Date.now()}`;

    await loginAs(page, "canary-engineer");
    await page.goto("/datasets/register");

    await expect(page.getByTestId("register-form")).toBeVisible();

    // No nav item reads as current here. Registering is reached from a
    // button on the Datasets page, not from its own nav entry, so the
    // Datasets link (with `end`, an exact match only) correctly does not
    // light up while on "/datasets/register" even though the path starts
    // with "/datasets": a sidebar claiming the reader was on the list page
    // while they are on the register form would be wrong in the other
    // direction.
    const active = page.locator("nav a.bg-slate-900");
    await expect(active).toHaveCount(0);

    // Nothing can be sent without an owning department.
    await expect(page.getByTestId("register-submit")).toBeDisabled();

    await page.getByTestId("register-name").fill(name);
    await page
      .getByTestId("register-department")
      .selectOption({ label: "Verification" });
    await expect(page.getByTestId("register-submit")).toBeEnabled();
    await page.getByTestId("register-submit").click();

    await expect(page.getByTestId("upload-step")).toBeVisible();

    await page
      .getByTestId("upload-files")
      .setInputFiles({
        name: "probe.bin",
        mimeType: "application/octet-stream",
        buffer: Buffer.from([1, 2, 3, 4, 5]),
      });
    await expect(page.getByTestId("uploaded-files")).toContainText("probe.bin");

    await expect(page.getByTestId("seal-submit")).toBeEnabled();
    await page.getByTestId("seal-submit").click();
    await expect(page.getByTestId("seal-done")).toBeVisible();

    // The redirect after sealing names a real version, not an undefined one.
    // This is not a hypothetical: it broke exactly this way the first time,
    // because the seal endpoint returns `id` and the console read
    // `dataset_version_id`, and nothing caught it until the browser was
    // driven through the flow rather than trusting the type-check.
    await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/, {
      timeout: 3000,
    });
    await expect(page.getByTestId("sealed-class")).toContainText(CLASS_LABEL.RAW);

    const registered = await api<{ datasets: { name: string }[] }>(
      `/datasets?tenant_id=${CANARY}&q=${name}`,
      await canaryAuth(),
    );
    expect(registered.datasets.some((d) => d.name === name)).toBe(true);
  });

  test("a claim waits for the custodian, and confirming it clears the queue", async ({
    page,
  }) => {
    const name = `console-claim-check-${Date.now()}`;

    // Registered directly through the API with a claimed sensitivity, so this
    // test is about the confirmation screen rather than about the form
    // already covered above.
    const department = await api<{ departments: { id: string; name: string }[] }>(
      `/organisation?tenant_id=${CANARY}`,
      await canaryAuth(),
    ).then((o) => o.departments.find((d) => d.name === "Verification")!);

    const registered = await post<{ id: string }>("/datasets/register", {
      tenant_id: CANARY,
      name,
      department_id: department.id,
      registered_by: "canary-engineer",
      provenance: "internal_regulated",
      declared_class: "UNDER_REVIEW",
      modality: [],
    });

    await loginAs(page, "canary-custodian");

    const arrival = page
      .getByTestId("awaiting-confirmation")
      .locator(`[data-arrival="${registered.id}"]`);
    await expect(arrival).toBeVisible();
    await expect(arrival).toContainText(name);
    await expect(arrival.getByTestId("class-badge-UNDER_REVIEW")).toBeVisible();

    await arrival.getByTestId(`confirm-${registered.id}`).click();
    await expect(arrival).toHaveCount(0);

    // Confirmed in the platform, not just off the screen. The queue endpoint
    // is the one that matters: it is what decides whether this claim still
    // blocks release, and its own query already excludes anything confirmed.
    const stillWaiting = await api<{ id: string }[]>(
      `/datasets/awaiting-confirmation?tenant_id=${CANARY}&custodian=canary-custodian`,
      await canaryAuth(),
    );
    expect(stillWaiting.some((d) => d.id === registered.id)).toBe(false);
  });

  test("selecting several files at once uploads and lists every one of them", async ({
    page,
  }) => {
    // Regression test for a real bug (flagged as task_c34fbf40): selecting
    // several files in one browser file-picker action fires one upload
    // mutation per file from the same shared upload hook. The fix reads each
    // upload's own result off the promise `mutateAsync()` returns, rather
    // than off an `onSuccess` callback passed into `mutate()` -- the latter
    // is attached to one shared observer, so a second call before the first
    // settles silently detaches the first upload's callback even though the
    // upload itself completes on the server. Before that fix, this test
    // reproduced the bug: one of the two files never appeared in the list.
    const name = `console-multi-upload-check-${Date.now()}`;

    await loginAs(page, "canary-engineer");
    await page.goto("/datasets/register");
    await page.getByTestId("register-name").fill(name);
    await page
      .getByTestId("register-department")
      .selectOption({ label: "Verification" });
    await page.getByTestId("register-submit").click();
    await expect(page.getByTestId("upload-step")).toBeVisible();

    await page.getByTestId("upload-files").setInputFiles([
      { name: "alpha.bin", mimeType: "application/octet-stream", buffer: Buffer.from([1]) },
      { name: "beta.bin", mimeType: "application/octet-stream", buffer: Buffer.from([2]) },
    ]);

    const list = page.getByTestId("uploaded-files");
    await expect(list).toContainText("alpha.bin");
    await expect(list).toContainText("beta.bin");

    await page.getByTestId("seal-submit").click();
    await expect(page.getByTestId("seal-done")).toContainText("2 files");
  });
});

test.describe("U41: who authorized this, and how long it lasts", () => {
  const CANARY = "canary";

  // A version whose dataset already has an owning department, found rather
  // than freshly registered: a lease request needs a custodian to land in
  // front of, and only an owned dataset has one.
  async function ownedVersion(): Promise<{ dataset_version_id: string }> {
    const datasets = await api<{
      datasets: { id: string; department_id: string | null }[];
    }>(`/datasets?tenant_id=${CANARY}&limit=500`, await canaryAuth());
    const owned = new Set(
      datasets.datasets.filter((d) => d.department_id).map((d) => d.id),
    );
    const versions = await api<{ dataset_version_id: string; dataset_id: string }[]>(
      `/dataset-versions?tenant_id=${CANARY}&limit=500`,
      await canaryAuth(),
    );
    const found = versions.find((v) => owned.has(v.dataset_id));
    if (!found) throw new Error("no owned canary version to test against");
    return found;
  }

  test("a custodian sees who asked, even when it was not the workload itself", async ({
    page,
  }) => {
    const version = await ownedVersion();
    const purpose = `console provenance check ${Date.now()}`;

    const created = await post<{ id: string }>("/leases/requests", {
      tenant_id: CANARY,
      principal: "canary-trainer",
      dataset_version_id: version.dataset_version_id,
      purpose,
      justification: "on-behalf-of rendering check",
      ttl_hours: 4,
    }, await bearerFor("canary-engineer"));

    await loginAs(page, "canary-custodian");

    const row = page
      .getByTestId("pending-requests")
      .locator(`[data-request="${created.id}"]`);
    await expect(row).toBeVisible();
    // Names the human who asked and the workload that will read, not one or
    // the other. "canary-trainer wants to read" would be true but would lose
    // who is accountable for having asked; "Canary engineer wants to read"
    // would be false, since the engineer will never be the one reading.
    await expect(row).toContainText("Canary engineer asked for canary-trainer to read");

    // A real click: approve_lease requires a real session
    // (platform/api/app/auth.py's current_session), and this suite signs
    // in for real, so the button the custodian would actually press is
    // what's exercised here, not a stand-in API call.
    await row.getByTestId(`approve-${created.id}`).click();
    await page.goto("/");

    const decided = page
      .getByTestId("decided-requests")
      .locator(`[data-decided="${created.id}"]`);
    await expect(decided).toBeVisible();
    await expect(decided).toContainText("Canary engineer asked for canary-trainer to read");
  });

  test("a standing grant reads as open until revoked, never as a date", async ({
    page,
  }) => {
    const version = await ownedVersion();
    const purpose = `console standing check ${Date.now()}`;

    const created = await post<{ id: string }>("/leases/requests", {
      tenant_id: CANARY,
      principal: "canary-trainer",
      dataset_version_id: version.dataset_version_id,
      purpose,
      justification: "standing lease rendering check",
      standing: true,
    }, await bearerFor("canary-engineer"));

    await loginAs(page, "canary-custodian");

    const row = page
      .getByTestId("pending-requests")
      .locator(`[data-request="${created.id}"]`);
    await expect(row).toBeVisible();
    // Says the shape of the grant, not a number of hours that does not apply
    // to it. A standing request has no requested_ttl_hours at all.
    await expect(row).toContainText("until revoked");

    // See the comment on the previous test: a real click, now that a real
    // session backs it.
    await row.getByTestId(`approve-${created.id}`).click();
    await page.goto("/");

    const decided = page
      .getByTestId("decided-requests")
      .locator(`[data-decided="${created.id}"]`);
    await expect(decided).toBeVisible();
    // Never a formatted date. A standing grant rendered with an expiry date
    // would be a screen claiming something the platform did not promise.
    await expect(decided).toContainText("Standing: open until somebody revokes it.");

    // Regression coverage for a real gap: approving a standing grant had a
    // console button, but nothing could end one afterwards except an API
    // call by hand. `useRevokeLease` + this "Revoke" button is the fix.
    //
    // Cancelling the confirm dialog first, to prove the guard is real and
    // not decorative: dismissing it must send no request at all. An in-app
    // dialog (ConfirmDialog.tsx), not window.confirm, since the raw browser
    // dialog was the one place in the console still breaking its own design
    // system, worst on exactly the actions that cannot be undone.
    const revokeButton = decided.getByRole("button", { name: "Revoke" });
    await expect(revokeButton).toBeVisible();
    await revokeButton.click();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(decided).toContainText("Standing: open until somebody revokes it.");

    // Accepting it does the real thing: the API call, the row updating, and
    // the button disappearing because the lease is no longer active.
    await revokeButton.click();
    await page.getByTestId("confirm-dialog-confirm").click();
    await expect(decided).toContainText("Withdrawn before it ran out.");
    await expect(revokeButton).toHaveCount(0);

    // Confirmed in the platform, not just off the screen.
    const { leases } = await api<{ leases: { purpose: string; revoked: boolean }[] }>(
      `/leases?tenant_id=${CANARY}&principal=canary-trainer`,
      await canaryAuth(),
    );
    const lease = leases.find((l) => l.purpose === purpose);
    expect(lease?.revoked).toBe(true);
  });
});

test.describe("U42: fetching a dataset directly from HuggingFace", () => {
  const CANARY = "canary";

  test("the platform fetches the files itself, and the claim needs no confirmation", async ({
    page,
  }) => {
    // Reachability checked first and named as a skip rather than a failure.
    // This is the one path in the console that depends on a service outside
    // the platform's own stack, and a network hiccup here is not a defect in
    // what was built.
    let reachable = true;
    try {
      const probe = await fetch("https://huggingface.co/api/datasets/xhluca/publichealth-qa", {
        signal: AbortSignal.timeout(5000),
      });
      reachable = probe.ok;
    } catch {
      reachable = false;
    }
    test.skip(!reachable, "huggingface.co was not reachable from this environment");

    const name = `console-hf-check-${Date.now()}`;

    const department = await api<{ departments: { id: string; name: string }[] }>(
      `/organisation?tenant_id=${CANARY}`,
      await canaryAuth(),
    ).then((o) => o.departments.find((d) => d.name === "Verification")!);

    await loginAs(page, "canary-engineer");
    await page.goto("/datasets/register");

    await page.getByTestId("register-name").fill(name);
    await page
      .getByTestId("register-department")
      .selectOption({ value: department.id });
    await page.getByTestId("provenance-external_public").check();
    // PUBLISHED is a real claim, not the safe default, and it should still need
    // no confirmation once the fetch below completes: the platform, not the
    // engineer, is what will have checked the origin.
    await page.getByTestId("register-class").selectOption("PUBLISHED");
    await page.getByTestId("register-submit").click();

    await expect(page.getByTestId("upload-step")).toBeVisible();
    await page.getByTestId("source-huggingface").click();

    await page.getByTestId("hf-repo-id").fill("xhluca/publichealth-qa");
    await page.getByTestId("hf-path").fill("data");
    await page.getByTestId("hf-fetch").click();

    // Eight real files, fetched from the real repository, not a fixture.
    // One row rather than eight, because the fetch is a background job: the
    // console is told what the job did, not handed each file as it lands.
    const fetched = page.getByTestId("uploaded-files").locator("li");
    await expect(fetched.first()).toContainText(
      "8 files from xhluca/publichealth-qa",
      { timeout: 20_000 },
    );
    // Labelled distinctly from an upload, since the platform did this one
    // itself rather than receiving bytes from the browser.
    await expect(fetched.first()).toContainText("Fetched");
    await expect(fetched.first()).toContainText("1060653 bytes");

    await page.getByTestId("seal-submit").click();
    await expect(page).toHaveURL(/\/versions\/[0-9a-f-]{36}$/, { timeout: 5000 });
    await expect(page.getByTestId("sealed-class")).toContainText(CLASS_LABEL.PUBLISHED);

    // The claim was never asserted; it was verified by the fetch. Confirmed
    // by checking the queue it would otherwise be sitting in.
    const waiting = await api<{ name: string }[]>(
      `/datasets/awaiting-confirmation?tenant_id=${CANARY}&custodian=canary-custodian`,
    );
    expect(waiting.some((d) => d.name === name)).toBe(false);
  });

  test("a repo that does not exist is refused, not a server error", async ({
    page,
  }) => {
    let reachable = true;
    try {
      const probe = await fetch("https://huggingface.co", {
        signal: AbortSignal.timeout(5000),
      });
      reachable = probe.ok;
    } catch {
      reachable = false;
    }
    test.skip(!reachable, "huggingface.co was not reachable from this environment");

    const name = `console-hf-missing-check-${Date.now()}`;
    const department = await api<{ departments: { id: string; name: string }[] }>(
      `/organisation?tenant_id=${CANARY}`,
      await canaryAuth(),
    ).then((o) => o.departments.find((d) => d.name === "Verification")!);

    await loginAs(page, "canary-engineer");
    await page.goto("/datasets/register");

    await page.getByTestId("register-name").fill(name);
    await page
      .getByTestId("register-department")
      .selectOption({ value: department.id });
    await page.getByTestId("register-submit").click();

    await expect(page.getByTestId("upload-step")).toBeVisible();
    await page.getByTestId("source-huggingface").click();
    await page
      .getByTestId("hf-repo-id")
      .fill("nobody/this-does-not-exist-on-huggingface-xyz");
    await page.getByTestId("hf-fetch").click();

    // A refusal with a reason, not a blank crash. The form stays usable
    // afterwards rather than being left in a broken half-fetched state.
    // The platform's own sentence, which names what to do about it rather
    // than reporting that something went wrong.
    await expect(page.locator('[role="alert"]')).toContainText(
      /connect a HuggingFace account|no public repo|could not/i,
      { timeout: 15_000 },
    );
    await expect(page.getByTestId("uploaded-files")).toHaveCount(0);
  });

  test("cancelling a running fetch stops it, without reporting it as failed", async ({
    page,
  }) => {
    let reachable = true;
    try {
      const probe = await fetch("https://huggingface.co/api/datasets/rajpurkar/squad", {
        signal: AbortSignal.timeout(5000),
      });
      reachable = probe.ok;
    } catch {
      reachable = false;
    }
    test.skip(!reachable, "huggingface.co was not reachable from this environment");

    const name = `console-hf-cancel-check-${Date.now()}`;
    const department = await api<{ departments: { id: string; name: string }[] }>(
      `/organisation?tenant_id=${CANARY}`,
      await canaryAuth(),
    ).then((o) => o.departments.find((d) => d.name === "Verification")!);

    await loginAs(page, "canary-engineer");
    await page.goto("/datasets/register");

    await page.getByTestId("register-name").fill(name);
    await page
      .getByTestId("register-department")
      .selectOption({ value: department.id });
    await page.getByTestId("register-submit").click();

    await expect(page.getByTestId("upload-step")).toBeVisible();
    await page.getByTestId("source-huggingface").click();
    // Several real files, chosen over a single-file repo so cancelling has
    // something to interrupt: a fetch that finished before Cancel could be
    // clicked would prove nothing about cancellation itself.
    await page.getByTestId("hf-repo-id").fill("rajpurkar/squad");
    await page.getByTestId("hf-fetch").click();

    await page.getByTestId("hf-cancel").click({ timeout: 15_000 });

    await expect(page.getByTestId("hf-job-status")).toContainText(
      "Cancelled before it finished",
      { timeout: 20_000 },
    );
    // Distinct from a failure: nothing went wrong, the console asked for it
    // to stop, and that distinction is what the status word itself, not
    // just the explanatory sentence, has to carry.
    await expect(page.getByTestId("hf-job-status")).not.toContainText(/fail/i);
  });
});

/**
 * The de-identification gate.
 *
 * Both tests need a decision that is still waiting on somebody, and there is
 * no endpoint that creates one: only a pipeline run does, which is the whole
 * point of the screen. So they find one and say plainly what is missing when
 * there is none, rather than passing on an empty page.
 */
interface GateRow {
  id: string;
  state: string;
  triggered_by: string | null;
  recommendation: string;
  recommendation_reason: string;
}

async function pendingGate(): Promise<GateRow | null> {
  const page = await api<{ gate_decisions: GateRow[] }>(
    `/gate-decisions?tenant_id=${TENANT}`,
  );
  return page.gate_decisions.find((r) => r.state === "pending") ?? null;
}

interface ActionRunRow {
  id: string;
  status: string;
  action_name: string;
  trigger_kind: "manual" | "scheduled";
  triggered_by: string | null;
  triggered_by_label: string | null;
  schedule_id: string | null;
}

test.describe("U81: action runs, with how each one started", () => {
  test("the list matches the API, including how the run was triggered", async ({
    page,
  }) => {
    // limit=15 matches ActionRuns.tsx's own page size: the console now
    // paginates this list, so only its first page is on screen at once,
    // not every run the tenant has ever recorded.
    const { action_runs: runs } = await api<{ action_runs: ActionRunRow[] }>(
      `/action-runs?tenant_id=${TENANT}&limit=15`,
    );
    test.skip(!runs.length, "no action runs recorded yet; run a pipeline first");

    await page.goto("/action-runs");
    await expect(page.getByTestId("action-run-rows")).toBeVisible();

    const rows = page.getByTestId("action-run-rows").locator("tr");
    await expect(rows).toHaveCount(runs.length);

    const first = runs[0];
    const firstRow = page.getByTestId(`action-run-${first.id}`);
    await expect(firstRow).toBeVisible();
    await expect(firstRow.getByTestId(`action-run-status-${first.id}`)).toHaveText(first.status);

    // How the run started is shown, not just that it ran: a schedule's own
    // id for a scheduled run, or who ran it by hand.
    const trigger = firstRow.getByTestId(`action-run-trigger-${first.id}`);
    if (first.trigger_kind === "scheduled") {
      await expect(trigger).toContainText(first.schedule_id!);
    } else {
      await expect(trigger).toContainText(first.triggered_by_label ?? first.triggered_by ?? "");
    }
  });
});

test.describe("U4: whoever started a run cannot clear it", () => {
  test("the actions are disabled, and say why", async ({ page }) => {
    const gate = await pendingGate();
    test.skip(
      gate === null,
      "no de-identification result is waiting on a decision in the health tenant",
    );
    test.skip(
      gate!.triggered_by === null,
      "the waiting result was started by a schedule, so nobody can be its own approver",
    );

    // Act as the person who started it. The policy refuses them, and the
    // screen has to say so before they press anything rather than after.
    await loginAs(page, gate!.triggered_by!);
    await page.goto(`/gates/${gate!.id}`);

    await expect(page.getByTestId("gate-blocked")).toContainText(
      /you started this run/i,
    );
    await expect(page.getByTestId("gate-promote")).toBeDisabled();
    await expect(page.getByTestId("gate-refuse")).toBeDisabled();
  });
});

test.describe("U5: a gate refusal explains itself", () => {
  test("the reason is on the page, not just the verdict", async ({ page }) => {
    const gate = await pendingGate();
    test.skip(
      gate === null,
      "no de-identification result is waiting on a decision in the health tenant",
    );

    await page.goto(`/gates/${gate!.id}`);

    // The verdict alone is not an explanation. Whatever the machine concluded,
    // its own words for why have to be visible.
    await expect(page.getByTestId("gate-recommendation")).toContainText(
      gate!.recommendation_reason,
    );
  });
});

test.describe("stage 4 and later", () => {
  test.skip("U8: deep links resolve", () => {
    // Needs the operate screens, which are stage 4.
  });
});
