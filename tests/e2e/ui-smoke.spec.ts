import { expect, test } from "@playwright/test";

test("application boot, settings import, and graph rendering", async ({ page }) => {
  test.setTimeout(90_000);
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));

  await page.goto("./", { waitUntil: "domcontentloaded" });
  await expect(page.locator(".viewport-status")).toContainText(
    "build123d ready",
    { timeout: 75_000 },
  );
  await expect(page.locator("#python-source-input")).toBeHidden();

  const log = page.locator(".viewport-log-panel");
  if (!(await log.isVisible())) {
    await page.getByRole("button", { name: "Show activity log" }).click();
  }
  const activityEntries = page.locator(".viewport-log-entry");
  await page.getByRole("button", { name: "Create", exact: true }).click();
  await expect.poll(async () =>
    (await activityEntries.allTextContents()).some(message =>
      message.includes("API discovery complete"),
    ),
  { timeout: 15_000 }).toBe(true);

  const sourceInput = page.locator("#python-source-input");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(sourceInput).toBeVisible();
  const evaluationBeforeImport = await page.evaluate(
    () => (window as any).__build123dFlowTest.getEvaluationState(),
  );
  await sourceInput.fill(`from build123d import *

with BuildPart() as part:
    Box(10, 10, 10)
`);
  const importButton = page.getByRole("button", { name: "Import pasted code" });
  await expect(importButton).toBeEnabled();
  await importButton.click();

  await expect.poll(async () => {
    const state = await page.evaluate(
      () => (window as any).__build123dFlowTest.getEvaluationState(),
    );
    if (state.revision <= evaluationBeforeImport.revision || state.running) return null;
    return state;
  }, { timeout: 30_000, intervals: [100, 200, 500] }).not.toBeNull();

  const evaluation = await page.evaluate(
    () => (window as any).__build123dFlowTest.getEvaluationState(),
  );
  expect(evaluation.errors).toEqual([]);
  expect(evaluation.geometryCount).toBeGreaterThan(0);
  await expect(page.locator(".viewport-status-count")).toHaveText(/[1-9][0-9]* visible/);
  await expect(page.locator(".geometry-viewport canvas")).toBeVisible();
  expect(pageErrors, "no uncaught browser errors").toEqual([]);
});
