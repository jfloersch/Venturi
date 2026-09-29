import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";

const noPayments = process.env.VENTURI_SKIP_PAYMENT_TESTS === "1";

test(`Managed tariff approval, wallet, ${noPayments ? "" : "checkout and "}disconnect with simulated service`, async ({
  page,
}) => {
  let connected = false;
  let checkoutCalls = 0;
  const tariff = {
    model: "evaluated-test-model",
    version: "test-1",
    input_usd_per_million: "1",
    output_usd_per_million: "2",
    markup: "2.5",
  };
  const assistant = () => ({
    connected,
    connection: connected
      ? {
          provider: "managed",
          model: tariff.model,
          budget_usd: 5,
          remember: false,
          structured_output_verified: false,
        }
      : null,
    requests: [],
    reserved_or_spent_usd: 0,
  });
  await page.route("**/v1/managed", (route) =>
    route.fulfill({
      json: {
        configured: true,
        connected,
        url: "https://managed.example.test",
        tariff,
        tariff_hash: "a".repeat(64),
        top_up_cents: [2000, 5000],
      },
    }),
  );
  await page.route("**/v1/assistant", (route) =>
    route.fulfill({ json: assistant() }),
  );
  await page.route("**/v1/managed/connect", (route) => {
    const body = route.request().postDataJSON();
    expect(body.tariff_hash).toBe("a".repeat(64));
    expect(body.budget_usd).toBe(5);
    expect(body.study_budget_usd).toBe(1);
    connected = true;
    return route.fulfill({ json: assistant() });
  });
  await page.route("**/v1/managed/wallet", (route) =>
    route.fulfill({
      json: {
        available_microusd: 20000000,
        requests: [],
        entries: [],
        limits: [],
      },
    }),
  );
  await page.route("**/v1/managed/checkout", (route) => {
    expect(noPayments).toBe(false);
    checkoutCalls++;
    expect(route.request().postDataJSON().amount_cents).toBe(2000);
    return route.fulfill({
      json: {
        url: "https://checkout.stripe.com/c/simulated",
        status: "pending",
        order_id: "order-simulated",
      },
    });
  });
  await page.route("**/v1/assistant/disconnect", (route) => {
    connected = false;
    return route.fulfill({ json: assistant() });
  });
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page.getByText("Account, privacy and support", { exact: true }).click();
  await page.getByText("Venturi Managed · prepaid AI", { exact: true }).click();
  await page.getByLabel("Managed account name").fill("test-user");
  await page
    .getByLabel("Managed password", { exact: true })
    .fill("long-test-password");
  await expect(
    page.getByRole("button", { name: "Use Venturi Managed", exact: true }),
  ).toBeDisabled();
  await page
    .getByLabel(
      "Use Managed billing at the displayed tariff and limits for future approved AI requests",
    )
    .check();
  await page
    .getByRole("button", { name: "Use Venturi Managed", exact: true })
    .click();
  await expect(
    page.getByText("Prepaid wallet · $20.000000 available"),
  ).toBeVisible();
  await expect(
    page.getByLabel("Managed password", { exact: true }),
  ).toHaveValue("");
  if (!noPayments) {
    await page.getByRole("button", { name: "Add $20.00", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Open secure checkout" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Add $20.00", exact: true }),
    ).toBeDisabled();
    expect(checkoutCalls).toBe(1);
  } else {
    expect(checkoutCalls).toBe(0);
  }
  mkdirSync("../../artifacts/milestone-4", { recursive: true });
  await page.screenshot({
    path: "../../artifacts/milestone-4/managed-wallet.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Disconnect Managed", exact: true })
    .click();
  await expect(
    page.getByText("Prepaid wallet · $20.000000 available"),
  ).not.toBeVisible();
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
});

test("support export requires preview and consent and contains no project data", async ({
  page,
}) => {
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  await page.getByText("Account, privacy and support", { exact: true }).click();
  await page.getByText("Privacy and support report", { exact: true }).click();
  await page.getByRole("button", { name: "Preview support report" }).click();
  await expect(
    page.getByRole("button", { name: "Save reviewed support report" }),
  ).toBeDisabled();
  await page
    .getByLabel(
      "I reviewed these diagnostics and want to save a support report",
    )
    .check();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", { name: "Save reviewed support report" }).click(),
  ]);
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(chunk);
  const report = JSON.parse(Buffer.concat(chunks).toString());
  expect(report.schema).toBe("venturi.support.v1");
  expect(report.automatic_upload).toBe(false);
  expect(report.excluded).toContain("credentials");
  expect(report).not.toHaveProperty("geometry");
  expect(report).not.toHaveProperty("account_id");
  expect(report).not.toHaveProperty("requests");
});

test("worker connection retries network failures and the same session can reconnect", async ({
  page,
}) => {
  let failures = 2;
  await page.route("**/v1/diagnostics", (route) => {
    if (failures > 0) {
      failures--;
      return route.abort("connectionrefused");
    }
    return route.continue();
  });
  await page.goto("/#token=venturi-e2e-session-only");
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
  expect(failures).toBe(0);
  await page
    .getByRole("button", { name: "Worker connection", exact: true })
    .click();
  failures = 100;
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Failed to fetch", {
    timeout: 10000,
  });
  failures = 0;
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByText("worker connected", { exact: false }),
  ).toBeVisible();
});
