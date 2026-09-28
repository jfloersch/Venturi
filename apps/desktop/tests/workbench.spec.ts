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

test("cancellation retains a report and retry creates a separate attempt", async ({
  page,
}) => {
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Run pipe reference" }).click();
  await expect(page.getByRole("button", { name: "Cancel run" })).toBeVisible();
  const original = await page.locator(".run-heading code").textContent();
  await page.getByRole("button", { name: "Cancel run" }).click();
  await expect(page.locator(".status-pill")).toHaveText("cancelled", {
    timeout: 30_000,
  });
  await expect(page.locator(".evidence-checks")).toContainText(
    "Execution completed",
  );
  await page.getByRole("button", { name: "Retry as a new attempt" }).click();
  await expect(page.locator(".run-heading code")).not.toHaveText(original!);
  await expect(
    page.getByText("The original evidence is retained.", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Cancel run" }).click();
  await expect(page.locator(".status-pill")).toHaveText("cancelled", {
    timeout: 30_000,
  });
  await page.getByText("Saved study and provenance", { exact: true }).click();
  await expect(page.locator(".study-record")).toContainText("laminar-pipe/1");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export complete run" }).click();
  expect((await downloadPromise).suggestedFilename()).toMatch(
    /^venturi-.*\.zip$/,
  );
});

test("prepared manifold: import, explicit ports, mesh review, flow split and export", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const source = "../../fixtures/internal/manifold.step";
  const study = JSON.parse(
    readFileSync("../../fixtures/internal/manifold.study.json", "utf8"),
  );
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page.getByLabel("Import STEP", { exact: true }).setInputFiles(source);
  await expect(
    page.getByRole("heading", { name: "Set up internal flow" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Build mesh for review" }),
  ).toBeDisabled();
  const response = await page.request.get("http://127.0.0.1:8766/v1/geometry", {
    headers: { Authorization: "Bearer venturi-e2e-session-only" },
  });
  const geometry = await response.json();
  for (const face of geometry.faces) {
    const role = study.selection.assignments[face.id];
    if (role === "wall") continue;
    await page.locator(".face-list button").nth(face.index).click();
    await page.getByLabel("SELECTED FACE ROLE").selectOption(role);
  }
  await page.getByRole("button", { name: "Save assignments" }).click();
  await expect(
    page.getByRole("button", { name: "Assignments saved" }),
  ).toBeVisible();
  await page.getByLabel("Inlet flow (m³/s)").fill(String(study.flow_rate_m3_s));
  await page
    .getByLabel("Mesh cell size (mm)")
    .fill(String(study.mesh.cell_size_m * 1000));
  await page.reload();
  await expect(page.getByLabel("Mesh cell size (mm)")).toHaveValue("0.625");
  await page.screenshot({
    path: "../../artifacts/milestone-1b/browser-import.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Build mesh for review" }).click();
  await expect(page.locator(".status-pill")).toHaveText("passed", {
    timeout: 90_000,
  });
  await expect(
    page.getByRole("heading", { name: "Review this mesh and saved study" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Approve saved study & solve" }),
  ).toBeEnabled();
  await expect(
    page.getByTestId("geometry-viewer").locator("canvas"),
  ).toBeVisible();
  const meshId = await page.locator(".run-heading code").textContent();
  await page.reload();
  await page.getByRole("button", { name: "Runs & evidence" }).click();
  await expect(page.locator(".run-heading code")).toHaveText(meshId!);
  await expect(
    page.getByRole("button", { name: "Approve saved study & solve" }),
  ).toBeEnabled();
  await page.screenshot({
    path: "../../artifacts/milestone-1b/browser-mesh.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Approve saved study & solve" })
    .click();
  await expect(page.locator(".run-heading code")).not.toHaveText(meshId!);
  await expect(page.locator(".status-pill")).toHaveText("passed", {
    timeout: 180_000,
  });
  await expect(page.locator(".outlet-results tbody tr")).toHaveCount(2);
  await expect(page.locator(".evidence-checks .fail")).toHaveCount(0);
  await expect(page.locator(".outlet-results")).toContainText("50.0");
  await page.screenshot({
    path: "../../artifacts/milestone-1b/browser-flow.png",
    fullPage: true,
  });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export complete run" }).click();
  const download = await downloadPromise;
  await download.saveAs("../../artifacts/milestone-1b/browser-manifold.zip");
  expect(
    readFileSync((await download.path())!)
      .subarray(0, 2)
      .toString(),
  ).toBe("PK");
  await page.reload();
  await page.getByRole("button", { name: "Runs & evidence" }).click();
  await expect(page.locator(".status-pill")).toHaveText("passed");
  await expect(page.locator(".outlet-results tbody tr")).toHaveCount(2);
  expect(errors).toEqual([]);
  // A fetched mesh is insufficient for approval if the renderer produces a blank frame.
  await page.addInitScript(() => {
    WebGL2RenderingContext.prototype.readPixels = function (
      ...args: unknown[]
    ) {
      const pixels = args[6];
      if (pixels instanceof Uint8Array) pixels.fill(0);
    } as typeof WebGL2RenderingContext.prototype.readPixels;
  });
  await page.reload();
  await page
    .getByRole("button", {
      name: new RegExp(
        `STEP mesh review ${meshId!.replace(/^RUN /, "").slice(0, 8)}`,
      ),
    })
    .click();
  await expect(page.locator(".mesh-review").getByRole("alert")).toContainText(
    "did not draw",
  );
  await expect(
    page.getByRole("button", { name: "Approve saved study & solve" }),
  ).toBeDisabled();
});
