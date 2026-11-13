import { expect, test } from "@playwright/test";

const cases = [
  {
    name: "ShapeList.group_by on build123d edge and vertex collections",
    source: `from build123d import *

with BuildPart() as part:
    Box(10, 10, 10)

fender_edges = part.part.edges()
edge_groups = fender_edges.group_by(Axis.Z)
vertex_groups = part.part.vertices().group_by(Axis.Z)
`
  },
  {
    name: "Vector construction, normalized(), and Locations arguments",
    source: `from build123d import *

offset = Vector(10, 0, 0)
with Locations(offset.normalized()):
    Box(2, 2, 2)
`
  },
  {
    name: "Plane constant in nested BuildSketch context manager",
    source: `from build123d import *

with BuildPart():
    with BuildSketch(Plane.XY):
        Circle(5)
    extrude(amount=10)
`
  },
  {
    name: "for-loop maps one operation over a dynamic face stream",
    source: `from build123d import *

box = Box(10, 10, 10)
for face in box.faces():
    face.area
`
  },
  {
    name: "nested for-loops preserve the collection tree",
    source: `from build123d import *

box = Box(10, 10, 10)
for face in box.faces():
    for edge in face.edges():
        edge.length
`
  },
  {
    name: "ambiguous Y property under unary negation",
    source: `from build123d import *

offset = Vector(10, 20, 30)
with Locations((0, -offset.Y, 0)):
    Box(2, 2, 2)
`
  }
];

test("browser imports and evaluates build123d Python", async ({ page }) => {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  page.on("console", message => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });

  await page.goto("./", { waitUntil: "domcontentloaded" });

  // Runtime fetches happen inside a dedicated worker, not the page's request
  // lifecycle. Fetch the exact versioned source independently instead of
  // waiting for a page response that can never arrive.
  const runtime = await page.evaluate(async () => {
    const response = await fetch(
      new URL("python/flow_runtime.py?v=2026-11-13-r14", document.baseURI),
      { cache: "no-store" }
    );
    return { ok: response.ok, status: response.status, source: await response.text() };
  });
  expect(runtime.ok, "versioned Python runtime must be served").toBeTruthy();
  expect(runtime.source).toContain("_PYTHON_ATTRIBUTE_ID");
  expect(runtime.source).toContain("def _python_getattr");

  const runtimeErrorLocator = page.locator(".runtime-error");
  const runtimeError = await runtimeErrorLocator.count()
    ? await runtimeErrorLocator.textContent()
    : null;
  if (runtimeError) {
    await test.info().attach("runtime-error.txt", { body: runtimeError });
    await test.info().attach("runtime-console.txt", { body: consoleErrors.join("\n") });
  }
  expect(runtimeError, "Pyodide and build123d initialization must complete").toBeNull();

  // The status text is presentation, not a reliable readiness hook. The
  // importer textarea is disabled until the worker has initialized and the
  // catalog has loaded; assert that actual user capability instead.
  await page.getByRole("button", { name: "Settings" }).click();
  await expect(page.locator("#python-source-input")).toBeEnabled({ timeout: 5 * 60 * 1000 });
  await expect(page.locator(".viewport-status")).toContainText("Ready");

  for (const [index, scenario] of cases.entries()) {
    await test.step(scenario.name, async () => {
      if (index > 0) await page.getByRole("button", { name: "Settings" }).click();
      await page.locator("#python-source-input").fill(scenario.source);
      await page.getByRole("button", { name: "Import pasted code" }).click();

      await expect(page.locator(".viewport-status")).toContainText("Python imported", {
        timeout: 120_000
      });
      await expect(page.locator(".viewport-status-count")).not.toHaveText("0 visible", {
        timeout: 120_000
      });
      await expect(page.locator(".viewport-status-dot")).not.toHaveClass(/is-error/);

      const log = page.locator(".viewport-log-panel");
      if (!(await log.isVisible())) {
        await page.getByRole("button", { name: "Show activity log" }).click();
      }
      await expect(log).toContainText("Python imported", { timeout: 30_000 });
      await expect(log).toContainText("Evaluation complete", { timeout: 120_000 });
      await expect(log).not.toContainText("Python import failed");
    });
  }

  expect(pageErrors, "no uncaught browser errors").toEqual([]);
});
