import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

test("requires the local worker token", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Connect your local worker" }),
  ).toBeVisible();
  await page.getByLabel("Worker token").fill("incorrect-token");
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("incorrect");
});

test("real geometry, boundary mapping, solver evidence, reload and portable export", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByTestId("geometry-viewer").locator("canvas"),
  ).toBeVisible();
  await expect
    .poll(async () =>
      Number(
        await page
          .getByTestId("geometry-viewer")
          .getAttribute("data-rendered-pixels"),
      ),
    )
    .toBeGreaterThan(1000);
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Inlet port", exact: false }).click();
  await expect(page.getByLabel("SELECTED FACE ROLE")).toHaveValue("inlet");
  const canvas = page.getByTestId("geometry-viewer").locator("canvas");
  const canvasBox = await canvas.boundingBox();
  await canvas.click({
    position: { x: canvasBox!.width / 2, y: canvasBox!.height / 2 },
  });
  await expect(page.getByLabel("SELECTED FACE ROLE")).toHaveValue("wall");
  await page.getByRole("button", { name: "Inlet port", exact: false }).click();
  // Invalid assignments must surface a worker error; they must not get silently fixed.
  await page.getByLabel("SELECTED FACE ROLE").selectOption("wall");
  await page.getByRole("button", { name: "Save assignments" }).click();
  await expect(page.getByRole("alert")).toContainText("exactly one inlet");
  await page.getByLabel("SELECTED FACE ROLE").selectOption("inlet");
  await page.getByRole("button", { name: "Save assignments" }).click();
  await expect(
    page.getByRole("button", { name: "Assignments saved" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Toggle wall transparency" }).click();
  await page.getByRole("button", { name: "Toggle section cut" }).click();
  await page.getByRole("button", { name: "Toggle section cut" }).click();
  await page.getByRole("button", { name: "Toggle wall transparency" }).click();
  await page.screenshot({
    path: "../../artifacts/screenshots/geometry.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Run boundary check" }).click();
  await expect(page.locator(".status-pill")).toHaveText("passed", {
    timeout: 90_000,
  });
  await expect(page.locator(".evidence-checks")).toContainText("inlet mapping");
  await expect(page.locator(".evidence-checks .fail")).toHaveCount(0);
  await page.getByRole("button", { name: "Geometry & boundaries" }).click();
  await page.getByRole("button", { name: "Run pipe reference" }).click();
  await expect(page.locator(".status-pill")).toHaveText(
    /queued|running|passed/,
  );
  const runId = await page.locator(".run-heading code").textContent();
  await page.reload();
  await page.getByRole("button", { name: "Runs & evidence" }).click();
  await expect(page.locator(".run-heading code")).toHaveText(runId!);
  await expect(page.locator(".status-pill")).toHaveText("passed", {
    timeout: 120_000,
  });
  await expect(page.locator(".result-metrics")).toContainText("0.322");
  await expect(page.locator(".evidence-checks .fail")).toHaveCount(0);
  await page.screenshot({
    path: "../../artifacts/screenshots/evidence.png",
    fullPage: true,
  });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export complete run" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^venturi-.*\.zip$/);
  const bytes = readFileSync((await download.path())!);
  expect(bytes.length).toBeGreaterThan(10_000);
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  expect(errors).toEqual([]);
});
