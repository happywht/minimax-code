/**
 * v0.2.0 boot smoke — page renders, agent handshake completes.
 *
 * Verifies:
 *   1. Vite serves the SPA shell (no 500 / unhandled exception).
 *   2. The MiniMax Code brand string is visible.
 *   3. The agent `/health` endpoint responds ok from the page context
 *      (proves the CORS config lets the browser talk to the agent).
 *   4. The v0.3.0 top bar contains the new ``GitStatusBar`` widget
 *      (proves the git IPC namespace is wired into the shell).
 *
 * Does NOT depend on the agent being reachable on first paint — the
 * web client is designed to fall back to mock mode if /health fails.
 * So this spec is the happy-path "everything is up" assertion; the
 * agent-rpc spec is the stricter "real round-trip" check.
 */
import { test, expect } from "@playwright/test";
import { AGENT_BASE } from "./runtime-config";

test("boot: page renders and agent health probe succeeds", async ({ page, request }) => {
  // Direct API probe — independent of the page so a UI hang doesn't
  // mask a real agent outage. We can run this without the webServer.
  const health = await request.get(`${AGENT_BASE}/health`, {
    timeout: 5_000,
  });
  expect(health.ok(), "agent /health must return 2xx").toBeTruthy();
  const body = await health.json();
  expect(body.ok).toBe(true);
  expect(typeof body.version).toBe("string");
  expect(body.version.length).toBeGreaterThan(0);

  // UI probe — Vite dev page renders.
  await page.goto("/");
  // The brand mark is in the sidebar's collapsed view AND the top
  // bar — either is fine. Use the sidebar's text to avoid matching
  // the document title in the head.
  await expect(page.getByText("MiniMax Code").first()).toBeVisible({
    timeout: 10_000,
  });

  // v0.3.0: the top bar must contain the GitStatusBar widget
  // (data-testid="git-status-bar"). We don't assert the branch
  // text — the project is in a real git repo but the agent
  // subprocess runs from its own CWD, so the displayed branch
  // depends on the agent's environment, not the test runner's.
  await expect(page.locator("[data-testid='git-status-bar']").first()).toBeVisible({
    timeout: 10_000,
  });
});

test("boot: demo mode points directly to provider configuration", async ({ page }) => {
  await page.goto("/");

  const banner = page.getByTestId("provider-readiness-banner");
  await expect(banner).toBeVisible({ timeout: 10_000 });
  await expect(banner).toContainText("演示模式");
  await expect(banner).toContainText("模拟响应");

  await page.getByTestId("provider-readiness-action").click();
  await expect(page.getByTestId("settings-providers")).toBeVisible();
  await expect(page.getByTestId("settings-tab-providers")).toHaveAttribute(
    "aria-selected",
    "true",
  );
});

test("settings: provider and model tabs fit a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  await page.getByTestId("provider-readiness-action").click();
  await expect(page.getByTestId("settings-providers")).toBeVisible();

  const expectNoHorizontalOverflow = async () => {
    const metrics = await page.evaluate(() => ({
      viewportWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth);

    const dialog = await page.getByTestId("settings-page").boundingBox();
    expect(dialog).not.toBeNull();
    expect(dialog!.x).toBeGreaterThanOrEqual(0);
    expect(dialog!.x + dialog!.width).toBeLessThanOrEqual(metrics.viewportWidth);
  };

  await expectNoHorizontalOverflow();
  await page.getByTestId("settings-provider-builtin-minimax-expand").click();
  await expect(page.getByTestId("settings-provider-builtin-minimax-key-input")).toBeVisible();
  await expectNoHorizontalOverflow();

  await page.getByTestId("settings-tab-models").click();
  await expect(page.getByTestId("settings-models")).toBeVisible();
  await expectNoHorizontalOverflow();
});

test("settings: management forms stay labeled and usable on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByTestId("provider-readiness-action").click();

  const expectFullWidth = async (control: ReturnType<typeof page.getByLabel>) => {
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(300);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    );
    expect(overflow).toBe(false);
  };

  await page.getByTestId("settings-tab-permissions").click();
  await expectFullWidth(page.getByLabel("工具"));
  await expectFullWidth(page.getByLabel("决策", { exact: true }));

  await page.getByTestId("settings-tab-scheduled").click();
  await expectFullWidth(page.getByLabel("任务名称"));
  await expectFullWidth(page.getByLabel("Cron 表达式"));

  await page.getByTestId("settings-tab-agents").click();
  await page.getByTestId("settings-agent-create").click();
  await expectFullWidth(page.getByLabel("Agent 名称"));

  await page.getByTestId("settings-tab-teams").click();
  await page.getByTestId("settings-team-create").click();
  await expectFullWidth(page.getByLabel("名称"));
  await expectFullWidth(page.getByLabel("编排模式"));

  await page.getByTestId("settings-tab-webhooks").click();
  await page.getByTestId("webhook-create-btn").click();
  await expectFullWidth(page.getByLabel("来源"));
  await expectFullWidth(page.getByLabel("动作"));

  await page.getByTestId("settings-tab-workflows").click();
  await page.getByTestId("workflow-create-btn").click();
  await expectFullWidth(page.getByLabel("触发器"));
  await expectFullWidth(page.getByLabel("描述"));

  await page.getByTestId("settings-tab-audit").click();
  await expect(page.getByLabel("按工具筛选：")).toBeVisible();
});

test("settings: destructive provider deletion requires explicit confirmation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByTestId("provider-readiness-action").click();
  await expect(page.getByTestId("settings-providers")).toBeVisible();

  await page.getByTestId("settings-provider-add").click();
  await page.getByLabel("名称", { exact: true }).fill("E2E Provider");
  await page.getByLabel("Base URL").fill("https://example.invalid/v1");
  await page.getByTestId("settings-provider-form-submit").click();

  const providerCard = page.locator('li[data-testid^="settings-provider-"]').filter({
    hasText: "E2E Provider",
  });
  await expect(providerCard).toBeVisible();

  await providerCard.getByRole("button", { name: "删除 Provider" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("删除 E2E Provider");
  await expect(page.getByTestId("confirmation-cancel")).toBeFocused();
  await page.getByTestId("confirmation-cancel").click();
  await expect(providerCard).toBeVisible();

  await providerCard.getByRole("button", { name: "删除 Provider" }).click();
  await page.getByTestId("confirmation-confirm").click();
  await expect(providerCard).toHaveCount(0);
});
