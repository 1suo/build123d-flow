import { expect, test, type Page } from "@playwright/test";

// Pinned upstream build123d examples corpus. Pinning the commit makes the
// acceptance suite reproducible while keeping the fixtures byte-for-byte
// identical to upstream rather than maintaining hand-copied approximations.
const UPSTREAM_COMMIT = "03b257b1ce8f15c9f9c3fb726533798578b3f1c3";
const EXAMPLES = [
  "benchy.py",
  "benchy_v2024.py",
  "bicycle_tire.py",
  "boxes_on_faces.py",
  "boxes_on_faces_algebra.py",
  "bracelet.py",
  "build123d_customizable_logo.py",
  "build123d_customizable_logo_algebra.py",
  "build123d_logo.py",
  "build123d_logo_algebra.py",
  "canadian_flag.py",
  "canadian_flag_algebra.py",
  "cast_bearing_unit.py",
  "circuit_board.py",
  "circuit_board_algebra.py",
  "clock.py",
  "clock_algebra.py",
  "custom_sketch_objects.py",
  "custom_sketch_objects_algebra.py",
  "din_rail.py",
  "din_rail_algebra.py",
  "dual_color_3mf.py",
  "extrude.py",
  "extrude_algebra.py",
  "fast_grid_holes.py",
  "handle.py",
  "handle_algebra.py",
  "heat_exchanger.py",
  "heat_exchanger_algebra.py",
  "holes.py",
  "holes_algebra.py",
  "intersecting_chamfers.py",
  "intersecting_chamfers_algebra.py",
  "intersecting_pipes.py",
  "joints.py",
  "joints_algebra.py",
  "key_cap.py",
  "key_cap_algebra.py",
  "lego.py",
  "lego_algebra.py",
  "loft.py",
  "loft_algebra.py",
  "maker_coin.py",
  "mixed_algebra_context.py",
  "multiple_workplanes.py",
  "multiple_workplanes_algebra.py",
  "packed_boxes.py",
  "pegboard_j_hook.py",
  "pegboard_j_hook_algebra.py",
  "pillow_block.py",
  "pillow_block_algebra.py",
  "platonic_solids.py",
  "playing_cards.py",
  "projection.py",
  "projection_algebra.py",
  "python_logo.py",
  "roller_coaster.py",
  "roller_coaster_algebra.py",
  "shamrock.py",
  "spring.py",
  "stud_wall.py",
  "tea_cup.py",
  "tea_cup_algebra.py",
  "toy_truck.py",
  "twist_extrude.py",
  "vase.py",
  "vase_algebra.py"
];


const SMOKE_CASES = [
  `from build123d import *

with BuildPart() as part:
    Box(10, 10, 10)

fender_edges = part.part.edges()
edge_groups = fender_edges.group_by(Axis.Z)
vertex_groups = part.part.vertices().group_by(Axis.Z)
`,
  `from build123d import *

offset = Vector(10, 0, 0)
with Locations(offset.normalized()):
    Box(2, 2, 2)
`,
  `from build123d import *

with BuildPart():
    with BuildSketch(Plane.XY):
        Circle(5)
    extrude(amount=10)
`,
  `from build123d import *

box = Box(10, 10, 10)
for face in box.faces():
    face.area
`,
  `from build123d import *

box = Box(10, 10, 10)
for face in box.faces():
    for edge in face.edges():
        edge.length
`,
  `from build123d import *

offset = Vector(10, 20, 30)
with Locations((0, -offset.Y, 0)):
    Box(2, 2, 2)
`
];

async function importExample(page: Page, source: string, throughEditor: boolean) {
  if (source.includes("Mesher()")) {
    await page.evaluate(async ({ url }) => {
      const testHook = (window as any).__build123dFlowTest;
      await testHook.downloadFile("low_poly_benchy.stl", url);
    }, { url: `https://raw.githubusercontent.com/gumyr/build123d/${UPSTREAM_COMMIT}/examples/low_poly_benchy.stl` });
  }

  const evaluationBeforeImport = await page.evaluate(() => (window as any).__build123dFlowTest.getEvaluationState());
  if (throughEditor) {
    const log = page.locator(".viewport-log-panel");
    if (!(await log.isVisible())) {
      await page.getByRole("button", { name: "Show activity log" }).click();
    }
    const sourceInput = page.locator("#python-source-input");
    // isVisible() can observe the outgoing popover DOM during its close
    // transition. Wait for the previous import to finish closing before
    // opening a fresh editor for this smoke case.
    await expect(sourceInput).toBeHidden({ timeout: 2_000 });
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(sourceInput).toBeVisible({ timeout: 2_000 });
    // Exercise the actual paste-and-import UI for the smoke cases.
    await sourceInput.fill(source);
    await expect(page.getByRole("button", { name: "Import pasted code" })).toBeEnabled();
    await page.getByRole("button", { name: "Import pasted code" }).click();
  } else {
    // The upstream corpus exercises the same application import pipeline,
    // but avoids replaying textarea/popover transitions 67 times.
    const imported = await page.evaluate(async (code) => {
      const hook = (window as any).__build123dFlowTest;
      if (typeof hook?.importPython !== "function") throw new Error("Application import test hook is unavailable");
      return await hook.importPython(code);
    }, source);
    expect(imported, "application import pipeline result").toBe(true);
  }

  await expect.poll(async () => {
    const state = await page.evaluate(() => (window as any).__build123dFlowTest.getEvaluationState());
    if (state.revision <= evaluationBeforeImport.revision || state.running) return null;
    return state;
  }, { timeout: 30_000, intervals: [50, 100, 150, 250] }).not.toBeNull();

  const evaluation = await page.evaluate(() => (window as any).__build123dFlowTest.getEvaluationState());
  expect(evaluation.errors, "graph evaluation errors").toEqual([]);
  expect(evaluation.geometryCount, "visible geometry count").toBeGreaterThan(0);

  await expect(page.locator(".viewport-status-count")).toHaveText(/[1-9][0-9]* visible/, { timeout: 500 });
  await expect(page.locator(".geometry-viewport canvas")).toBeVisible({ timeout: 500 });
  if (throughEditor) {
    await expect(page.locator("#python-source-input")).toBeHidden({ timeout: 2_000 });
  }
}

test.describe.configure({ mode: "parallel" });

const SHARDS = [
  {
    name: "first half",
    examples: EXAMPLES.slice(0, 34),
    smoke: SMOKE_CASES.slice(0, 3),
    runGraphWorkflowTests: true,
  },
  {
    name: "second half",
    examples: EXAMPLES.slice(34),
    smoke: SMOKE_CASES.slice(3),
    runGraphWorkflowTests: false,
  },
];

for (const shard of SHARDS) {
  test(`imports and evaluates pinned upstream examples — ${shard.name}`, async ({ page, request }) => {
    expect(EXAMPLES).toHaveLength(67);
    expect(new Set(EXAMPLES).size).toBe(EXAMPLES.length);
    expect(shard.examples.length + shard.smoke.length).toBeGreaterThan(0);
    expect(SHARDS.reduce((total, item) => total + item.examples.length, 0)).toBe(EXAMPLES.length);
    expect(SHARDS.reduce((total, item) => total + item.smoke.length, 0)).toBe(SMOKE_CASES.length);
    test.setTimeout(15 * 60 * 1000);

    const pageErrors: string[] = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    await page.goto("./", { waitUntil: "domcontentloaded" });
    const sourcesPromise = Promise.all(shard.examples.map(async filename => {
      const url = `https://raw.githubusercontent.com/gumyr/build123d/${UPSTREAM_COMMIT}/examples/${filename}`;
      const response = await request.get(url, { timeout: 10_000 });
      expect(response.ok(), `upstream fixture fetch failed: ${url}`).toBeTruthy();
      return { filename, source: await response.text() };
    }));

    await expect(page.locator(".viewport-status")).toContainText("build123d ready", { timeout: 75_000 });
    await expect(page.locator("#python-source-input")).toBeHidden();

    const log = page.locator(".viewport-log-panel");
    if (!(await log.isVisible())) {
      await page.getByRole("button", { name: "Show activity log" }).click();
    }
    const activityEntries = page.locator(".viewport-log-entry");
    await page.getByRole("button", { name: "Create", exact: true }).click();
    await expect.poll(async () =>
      (await activityEntries.allTextContents()).some(message => message.includes("API discovery complete")),
    { timeout: 15_000, intervals: [100, 200, 500] }).toBe(true);

    if (shard.runGraphWorkflowTests) {
      const graphWorkflowResults = await page.evaluate(async () => {
        const hook = (window as any).__build123dFlowTest;
        if (!hook) throw new Error("Graph workflow test hook was not installed in CI build");
        return await hook.runGraphWorkflowTests();
      });
      expect(graphWorkflowResults.length).toBeGreaterThan(10);
      await test.info().attach("graph-workflow-results.json", {
        body: JSON.stringify(graphWorkflowResults, null, 2),
        contentType: "application/json",
      });
    }

    const upstreamSources = await sourcesPromise;
    const allSources = [
      ...shard.smoke.map((source, index) => ({ filename: `smoke-${shard.name}-${index + 1}.py`, source, throughEditor: true })),
      ...upstreamSources.map(example => ({ ...example, throughEditor: false })),
    ];
    const failures: string[] = [];
    const results: string[] = [];

    for (const { filename, source, throughEditor } of allSources) {
      await test.step(filename, async () => {
        try {
          await importExample(page, source, throughEditor);
          const graph = await page.evaluate(() => (window as any).__build123dFlowTest.getCurrentGraph());
          expect(
            graph.nodes.every((node: any) => node.data.definition.cadOperation?.mapping?.preserveNesting === true),
            "every imported node must carry a validated kernel-neutral operation contract",
          ).toBeTruthy();
          results.push(filename + ": passed");
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          try {
            const graph = await page.evaluate(() => (window as any).__build123dFlowTest.getCurrentGraph());
            await test.info().attach(filename.replace(/\W+/g, "-") + "-graph.json", {
              body: JSON.stringify(graph, null, 2), contentType: "application/json"
            });
            try {
              const evaluation = await page.evaluate(() => (window as any).__build123dFlowTest.evaluateCurrentGraph());
              await test.info().attach(filename.replace(/\W+/g, "-") + "-evaluation.json", {
                body: JSON.stringify(evaluation, null, 2), contentType: "application/json"
              });
            } catch (evaluationError) {
              await test.info().attach("evaluation-diagnostics-error.txt", { body: String(evaluationError) });
            }
          } catch (diagnosticError) {
            await test.info().attach("evaluation-diagnostics-error.txt", { body: String(diagnosticError) });
          }
          failures.push(filename + ": " + message);
          const safeName = filename.replace(/\W+/g, "-");
          await test.info().attach(safeName + ".py", { body: source });
          await test.info().attach(safeName + "-failure.txt", { body: message });
          try {
            const entries = await page.locator(".viewport-log-entry").allTextContents();
            await test.info().attach(safeName + "-activity-log.txt", { body: entries.join("\n") });
          } catch {
            // Preserve the original importer failure if the browser has closed.
          }
        }
      });
    }
    await test.info().attach("upstream-example-results.txt", { body: [...results, ...failures].join("\n") });
    await test.info().attach("browser-errors.txt", { body: pageErrors.join("\n") });
    expect(pageErrors, "no uncaught browser errors").toEqual([]);
    expect(failures, "every example in this shard must import and evaluate").toEqual([]);
  });
}
