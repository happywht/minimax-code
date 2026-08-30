/**
 * B4 mobile-viewport smoke — the three-pane shell must stay usable on
 * phone (375×667) and tablet (820×1180) widths.
 *
 * The responsive skeleton landed in earlier rounds (viewport meta,
 * <768px sidebar drawer behind the TopBar hamburger, md–lg inspector
 * drawer). This spec pins the contract end-to-end against a real
 * browser viewport:
 *
 *   phone (375):  hamburger visible → drawer opens (role=dialog) →
 *                 backdrop closes it; inspector button hidden (it
 *                 only exists in the md–lg band); chat area has no
 *                 horizontal overflow; the composer is usable.
 *   tablet (820): hamburger hidden; inspector button visible →
 *                 InspectorDrawer opens and closes via backdrop;
 *                 no horizontal overflow.
 *
 * The overflow assertions mirror smoke-boot's "fit a narrow viewport"
 * check: documentElement.scrollWidth must never exceed clientWidth.
 */
import { test, expect, type Page } from "@playwright/test";

async function expectNoHorizontalOverflow(page: Page): Promise<void> {
  const metrics = await page.evaluate(() => ({
    viewportWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.viewportWidth);
}

test.describe("mobile viewport (375×667)", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto("/");
  });

  test("hamburger opens the sidebar drawer and the backdrop closes it", async ({ page }) => {
    const hamburger = page.getByTestId("app-topbar-hamburger");
    await expect(hamburger).toBeVisible();

    await hamburger.click();
    const drawer = page.getByRole("dialog", { name: "导航" });
    await expect(drawer).toBeVisible();

    // Backdrop click dismisses the drawer.
    await page.mouse.click(370, 400);
    await expect(drawer).toBeHidden();
  });

  test("inspector button stays hidden below the md breakpoint", async ({ page }) => {
    await expect(page.getByTestId("app-topbar-inspector")).toBeHidden();
  });

  test("chat area fits the viewport with no horizontal overflow", async ({ page }) => {
    await expect(page.getByTestId("message-input-textarea")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });

  test("composer is usable: typing then send button", async ({ page }) => {
    const textarea = page.getByTestId("message-input-textarea");
    await expect(textarea).toBeVisible();
    await textarea.fill("hello from a narrow viewport");
    await expect(textarea).toHaveValue("hello from a narrow viewport");
    await expectNoHorizontalOverflow(page);
  });
});

test.describe("tablet viewport (820×1180, md–lg band)", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1180 });
    await page.goto("/");
  });

  test("hamburger hidden, inspector button opens and closes the drawer", async ({ page }) => {
    await expect(page.getByTestId("app-topbar-hamburger")).toBeHidden();

    const inspector = page.getByTestId("app-topbar-inspector");
    await expect(inspector).toBeVisible();
    await inspector.click();

    const drawer = page.getByTestId("inspector-drawer");
    await expect(drawer).toBeVisible();
    await expect(page.getByTestId("inspector-drawer-backdrop")).toBeVisible();

    await page.getByTestId("inspector-drawer-backdrop").click({ position: { x: 10, y: 600 } });
    await expect(drawer).toBeHidden();
  });

  test("chat area fits the tablet viewport with no horizontal overflow", async ({ page }) => {
    await expect(page.getByTestId("message-input-textarea")).toBeVisible();
    await expectNoHorizontalOverflow(page);
  });
});
