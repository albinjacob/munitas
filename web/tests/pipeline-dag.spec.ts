/**
 * U162 in the console: the steps of a pipeline version are drawn as a graph, and the drawing keeps to its rules.
 *
 * The rules are checked on the geometry itself (lib/dagLayout.ts, run in the page) for ten shapes of graph, and then on the real pipeline page: a
 * box for every step, an arrow for every dependency, every arrow with its head, and a legend that names each kind of box that is on the page.
 */
import { expect, test } from "@playwright/test";
import { bearerFor, loginAs } from "./auth-helpers";
import { API_BASE } from "../config/ports";

const ENGINEER = "canary-engineer";

interface Step { name: string; kind: string; depends_on?: string[]; [k: string]: unknown }

async function aPipelineWithVersion(): Promise<{ id: string; steps: Step[] }> {
  const headers = await bearerFor(ENGINEER);
  const list = (await (await fetch(`${API_BASE}/pipelines`, { headers })).json()) as any;
  for (const p of (Array.isArray(list) ? list : list.pipelines).slice(0, 40)) {
    const d = (await (await fetch(`${API_BASE}/pipelines/${p.id}`, { headers })).json()) as any;
    const steps = d.versions?.[0]?.dag_config?.steps as Step[] | undefined;
    if (steps && steps.length >= 2) return { id: p.id, steps };
  }
  throw new Error("no pipeline with a version was found for this person");
}

const BIG: Step[] = [
  { kind: "block", name: "transcribe", block: "transcribe" },
  { kind: "block", name: "find_identifiers", block: "find_identifiers", depends_on: ["transcribe"] },
  { kind: "script", name: "check_notes", script: "steps/check_notes.py", reads_files: true, memory_mb: 2048, depends_on: ["transcribe"] },
  { kind: "block", name: "hand_off", block: "annotate", depends_on: ["find_identifiers", "check_notes"] },
  { kind: "script", name: "remove", script: "steps/remove.py", writes_files: true, depends_on: ["hand_off", "transcribe"] },
  { kind: "gate", name: "decide", to_class: "OPEN_FOR_ANNOTATION", depends_on: ["remove", "check_notes"] },
];

test.describe("U162: a pipeline version as a graph", () => {
  test("the layout keeps to its rules on ten shapes of graph", async ({ page }) => {
    await page.goto("/auth/login");
    const res = await page.evaluate(async () => {
      const m: any = await import("/src/lib/dagLayout.ts");
      const S = (name: string, ...d: string[]) => ({ name, depends_on: d });
      const cases: Record<string, any[]> = {
        chain: [S("a"), S("b", "a"), S("c", "b")],
        pair: [S("silence"), S("decide", "silence")],
        diamond: [S("a"), S("b", "a"), S("c", "a"), S("d", "b", "c")],
        fanout: [S("a"), S("b", "a"), S("c", "a"), S("d", "a")],
        fanin: [S("a"), S("b"), S("c"), S("d", "a", "b", "c")],
        skip: [S("a"), S("b", "a"), S("c", "b"), S("d", "c", "a")],
        skips: [S("a"), S("b", "a"), S("c", "b"), S("d", "c"), S("e", "d", "a"), S("f", "e", "b")],
        deid: [S("transcribe"), S("find", "transcribe"), S("handoff", "find"), S("remove", "handoff", "transcribe"), S("decide", "remove")],
        mix: [S("a"), S("b"), S("c", "a"), S("d", "a", "b"), S("e", "b"), S("f", "c", "d"), S("g", "d", "e"), S("h", "f", "g", "a")],
        wide: [S("s1"), S("s2"), S("s3"), S("s4"), S("m", "s1", "s2", "s3", "s4"), S("x", "m"), S("y", "m"), S("z", "x", "y", "s1")],
      };
      const out: Record<string, { problems: string[]; edges: number; expected: number }> = {};
      for (const [k, steps] of Object.entries(cases)) {
        const l = m.layoutDag(steps);
        out[k] = { problems: m.checkLayout(l), edges: l.edges.length, expected: steps.reduce((n: number, s: any) => n + s.depends_on.length, 0) };
      }
      return out;
    });
    for (const [name, r] of Object.entries(res)) {
      expect(r.problems, `${name}: ${r.problems.join("; ")}`).toEqual([]);
      expect(r.edges, `${name}: an arrow for every dependency`).toBe(r.expected);
    }
  });

  test("the real page draws a box for each step and an arrow for each dependency, every arrow with its head", async ({ page }) => {
    const { id, steps } = await aPipelineWithVersion();
    await loginAs(page, ENGINEER);
    await page.goto(`/pipelines/${id}`);
    const dag = page.getByTestId("pipeline-dag").first();
    await expect(dag).toBeVisible({ timeout: 20_000 });
    for (const s of steps) await expect(dag.getByTestId(`dag-step-${s.name}`)).toBeVisible();
    const dependencies = steps.reduce((n, s) => n + (s.depends_on?.length ?? 0), 0);
    const arrows = dag.locator('[data-testid^="dag-edge-"]');
    await expect(arrows).toHaveCount(dependencies);
    expect(await arrows.evaluateAll((els) => els.filter((e) => (e.getAttribute("marker-end") ?? "").startsWith("url(#")).length)).toBe(dependencies);
    await expect(page.getByTestId("dag-legend").first()).toContainText("Arrow");
  });

  test("a larger graph keeps every arrow's head, the boxes do not overlap, and the legend names each kind shown", async ({ page }) => {
    const { id } = await aPipelineWithVersion();
    await loginAs(page, ENGINEER);
    await page.route(`**/pipelines/${id}**`, async (route) => {
      const type = route.request().resourceType();
      if (type !== "fetch" && type !== "xhr") return route.continue();
      const response = await route.fetch();
      const body = await response.json();
      body.versions[0].dag_config.steps = BIG;
      await route.fulfill({ response, json: body });
    });
    await page.goto(`/pipelines/${id}`);
    const dag = page.getByTestId("pipeline-dag").first();
    await expect(dag).toBeVisible({ timeout: 20_000 });
    const dependencies = BIG.reduce((n, s) => n + (s.depends_on?.length ?? 0), 0);
    const arrows = dag.locator('[data-testid^="dag-edge-"]');
    await expect(arrows).toHaveCount(dependencies);
    expect(await arrows.evaluateAll((els) => els.filter((e) => (e.getAttribute("marker-end") ?? "").startsWith("url(#")).length)).toBe(dependencies);
    const boxes = await dag.locator('[data-testid^="dag-step-"]').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }));
    expect(boxes.length).toBe(BIG.length);
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i], b = boxes[j];
        expect(a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3], `box ${i} overlaps box ${j}`).toBe(false);
      }
    }
    const legend = page.getByTestId("dag-legend").first();
    for (const word of ["Built-in step", "Your script", "Decision", "Arrow"]) await expect(legend).toContainText(word);
  });

  test("a graph that fits says nothing; a wider one says so, fades its edge, and every step can be scrolled into view", async ({ page }) => {
    const { id } = await aPipelineWithVersion();
    await loginAs(page, ENGINEER);
    await page.route(`**/pipelines/${id}**`, async (route) => {
      const type = route.request().resourceType();
      if (type !== "fetch" && type !== "xhr") return route.continue();
      const response = await route.fetch();
      const body = await response.json();
      body.versions[0].dag_config.steps = BIG;
      await route.fulfill({ response, json: body });
    });

    await page.setViewportSize({ width: 1920, height: 900 });
    await page.goto(`/pipelines/${id}`);
    await expect(page.getByTestId("pipeline-dag").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("dag-scroll-hint")).toHaveCount(0);
    const fits = await page.evaluate(() => {
      const box = (document.querySelector('[data-testid="dag-scroller"]') as HTMLElement).getBoundingClientRect();
      return Array.from(document.querySelectorAll('[data-testid^="dag-step-"]')).every((e) => { const r = e.getBoundingClientRect(); return r.left >= box.left - 1 && r.right <= box.right + 1; });
    });
    expect(fits, "every step is inside the card at a wide window").toBe(true);

    await page.setViewportSize({ width: 800, height: 900 });
    await page.goto(`/pipelines/${id}`);
    await expect(page.getByTestId("dag-scroll-hint")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("dag-more")).toBeVisible();
    const scroller = page.getByTestId("dag-scroller");
    await scroller.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
    await expect(page.getByTestId("dag-more")).toHaveCount(0);
    const last = await page.evaluate(() => {
      const box = (document.querySelector('[data-testid="dag-scroller"]') as HTMLElement).getBoundingClientRect();
      const r = document.querySelector('[data-testid="dag-step-decide"]')!.getBoundingClientRect();
      return r.left >= box.left - 1 && r.right <= box.right + 1;
    });
    expect(last, "the last step can be scrolled fully into view").toBe(true);
  });

  test("a run is drawn as the graph it is, each box showing how far its step has got, and a step with no record yet shown as waiting", async ({ page }) => {
    const base = {
      id: "stub-run", dataset: "a-dataset", pipeline_name: "a-pipeline", workflow_id: "wf-1", error: null, status_error: null,
      started_at: new Date(Date.now() - 600_000).toISOString(), ended_at: null, ended_source: null, triggered_by: "canary-engineer",
      triggered_by_label: "Canary engineer", started_from: "by hand", source_version_id: null, gate_decision: null, status: "running",
    };
    const at = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();
    const diamond = {
      ...base, pipeline_kind: "dag",
      dag: [
        { name: "prepare", kind: "script", depends_on: [] },
        { name: "left", kind: "script", depends_on: ["prepare"] },
        { name: "right", kind: "block", depends_on: ["prepare"] },
        { name: "decide", kind: "gate", depends_on: ["left", "right"] },
      ],
      steps: [
        { action: "prepare", status: "succeeded", started_at: at(9), ended_at: at(8), output_version: "11111111-1111-4111-8111-111111111111" },
        { action: "left", status: "succeeded", started_at: at(8), ended_at: at(7), output_version: null },
        { action: "right", status: "running", started_at: at(7), ended_at: null, output_version: null },
      ],
    };
    await loginAs(page, ENGINEER);
    await page.route("**/pipeline-runs/stub-run**", (route) => {
      const type = route.request().resourceType();
      return type === "fetch" || type === "xhr" ? route.fulfill({ json: diamond }) : route.continue();
    });
    await page.goto("/pipeline-runs/stub-run");
    const dag = page.getByTestId("run-dag");
    await expect(dag).toBeVisible({ timeout: 20_000 });
    await expect(dag.locator('[data-testid^="dag-edge-"]')).toHaveCount(4);
    expect(await dag.locator('[data-testid^="dag-edge-"]').evaluateAll((els) => els.filter((e) => (e.getAttribute("marker-end") ?? "").startsWith("url(#")).length)).toBe(4);
    await expect(dag.getByTestId("pipeline-step-prepare")).toContainText("Done");
    await expect(dag.getByTestId("pipeline-step-right")).toContainText("Working");
    await expect(dag.getByTestId("pipeline-step-waiting")).toContainText("decide");
    await expect(dag.getByTestId("pipeline-step-waiting")).toContainText("Not started");
    await expect(dag.getByTestId("pipeline-step-made-prepare")).toBeVisible();
    const legend = dag.getByTestId("dag-legend");
    for (const word of ["Done", "Working", "Not started", "Arrow"]) await expect(legend).toContainText(word);
    await expect(legend).not.toContainText("Failed");
  });

  test("the built-in de-identification run is drawn as its chain of four steps", async ({ page }) => {
    const run = {
      id: "stub-deid", dataset: "a-dataset", pipeline_kind: "deidentify", pipeline_name: null, dag: null, workflow_id: "wf-2", error: null,
      status_error: null, started_at: new Date().toISOString(), ended_at: null, ended_source: null, triggered_by: "canary-engineer",
      triggered_by_label: "Canary engineer", started_from: "by hand", source_version_id: null, gate_decision: null, status: "running",
      steps: [{ action: "transcribe", status: "running", started_at: new Date().toISOString(), ended_at: null, output_version: null }],
    };
    await loginAs(page, ENGINEER);
    await page.route("**/pipeline-runs/stub-deid**", (route) => {
      const type = route.request().resourceType();
      return type === "fetch" || type === "xhr" ? route.fulfill({ json: run }) : route.continue();
    });
    await page.goto("/pipeline-runs/stub-deid");
    const dag = page.getByTestId("run-dag");
    await expect(dag).toBeVisible({ timeout: 20_000 });
    await expect(dag.locator('[data-testid^="dag-edge-"]')).toHaveCount(3);
    for (const label of ["Transcribe the files", "Find the identifiers", "Hand off for annotation", "Remove the identifiers"]) await expect(dag).toContainText(label);
  });
});
