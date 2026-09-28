import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";

const duplicateRunErrors = new WeakMap<Page, string[]>();
test.beforeEach(({ page }) => {
  const errors: string[] = [];
  duplicateRunErrors.set(page, errors);
  page.on("console", (message) => {
    if (message.type() === "error" && message.text().includes("same key"))
      errors.push(message.text());
  });
});
test.afterEach(({ page }) => {
  expect(duplicateRunErrors.get(page)).toEqual([]);
});

// Force the real poll response to list a run before its POST response arrives.
// Submission and polling must converge on one sidebar entry for that attempt.
async function submitAfterPoll(page: Page, submit: () => Promise<void>) {
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  let finish!: () => void;
  const delivered = new Promise<void>((resolve) => (finish = resolve));
  let runId = "";
  const handler = async (route: Route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    runId = (await response.json()).id;
    await held;
    await route.fulfill({ response });
    finish();
  };
  await page.route("**/v1/runs", handler);
  try {
    await submit();
    await expect.poll(() => runId).not.toBe("");
    const entry = page.locator(".run-list button").filter({
      hasText: runId.slice(0, 8),
    });
    await expect(entry).toHaveCount(1);
    release();
    await delivered;
    await expect(page.locator(".run-heading code")).toHaveText(
      `RUN ${runId.slice(0, 12)}`,
    );
    await expect(entry).toHaveCount(1);
  } finally {
    release();
    await page.unroute("**/v1/runs", handler);
  }
}

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
  await submitAfterPoll(page, () =>
    page.getByRole("button", { name: "Retry as a new attempt" }).click(),
  );
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

test("guided alpha: saved study, edge notes, resources, stale gate, real manifold flow, case viewer and export", async ({
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
    page.getByRole("button", { name: "Review study", exact: true }),
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
  await page
    .getByLabel("Engineering question")
    .fill("Is the manifold outlet flow evenly divided at the prescribed flow?");
  await page
    .getByLabel("Fluid property source")
    .fill(
      "Water approximation: 1000 kg/m³ and 0.001 Pa·s, user supplied for this comparison",
    );
  await page.getByLabel("Maximum elapsed time (s)").fill("2400");
  await page.getByLabel("Memory ceiling (MiB)").fill("4096");
  await page.getByLabel("Disk ceiling (MiB)").fill("2048");
  await page.getByRole("button", { name: "Save study", exact: true }).click();
  await expect(page.locator(".saved-studies button")).toHaveCount(1);
  await page.getByText("CAD edges (", { exact: false }).click();
  await page.locator(".edge-list button").first().click();
  await page
    .getByRole("button", { name: "Isolate selected reference" })
    .click();
  await page
    .getByRole("button", { name: "Isolate selected reference" })
    .click();
  await page.getByLabel("Reference label").fill("Supply rim");
  await page
    .getByLabel("Annotation", { exact: true })
    .fill("Check the port mapping at this edge.");
  await page
    .getByRole("button", { name: "Save annotation", exact: true })
    .click();
  await expect(page.locator(".note-list")).toContainText("Supply rim");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await expect(page.getByLabel("Mesh cell size (mm)")).toHaveValue("0.625");
  await expect(page.getByLabel("Engineering question")).toHaveValue(
    "Is the manifold outlet flow evenly divided at the prescribed flow?",
  );
  await expect(page.locator(".note-list")).toContainText("Supply rim");
  await page.getByRole("button", { name: "Supply rim", exact: true }).click();
  await expect(page.locator(".reference-detail")).toContainText("edge_");
  await page.getByRole("button", { name: "Review study", exact: true }).click();
  await expect(page.locator(".study-plan")).toContainText("Estimated cells:");
  await expect(
    page.getByRole("button", { name: "Build mesh for review" }),
  ).toBeDisabled();
  await page
    .getByRole("checkbox", { name: "I confirm the units", exact: false })
    .check();
  await page.screenshot({
    path: "../../artifacts/milestone-2/browser-import.png",
    fullPage: true,
  });
  await submitAfterPoll(page, () =>
    page.getByRole("button", { name: "Build mesh for review" }).click(),
  );
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
    path: "../../artifacts/milestone-2/browser-mesh.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Geometry & boundaries" }).click();
  await page
    .getByLabel("Inlet flow (m³/s)")
    .fill(String(study.flow_rate_m3_s * 0.5));
  await page.getByRole("button", { name: "Runs & evidence" }).click();
  await expect(page.locator(".study-currency")).toContainText(
    "Historical inputs",
  );
  await page.reload();
  await expect(page.locator(".study-currency")).toContainText(
    "Historical inputs",
  );
  await expect(
    page.getByRole("button", { name: "Approve saved study & solve" }),
  ).toBeDisabled();
  await page.locator(".saved-studies button").first().click();
  await page.getByRole("button", { name: "Runs & evidence" }).click();
  await expect(page.locator(".study-currency")).toContainText(
    "Current study inputs",
  );
  await submitAfterPoll(page, () =>
    page.getByRole("button", { name: "Approve saved study & solve" }).click(),
  );
  await expect(page.locator(".run-heading code")).not.toHaveText(meshId!);
  await expect(page.locator(".status-pill")).toHaveText("passed", {
    timeout: 180_000,
  });
  await expect(page.locator(".outlet-results tbody tr")).toHaveCount(2);
  await expect(page.locator(".evidence-checks .fail")).toHaveCount(0);
  await expect(page.locator(".outlet-results")).toContainText("50.0");
  await page
    .getByText("Inspect native case, logs & downloads", { exact: true })
    .click();
  await expect(page.getByLabel("File content")).toContainText(
    "laminar-internal/1",
  );
  await page.getByRole("button", { name: "Velocity boundary entries" }).click();
  await expect(page.getByLabel("File content")).toContainText("boundaryField");
  await expect(page.getByLabel("File content")).toContainText("inlet");
  await page.getByLabel("Compare same file with").selectOption({ index: 1 });
  // A mesh-only case may not contain 0/U; use the shared study for a meaningful diff.
  await page
    .getByRole("listitem")
    .filter({ has: page.locator("span", { hasText: /^study.json$/ }) })
    .click();
  await page.getByLabel("Compare same file with").selectOption({ index: 1 });
  await expect(page.getByLabel("File difference")).toContainText(
    "Files are identical",
  );
  await page
    .getByText("Inspect native case, logs & downloads", { exact: true })
    .click();
  await page.screenshot({
    path: "../../artifacts/milestone-2/browser-flow.png",
    fullPage: true,
  });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export complete run" }).click();
  const download = await downloadPromise;
  await download.saveAs("../../artifacts/milestone-2/browser-manifold.zip");
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

test("edited inputs invalidate an in-flight review; declared budget stops real work", async ({
  page,
}) => {
  const headers = { Authorization: "Bearer venturi-e2e-session-only" };
  const fixture = JSON.parse(
    readFileSync("../../fixtures/internal/manifold.study.json", "utf8"),
  );
  await page.request.post(
    "http://127.0.0.1:8766/v1/geometry?filename=manifold.step",
    {
      headers: { ...headers, "Content-Type": "application/octet-stream" },
      data: readFileSync("../../fixtures/internal/manifold.step"),
    },
  );
  const saved = await page.request.post("http://127.0.0.1:8766/v1/studies", {
    headers,
    data: {
      study: { ...fixture, name: "Budget experiment baseline" },
      question: "Check the declared execution budget",
      material_source: "Declared fixture fluid properties",
      profile: "guided",
    },
  });
  expect(saved.ok()).toBeTruthy();
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page
    .locator(".saved-studies button")
    .filter({ hasText: "Budget experiment baseline" })
    .click();
  await page.getByLabel("Study name").fill("Budget-limited manifold");
  let releaseSave!: () => void;
  const saving = new Promise<void>((resolve) => (releaseSave = resolve));
  await page.route(
    "**/v1/studies",
    async (route) => {
      const response = await route.fetch();
      await saving;
      await route.fulfill({ response });
    },
    { times: 1 },
  );
  await page.getByRole("button", { name: "Save study", exact: true }).click();
  await expect(page.getByLabel("Density (kg/m³)")).toBeDisabled();
  await expect(page.getByLabel("Study name")).toBeDisabled();
  releaseSave();
  await expect(page.getByLabel("Study name")).toBeEnabled();
  await page.getByLabel("Presentation").selectOption("expert");
  await expect(page.getByLabel("Maximum solver iterations")).toHaveValue("600");
  await page.getByLabel("Maximum elapsed time (s)").fill("1");
  await page.getByLabel("Flow units").selectOption("lmin");
  await expect(page.getByLabel("Inlet flow (L/min)")).not.toHaveValue("");
  await page.getByLabel("Flow units").selectOption("m3s");
  let release!: () => void;
  const held = new Promise<void>((resolve) => (release = resolve));
  let fetched = false;
  await page.route(
    "**/v1/studies/plan",
    async (route) => {
      const response = await route.fetch();
      fetched = true;
      await held;
      await route.fulfill({ response });
    },
    { times: 1 },
  );
  await page.getByRole("button", { name: "Review study", exact: true }).click();
  await expect.poll(() => fetched).toBeTruthy();
  await page.getByLabel("Density (kg/m³)").fill("1200");
  release();
  await expect(
    page.getByRole("button", { name: "Review study", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Build mesh for review" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Review study", exact: true }).click();
  await expect(page.locator(".study-plan")).toContainText("1200 kg/m³");
  await page
    .getByRole("checkbox", { name: "I confirm the units", exact: false })
    .check();
  await page.getByRole("button", { name: "Build mesh for review" }).click();
  await expect(page.locator(".status-pill")).toHaveText("failed", {
    timeout: 30_000,
  });
  await expect(page.locator(".run-error")).toContainText(/budget|limit/i);
  await expect(
    page.getByRole("button", { name: "Approve saved study & solve" }),
  ).toHaveCount(0);
  await expect(
    page
      .locator(".execution-budget")
      .filter({ hasText: "Saved execution limits" }),
  ).toContainText("1 s");
  const attempt = await page.locator(".run-heading code").textContent();
  await page.reload();
  await expect(page.locator(".run-heading code")).toHaveText(attempt!);
  await expect(page.locator(".status-pill")).toHaveText("failed");
  // An API outage must not trigger submission or erase the retained record.
  const posts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST") posts.push(request.url());
  });
  await page.route("**/v1/runs", (route) => route.abort("connectionrefused"));
  await expect(page.getByRole("alert")).toContainText(
    "Worker connection lost",
    { timeout: 10_000 },
  );
  await expect(
    page.getByText("Worker disconnected", { exact: true }),
  ).toBeVisible();
  await page.unroute("**/v1/runs");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(posts).toEqual([]);
});
