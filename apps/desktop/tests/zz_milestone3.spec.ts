import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const endpoint = "http://127.0.0.1:8766/v1";
const headers = { Authorization: "Bearer venturi-e2e-session-only" };

test("assistant UI: context consent, typed proposal, approval and saved revision with a simulated provider response", async ({
  page,
}) => {
  const study = JSON.parse(
    readFileSync("../../fixtures/internal/pipe.study.json", "utf8"),
  );
  study.name = "Assistant reviewed pipe";
  const status: any = {
    connected: false,
    connection: null,
    reserved_or_spent_usd: 0,
    requests: [],
  };
  let proposal: any;
  let sent = 0;
  let approvals = 0;
  await page.route("**/v1/assistant", (route) =>
    route.fulfill({ json: status }),
  );
  await page.route("**/v1/assistant/connect", (route) => {
    const connection = route.request().postDataJSON();
    expect(connection.api_key).toBe("test-browser-key-not-live");
    status.connected = true;
    status.connection = {
      model: connection.model,
      budget_usd: connection.budget_usd,
      remember: false,
      structured_output_verified: false,
    };
    return route.fulfill({ json: status });
  });
  await page.route("**/v1/assistant/propose", async (route) => {
    sent++;
    const body = route.request().postDataJSON();
    expect(body.share_context).toBe(true);
    expect(body.context_hash).toMatch(/^[0-9a-f]{64}$/);
    const planned = await page.request.post(endpoint + "/studies/plan", {
      headers,
      data: study,
    });
    expect(planned.ok()).toBeTruthy();
    proposal = {
      id: body.request_id,
      status: "completed",
      can_apply: true,
      cost_microusd: 400,
      proposal_hash: "a".repeat(64),
      plan: await planned.json(),
      message: body.message,
      selection: study.selection,
      proposal: {
        name: study.name,
        question: "What is the pipe pressure drop?",
        material_source: "User supplied water properties",
        physics: "laminar",
        explanation: "Review the stated SI values before applying this study.",
        unresolved_questions: [],
        inputs: [
          {
            field: "flow_rate_m3_s",
            value: study.flow_rate_m3_s,
            units: "m3/s",
            source: "user_supplied",
            evidence: "Flow provided in this message",
          },
          {
            field: "density_kg_m3",
            value: 1000,
            units: "kg/m3",
            source: "user_supplied",
            evidence: "User supplied water density",
          },
        ],
      },
    };
    status.requests = [proposal];
    status.reserved_or_spent_usd = 0.0004;
    await route.fulfill({ json: proposal });
  });
  await page.route("**/v1/assistant/proposals/*/approve", async (route) => {
    approvals++;
    expect(route.request().postDataJSON().proposal_hash).toBe(
      proposal.proposal_hash,
    );
    const response = await page.request.post(endpoint + "/studies", {
      headers,
      data: {
        study,
        question: proposal.proposal.question,
        material_source: proposal.proposal.material_source,
      },
    });
    expect(response.ok()).toBeTruthy();
    proposal.applied = await response.json();
    await route.fulfill({ json: proposal.applied });
  });
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page
    .getByLabel("Import STEP", { exact: true })
    .setInputFiles("../../fixtures/internal/pipe.step");
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(endpoint + "/geometry", { headers })
          ).json()
        ).geometry_hash,
    )
    .toBe(study.selection.geometry_hash);
  const geometry = await (
    await page.request.get(endpoint + "/geometry", { headers })
  ).json();
  for (const face of geometry.faces) {
    const role = study.selection.assignments[face.id];
    if (role === "wall") continue;
    await page.locator(".face-list button").nth(face.index).click();
    await page.getByLabel("SELECTED FACE ROLE").selectOption(role);
  }
  await page.getByRole("button", { name: "Save assignments" }).click();
  await page.getByText("Study assistant · OpenAI", { exact: true }).click();
  await page.getByText("Connect your OpenAI account", { exact: true }).click();
  await page.getByLabel("OpenAI model ID").fill("contract-test-model");
  await page.getByLabel("OpenAI API key").fill("test-browser-key-not-live");
  await page.getByLabel("Input price (USD / million tokens)").fill("1");
  await page.getByLabel("Output price (USD / million tokens)").fill("2");
  await page
    .getByRole("button", { name: "Connect OpenAI", exact: true })
    .click();
  await expect(page.getByLabel("OpenAI API key")).toHaveValue("");
  await page
    .getByLabel("Describe this study")
    .fill(
      "Calculate pressure drop using my flow and water properties in SI units.",
    );
  await expect(
    page.getByRole("button", { name: "Propose study", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Preview context to share" }).click();
  await page
    .getByLabel("Share this message, study, port measurements", {
      exact: false,
    })
    .check();
  await page
    .getByRole("button", { name: "Propose study", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Review proposed study" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Apply approved proposal" }),
  ).toBeDisabled();
  await page
    .getByLabel("I approve these physical inputs", { exact: false })
    .check();
  await page.getByRole("button", { name: "Apply approved proposal" }).click();
  await expect(page.getByLabel("Study name", { exact: true })).toHaveValue(
    study.name,
  );
  await expect(page.getByLabel("Engineering question")).toHaveValue(
    "What is the pipe pressure drop?",
  );
  expect(sent).toBe(1);
  expect(approvals).toBe(1);
  const browserData = await page.evaluate(() =>
    JSON.stringify([localStorage, sessionStorage]),
  );
  expect(browserData).not.toContain("test-browser-key-not-live");
  await page
    .getByText("Conversation and assumption ledger", { exact: false })
    .click();
  await expect(
    page.getByText("Approved · revision 1", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../../artifacts/milestone-3/browser-assistant.png",
    fullPage: true,
  });
});

test("real recovery controls, cancellation, experimental recipe inputs and native scientific views", async ({
  page,
}) => {
  test.setTimeout(180000);
  const runs = await (
    await page.request.get(endpoint + "/runs", { headers })
  ).json();
  const mesh = runs.find(
    (r: any) =>
      r.kind === "internal_mesh" &&
      r.status === "passed" &&
      r.request.study.name.includes("manifold"),
  );
  const flow = runs.find(
    (r: any) => r.kind === "internal_flow" && r.status === "passed",
  );
  expect(mesh).toBeTruthy();
  expect(flow).toBeTruthy();
  const saved = await (
    await page.request.post(endpoint + "/studies", {
      headers,
      data: {
        study: mesh.request.study,
        question: "Review bounded recovery",
        material_source: "Prior approved water properties",
      },
    })
  ).json();
  await page.request.post(endpoint + `/studies/${saved.id}/open`, { headers });
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page
    .locator(".run-list button")
    .filter({ hasText: mesh.id.slice(0, 8) })
    .click();
  await page.getByText("Bounded numerical recovery", { exact: true }).click();
  await page.getByRole("button", { name: "Review recovery plan" }).click();
  await expect(
    page.getByRole("button", { name: "Start approved recovery" }),
  ).toBeDisabled();
  await page
    .getByLabel("I approve this study, mesh and bounded recovery policy")
    .check();
  await page.getByRole("button", { name: "Start approved recovery" }).click();
  await expect(
    page.getByRole("button", { name: "Stop recovery" }),
  ).toBeVisible();
  await page.reload();
  await page.getByText("Bounded numerical recovery", { exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Stop recovery" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Stop recovery" }).click();
  await expect(
    page.getByRole("heading", { name: "Recovery: cancelled" }),
  ).toBeVisible({ timeout: 30000 });
  await page
    .locator(".run-list button")
    .filter({ hasText: flow.id.slice(0, 8) })
    .click();
  await page.getByText("Visual evidence review", { exact: true }).click();
  await page.getByRole("button", { name: "Prepare scientific views" }).click();
  await expect(page.locator(".scientific-view")).toHaveCount(2);
  await expect(
    page.getByText("Quantitative status: passed.", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Request visual observations" }),
  ).toBeDisabled();
  await expect
    .poll(() =>
      page
        .locator(".scientific-view")
        .first()
        .evaluate((img: HTMLImageElement) => img.naturalWidth),
    )
    .toBeGreaterThan(500);
  await page.screenshot({
    path: "../../artifacts/milestone-3/browser-visual-review.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Geometry & boundaries" }).click();
  await page.getByLabel("Flow recipe").selectOption("sst");
  await expect(page.getByLabel("Turbulence intensity (fraction)")).toHaveValue(
    "",
  );
  await expect(page.getByLabel("Turbulence length scale (mm)")).toHaveValue("");
});
