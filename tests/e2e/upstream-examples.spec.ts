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

async function importExample(page: Page, source: string) {
  if (!(await page.locator("#python-source-input").isVisible())) {
    await page.getByRole("button", { name: "Settings" }).click();
  }
  const log = page.locator(".viewport-log-panel");
  if (!(await log.isVisible())) {
    await page.getByRole("button", { name: "Show activity log" }).click();
  }

  const entries = page.locator(".viewport-log-entry");
  const previousCount = await entries.count();
  await page.locator("#python-source-input").fill(source);
  await page.getByRole("button", { name: "Import pasted code" }).click();

  // The status headline is deliberately transient (5s), so polling it can
  // miss a valid import result and burn the full 120s timeout. The activity
  // log is the durable result channel and keeps the exact importer exception.
  await expect.poll(async () => {
    const latest = await entries.first().textContent();
    return latest ?? "";
  }, { timeout: 180_000 }).toMatch(/Python imported|Python import failed/);

  const latest = (await entries.first().textContent()) ?? "";
  if (latest.includes("Python import failed")) {
    throw new Error(latest);
  }
  expect(latest).toContain("Python imported");
  await expect.poll(async () => await entries.count(), {
    timeout: 180_000
  }).toBeGreaterThan(previousCount + 1);
  await expect.poll(async () => (await entries.first().textContent()) ?? "", {
    timeout: 180_000
  }).toContain("Evaluation complete");
  await expect(page.locator(".viewport-status-dot")).not.toHaveClass(/is-error/);
}

test("imports and evaluates the pinned upstream build123d examples corpus", async ({ page, request }) => {\n  test.setTimeout(45 * 60 * 1000);
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.goto("./", { waitUntil: "domcontentloaded" });

  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.locator("#python-source-input")).toBeEnabled({ timeout: 5 * 60 * 1000 });
  await expect(page.locator(".viewport-status")).toContainText("Ready");

  const failures: string[] = [];
  for (const filename of EXAMPLES) {
    await test.step(filename, async () => {
      const url = `https://raw.githubusercontent.com/gumyr/build123d/${UPSTREAM_COMMIT}/examples/${filename}`;
      const response = await request.get(url);
      expect(response.ok(), `upstream fixture fetch failed: ${url}`).toBeTruthy();
      const source = await response.text();
      try {
        await importExample(page, source);
      } catch (error) {
        failures.push(`${filename}: ${error instanceof Error ? error.message : String(error)}`);
      }
    });
  }
  expect(pageErrors, "no uncaught browser errors").toEqual([]);
  expect(failures, "every upstream example must import and evaluate").toEqual([]);
});
