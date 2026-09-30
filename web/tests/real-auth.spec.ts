/**
 * U53: real login is the front door. The only one.
 *
 * This file predates the console's other suites having a real login flow of
 * their own to reuse, so it keeps its own small `loginAs` here rather than
 * importing ./auth-helpers's: this one takes an email and needs to submit
 * a deliberately wrong password in one test, which the shared helper (built
 * for "sign in as this seeded identity, no exceptions") does not support.
 *
 * Uses `cust-hartley`, seeded by infra/kratos/seed-identities.py, the same
 * identity verify/v53_real_authentication.py exercises against the API
 * directly. This is the same claim, driven through the real browser instead.
 */

import { expect, test, type Page } from "@playwright/test";
import { bearerFor } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const EMAIL = "hartley@health.example";
const PASSWORD = "dev-password-not-for-production";

// Shared with the lease- and agent-run-decision tests below: every seeded
// identity uses this one password (infra/kratos/seed-identities.py).
const RESEARCHER_EMAIL = "sam@health.example";
const DEVI_EMAIL = "devi@health.example";
// canary-engineer holds the same pipeline_operator role Devi does, in the
// tenant that exists for exactly this: infra/postgres/seed-canary.sql's own
// header records that every verify script used to write into the same
// tenant the demonstration organisation lives in, "so a console listing
// datasets showed seventy fixtures beside two real ones and none of them
// could be removed... the answer was never to clean up afterwards. It was
// to run the tests somewhere else." U60 below registers and seals a fresh
// dataset per run and never deletes it, so it belongs there too -- canary's
// own reclaim schedule is what cleans these up, not a teardown here.
const CANARY_ENGINEER_EMAIL = "engineer@canary.example";

async function loginAs(page: Page, email: string): Promise<void> {
  await page.goto("/auth/login");
  await page.getByTestId("login-identifier").fill(email);
  await page.getByTestId("login-password").fill(PASSWORD);
  await page.getByTestId("login-submit").click();
  // A real round trip, not a mock: Kratos's own login call, then the
  // console's session fetch and its own render. The default 5s expect
  // timeout is tight enough for this to occasionally read as a failed login
  // when the real cause is a slow-but-correct backend under load (this
  // machine's Postgres is a known slow point -- see CLAUDE.md/
  // SESSION_STATUS.md), not a broken one; 15s gives that real chain room
  // without masking an actually-broken login, which would never resolve.
  await expect(page.getByTestId("current-persona")).toBeVisible({ timeout: 15000 });
}

test.describe("U53: the front door is a real login", () => {
  test("an unresolved visitor lands on /auth/login", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveURL(/\/auth\/login$/);
    await expect(page.getByTestId("login-form")).toBeVisible();
  });

  test("the banner tells the truth", async ({ page }) => {
    await page.goto("/auth/login");
    const banner = page.getByTestId("unauthenticated-banner");
    await expect(banner).toContainText("Nobody is signed in");
    await expect(banner).toContainText("Sign in to continue");
    // The old picker's claim ("you can act as anyone") must never come back;
    // it would be false the moment a real login is the only door.
    await expect(banner).not.toContainText("act as anyone");
  });

  test("a deep link still goes to the real front door", async ({
    page,
  }) => {
    await page.goto("/audit");
    await expect(page).toHaveURL(/\/auth\/login$/);
  });

  test("the wrong password is refused, and the form stays usable", async ({ page }) => {
    await page.goto("/auth/login");
    await page.getByTestId("login-identifier").fill(EMAIL);
    await page.getByTestId("login-password").fill("not-the-real-password");
    await page.getByTestId("login-submit").click();

    await expect(page.getByTestId("login-error")).toBeVisible();
    await expect(page).toHaveURL(/\/auth\/login$/);

    // A second attempt, with the right password, still works: the failed
    // attempt must not have left the flow (in particular its csrf token) in
    // a state the next submit can't recover from.
    await page.getByTestId("login-identifier").fill(EMAIL);
    await page.getByTestId("login-password").fill(PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("current-persona")).toContainText("Hartley");
  });

  test("the right password resolves a real identity and lands on Home", async ({
    page,
  }) => {
    await page.goto("/auth/login");
    await page.getByTestId("login-identifier").fill(EMAIL);
    await page.getByTestId("login-password").fill(PASSWORD);
    await page.getByTestId("login-submit").click();

    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByTestId("current-persona")).toContainText("Hartley");
    await expect(page.getByTestId("current-tenant")).toContainText("health");
    // Once resolved, the banner is gone; U6's "present on every route"
    // claim is specifically about the unresolved state, not every state.
    await expect(page.getByTestId("unauthenticated-banner")).toHaveCount(0);
  });

  test("the session survives a reload", async ({
    page,
  }) => {
    await page.goto("/auth/login");
    await page.getByTestId("login-identifier").fill(EMAIL);
    await page.getByTestId("login-password").fill(PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("current-persona")).toContainText("Hartley");

    await page.reload();
    await expect(page.getByTestId("current-persona")).toContainText("Hartley");
  });

  test("signing out ends the real session, not just the local state", async ({
    page,
  }) => {
    await page.goto("/auth/login");
    await page.getByTestId("login-identifier").fill(EMAIL);
    await page.getByTestId("login-password").fill(PASSWORD);
    await page.getByTestId("login-submit").click();
    await expect(page.getByTestId("current-persona")).toContainText("Hartley");

    await page.getByTestId("switch-persona").click();
    await expect(page).toHaveURL(/\/auth\/login$/);

    // The claim that matters: reloading does not silently sign back in.
    // A local-only "forget" would pass the URL check above and fail this one.
    await page.reload();
    await expect(page).toHaveURL(/\/auth\/login$/);
    await expect(page.getByTestId("login-form")).toBeVisible();
  });
});

const API = API_BASE;

async function apiGet<T>(path: string, headers: Record<string, string>): Promise<T> {
  const response = await fetch(API + path, { headers });
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json() as Promise<T>;
}

/**
 * A sealed dataset version owned by the department this custodian id runs.
 *
 * `/organisation` and `/datasets` both require a real session (item 24's
 * read-endpoint auth sweep), so this reads as the custodian whose own
 * department it is looking up -- their own data either way, not a
 * privilege this test grants them. This function used to run before either
 * test identity signed in, back when both endpoints were open; that
 * stopped being true and this call went uncaught until now, because this
 * file was never part of the later dedicated sweep of `verify/`'s own
 * scripts with the same shape of gap.
 */
async function ownedVersion(custodianId: string): Promise<string> {
  const auth = await bearerFor(custodianId);
  const org = await apiGet<{ departments: { id: string; custodian: string }[] }>(
    "/organisation?tenant_id=health",
    auth,
  );
  const dept = org.departments.find((d) => d.custodian === custodianId);
  if (!dept) throw new Error(`${custodianId} owns no department in health`);

  const datasets = await apiGet<{
    datasets: { id: string; department_id: string | null; version_count: number }[];
  }>("/datasets?tenant_id=health", auth);
  const owned = datasets.datasets.find(
    (d) => d.department_id === dept.id && d.version_count > 0,
  );
  if (!owned) throw new Error(`${custodianId}'s department has no sealed dataset`);

  const versions = await apiGet<{ dataset_version_id: string }[]>(
    `/datasets/${owned.id}/versions`,
    auth,
  );
  if (!versions.length) throw new Error(`dataset ${owned.id} has no versions`);
  return versions[0].dataset_version_id;
}

test.describe("U56: the lease decision endpoints require a real session", () => {
  test("a custodian can grant a request a researcher filed, signed in for real", async ({
    page,
    browser,
  }) => {
    const versionId = await ownedVersion("cust-hartley");

    // The researcher's session files the request. A separate browser context,
    // not just a second page, because each needs its own cookie jar.
    const researcherContext = await browser.newContext();
    const researcherPage = await researcherContext.newPage();
    await loginAs(researcherPage, RESEARCHER_EMAIL);
    const filed = await researcherPage.request.post(`${API}/leases/requests`, {
      data: {
        tenant_id: "health",
        principal: "sam-researcher",
        dataset_version_id: versionId,
        purpose: "U56 real-session check",
        justification: "proving the migrated endpoint works end to end",
        ttl_hours: 4,
      },
    });
    expect(filed.ok()).toBe(true);
    const { id: requestId } = await filed.json();
    await researcherContext.close();

    // Hartley, signed in for real, grants it through the console UI.
    await loginAs(page, EMAIL);
    await page.goto("/");
    await expect(page.getByTestId("pending-requests")).toBeVisible();
    await page.getByTestId(`approve-${requestId}`).click();
    await expect(page.getByTestId(`approve-${requestId}`)).toHaveCount(0);
  });

  test("granting is refused without a real session", async ({ request }) => {
    const versionId = await ownedVersion("cust-hartley");
    const filed = await request.post(`${API}/leases/requests`, {
      data: {
        tenant_id: "health",
        principal: "cust-hartley",
        dataset_version_id: versionId,
        purpose: "U56 unauthenticated check",
        justification: "should be refused before this even matters",
        ttl_hours: 4,
      },
    });
    expect(filed.status()).toBe(401);
  });
});

test.describe("U56: agent deploy/start-run/approve-run require a real session", () => {
  test("deploying and starting a run works end to end, signed in for real", async ({
    page,
  }) => {
    await loginAs(page, DEVI_EMAIL);
    const { agents } = await page.request
      .get(`${API}/agents?tenant_id=health`)
      .then((r) => r.json());
    test.skip(!agents.length, "no agent registered in health to deploy a version of");
    const agent = agents[0];

    const detail = await page.request
      .get(`${API}/agents/${agent.id}?tenant_id=health`)
      .then((r) => r.json());
    // The Deploy button only renders for a version that is not already
    // active (AgentDetail.tsx), so the test needs one that genuinely isn't.
    const activeId = detail.active_version?.agent_version_id;
    const version = (detail.versions ?? []).find((v: { id: string }) => v.id !== activeId);
    test.skip(!version, "this agent has no non-active version to deploy");

    await page.goto(`/agents/${agent.id}`);
    await page.getByTestId(`deploy-version-${version.version}`).click();
    await expect(page.getByTestId(`deploy-version-${version.version}`)).toHaveCount(0);
  });

  test("approving a run is refused without a real session", async ({ request }) => {
    const refused = await request.post(`${API}/agents/runs/not-a-real-run/approve`);
    expect(refused.status()).toBe(401);
  });
});

/**
 * U60 (console): recordings can be brought in and sealed as recordings.
 *
 * The API half is proved by verify/v60_audio_prepare.py. This is the same
 * claim driven through the browser, which is the only way to find out whether
 * a person can actually reach it: that the platform's reading of each file
 * comes back to the screen, that a file contradicting its own name is refused
 * where the person can see it, and that a refused seal lists every problem
 * rather than the first.
 *
 * Signed in for real, because seal-audio takes the acting person from the
 * session, as canary-engineer -- see CANARY_ENGINEER_EMAIL's own comment
 * above for why this runs against the canary tenant rather than health.
 */
function wavBytes(seconds = 0.25, rate = 16000): Buffer {
  // A real, minimal PCM wav, built here so the test knows what it wrote and
  // can compare that against what the platform says it read. Two measurements
  // that ought to agree, rather than trusting the screen's own arithmetic.
  const frames = Math.round(seconds * rate);
  const data = Buffer.alloc(frames * 2);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVE", 8);
  header.write("fmt ", 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

const ANSWER_KEY = Buffer.from(
  JSON.stringify({
    spans: [{ entity: "PERSON", start: 0, end: 5, text: "Aoife" }],
    reference_transcript: "Aoife attended on Tuesday",
    hazard: false,
  }),
);

async function registerAudioDataset(page: Page, name: string): Promise<void> {
  await page.goto("/datasets/register");
  await page.getByTestId("register-name").fill(name);
  const departments = page.getByTestId("register-department");
  await expect(departments.locator("option")).not.toHaveCount(1);
  await departments.selectOption({ index: 1 });
  await page.getByTestId("register-submit").click();
  await expect(page.getByTestId("upload-step")).toBeVisible();
}

test.describe("U60: recordings are read on the way in, and sealed as recordings", () => {
  test("the platform's reading of each file comes back to the screen", async ({
    page,
  }) => {
    await loginAs(page, CANARY_ENGINEER_EMAIL);
    await registerAudioDataset(page, `console-audio-${Date.now()}`);

    // Deliberately not the obvious defaults. 22050 Hz is a rate nothing in the
    // platform would guess, so the screen showing it proves the header was
    // read rather than assumed, and 0.5s renders exactly rather than through
    // a rounding step that would make the assertion about toFixed instead.
    await page.getByTestId("upload-files").setInputFiles({
      name: "synth-0000.wav",
      mimeType: "audio/wav",
      buffer: wavBytes(0.5, 22050),
    });

    const files = page.getByTestId("uploaded-files");
    await expect(files).toContainText("synth-0000.wav");
    await expect(files).toContainText("0.5s at 22050 Hz");
  });

  test("a file that contradicts its own name is refused where you can see it", async ({
    page,
  }) => {
    await loginAs(page, CANARY_ENGINEER_EMAIL);
    await registerAudioDataset(page, `console-bad-${Date.now()}`);

    await page.getByTestId("upload-files").setInputFiles({
      name: "broken.wav",
      mimeType: "audio/wav",
      buffer: Buffer.from("pretend audio bytes"),
    });

    const alert = page.getByRole("alert");
    await expect(alert).toContainText("broken.wav");
    await expect(page.getByTestId("uploaded-files")).toHaveCount(0);
  });

  test("a refused seal lists every problem, and seals nothing", async ({ page }) => {
    await loginAs(page, CANARY_ENGINEER_EMAIL);
    await registerAudioDataset(page, `console-refuse-${Date.now()}`);

    const input = page.getByTestId("upload-files");
    await input.setInputFiles({
      name: "synth-0000.wav", mimeType: "audio/wav", buffer: wavBytes(),
    });
    await expect(page.getByTestId("uploaded-files")).toContainText("synth-0000.wav");
    await input.setInputFiles({
      name: "synth-0009.truth.json", mimeType: "application/json", buffer: ANSWER_KEY,
    });
    await input.setInputFiles({
      name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("neither kind"),
    });
    await expect(page.getByTestId("uploaded-files")).toContainText("notes.txt");

    await page.getByTestId("seal-audio-submit").click();

    const refused = page.getByTestId("seal-audio-refused");
    await expect(refused).toContainText("Nothing was sealed");
    // Both problems, in one response. Reporting only the first would turn two
    // problems into two attempts.
    await expect(refused).toContainText("synth-0009.truth.json");
    await expect(refused).toContainText("notes.txt");
    await expect(page.getByTestId("seal-done")).toHaveCount(0);
  });

  test("a coherent set seals, and says what it sealed", async ({ page }) => {
    await loginAs(page, CANARY_ENGINEER_EMAIL);
    await registerAudioDataset(page, `console-seal-${Date.now()}`);

    const input = page.getByTestId("upload-files");
    await input.setInputFiles({
      name: "synth-0000.wav", mimeType: "audio/wav", buffer: wavBytes(),
    });
    await expect(page.getByTestId("uploaded-files")).toContainText("synth-0000.wav");
    await input.setInputFiles({
      name: "synth-0000.truth.json", mimeType: "application/json", buffer: ANSWER_KEY,
    });
    await expect(page.getByTestId("uploaded-files")).toContainText("synth-0000.truth.json");

    await page.getByTestId("seal-audio-submit").click();

    const done = page.getByTestId("seal-done");
    await expect(done).toContainText("1 recording");
    await expect(done).toContainText("de-identification pipeline can read");
  });

  test("sealing as audio is refused without a real session", async ({ request }) => {
    const refused = await request.post(`${API}/datasets/not-a-real-dataset/seal-audio`);
    expect(refused.status()).toBe(401);
  });
});
