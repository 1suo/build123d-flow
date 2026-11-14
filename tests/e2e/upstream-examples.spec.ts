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

async function importExample(page: Page, source: string) {
  const log = page.locator(".viewport-log-panel");
  if (!(await log.isVisible())) {
    await page.getByRole("button", { name: "Show activity log" }).click();
  }
  if (!(await page.locator("#python-source-input").isVisible())) {
    await page.getByRole("button", { name: "Settings" }).click();
  }

  const entries = page.locator(".viewport-log-entry");
  const previousLatest = (await entries.first().textContent()) ?? "";
  await page.locator("#python-source-input").fill(source);
  await page.getByRole("button", { name: "Import pasted code" }).click();

  // The activity log is newest-first. Wait for this click's start marker,
  // then accept only import/evaluation events newer than that marker. This
  // prevents a late result from the previous example satisfying this one.
  await expect.poll(async () => {
    const latest = (await entries.first().textContent()) ?? "";
    return latest !== previousLatest && latest.includes("Importing Python source");
  }, { timeout: 2_000, intervals: [25, 50, 100] }).toBe(true);

  await expect.poll(async () => {
    const messages = await entries.allTextContents();
    const importStart = messages.findIndex(message => message.includes("Importing Python source"));
    if (importStart < 0) return "pending";
    const current = messages.slice(0, importStart);
    if (current.some(message => message.includes("Python import failed"))) return "import-failed";
    if (current.some(message => message.includes("Python imported"))) return "imported";
    return "pending";
  }, { timeout: 5_000, intervals: [50, 100, 150] }).toBe("imported");

  await expect.poll(async () => {
    const messages = await entries.allTextContents();
    const importStart = messages.findIndex(message => message.includes("Importing Python source"));
    if (importStart < 0) return "pending";
    const current = messages.slice(0, importStart);
    if (current.some(message => message.includes("Evaluation complete"))) return "passed";
    const state = await page.locator(".viewport-status-dot").getAttribute("class") ?? "";
    if (/is-error/.test(state)) return "evaluation-failed";
    return "pending";
  }, { timeout: 5_000, intervals: [50, 100, 150] }).toBe("passed");

  await expect(page.locator(".viewport-status-count")).toHaveText(/[1-9][0-9]* visible/, { timeout: 500 });
  await expect(page.locator(".geometry-viewport canvas")).toBeVisible({ timeout: 500 });
}

test("imports and evaluates every pinned upstream build123d example", async ({ page, request }) => {
  expect(EXAMPLES).toHaveLength(67);
  expect(new Set(EXAMPLES).size).toBe(EXAMPLES.length);
  test.setTimeout(110_000);
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto("./", { waitUntil: "domcontentloaded" });
  const sourcesPromise = Promise.all(EXAMPLES.map(async filename => {
    const url = `https://raw.githubusercontent.com/gumyr/build123d/${UPSTREAM_COMMIT}/examples/${filename}`;
    const response = await request.get(url, { timeout: 10_000 });
    expect(response.ok(), `upstream fixture fetch failed: ${url}`).toBeTruthy();
    return { filename, source: await response.text() };
  }));

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.locator("#python-source-input")).toBeEnabled({ timeout: 45_000 });
  await expect(page.locator(".viewport-status")).toContainText("Ready", { timeout: 1_000 });
  const upstreamSources = await sourcesPromise;

  const failures: string[] = [];
  const results: string[] = [];
  const allSources = [
    ...SMOKE_CASES.map((source, index) => ({ filename: `smoke-${index + 1}.py`, source })),
    ...upstreamSources
  ];
  for (const { filename, source } of allSources) {
    await test.step(filename, async () => {
      try {
        await importExample(page, source);
        results.push(filename + ": passed");
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        failures.push(filename + ": " + message);
        const safeName = filename.replace(/\W+/g, "-");
        await test.info().attach(safeName + ".py", { body: source });
        await test.info().attach(safeName + "-failure.txt", { body: message });
        try {
          const log = await page.locator(".viewport-log-entry").allTextContents();
          await test.info().attach(safeName + "-activity-log.txt", { body: log.join("\n") });
        } catch {
          // Preserve the original importer failure if the browser has closed.
        }
      }
    });
  }
  await test.info().attach("upstream-example-results.txt", { body: [...results, ...failures].join("\n") });
  await test.info().attach("browser-errors.txt", { body: pageErrors.join("\n") });
  expect(pageErrors, "no uncaught browser errors").toEqual([]);
  expect(failures, "every upstream example must import and evaluate").toEqual([]);
});
