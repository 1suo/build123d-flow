import { expect, test } from "@playwright/test";

const importCases = [
  {
    name: "ShapeList.group_by on a build123d topology collection",
    source: `from build123d import *

with BuildPart() as part:
    Box(10, 10, 10)

vertices = part.part.vertices()
groups = vertices.group_by(Axis.Z)
`
  },
  {
    name: "Vector construction, normalized(), and Locations scope arguments",
    source: `from build123d import *

offset = Vector(10, 0, 0)
with Locations(offset.normalized()):
    Box(2, 2, 2)
`
  },
  {
    name: "Plane constant passed into a nested BuildSketch context manager",
    source: `from build123d import *

with BuildPart():
    with BuildSketch(Plane.XY):
        Circle(5)
    extrude(amount=10)
`
  },
  {
    name: "ambiguous Y property inside a unary expression and Locations argument",
    source: `from build123d import *

offset = Vector(10, 20, 30)
with Locations((0, -offset.Y, 0)):
    Box(2, 2, 2)
`
  }
];

test("imports and evaluates real build123d scripts in the browser", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));

  const runtimeResponsePromise = page.waitForResponse((response) =>
    response.url().includes("/python/flow_runtime.py?v=")
  );
  await page.goto("./", { waitUntil: "domcontentloaded" });
  const runtimeResponse = await runtimeResponsePromise;
  expect(runtimeResponse.ok(), "the browser must fetch the current Python importer").toBeTruthy();
  expect(runtimeResponse.url()).toContain("2026-11-13-r14");
  const runtimeSource = await runtimeResponse.text();
  expect(runtimeSource).toContain("_PYTHON_ATTRIBUTE_ID");
  expect(runtimeSource).toContain("def _python_getattr");

  await expect(page.locator(".category-loading")).toHaveCount(0, { timeout: 20 * 60 * 1000 });
  await expect(page.locator(".viewport-status-label")).toHaveText("Ready", { timeout: 20 * 60 * 1000 });

  for (const scenario of importCases) {
    await test.step(scenario.name, async () => {
      await page.getByRole("button", { name: "Settings" }).click();
      await page.locator("#python-source-input").fill(scenario.source);
      await page.getByRole("button", { name: "Import pasted code" }).click();

      await expect(page.locator(".viewport-status-label")).toContainText("Python imported", {
        timeout: 120_000
      });
      await expect(page.locator(".viewport-status-count")).not.toHaveText("0 visible", {
        timeout: 120_000
      });
      await expect(page.locator(".viewport-status-dot")).not.toHaveClass(/is-error/);

      const logToggle = page.getByRole("button", { name: "Show activity log" });
      if (await logToggle.count()) await logToggle.click();
      const log = page.locator(".viewport-log-panel");
      await expect(log).toContainText("Python imported", { timeout: 120_000 });
      await expect(log).toContainText("Evaluation complete", { timeout: 120_000 });
      await expect(log).not.toContainText("Python import failed");
    });
  }

  expect(pageErrors, "the app must not throw uncaught browser errors").toEqual([]);
});
