import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { AGENT_PORT, WEB_BASE } from "./runtime-config";

// The iframe and its assets must work with browser cookies, without the
// suite's usual Authorization header concealing a cross-site cookie failure.
test.use({ extraHTTPHeaders: {} });

test("localhost UI authenticates a project preview and relative assets", async ({ page, context }) => {
  await page.goto(WEB_BASE.replace("127.0.0.1", "localhost"));
  await expect(page.getByTestId("app-root")).toBeVisible();
  const root = fileURLToPath(new URL(`./.runtime/${AGENT_PORT}/workspace`, import.meta.url));
  await page.evaluate(async (rootPath) => {
    // @ts-expect-error Vite serves source modules to the test browser.
    const { ipc } = await import("/src/ipc/index.ts");
    // @ts-expect-error Vite serves source modules to the test browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    const result = await ipc.request("project.create", { name: "Preview fixture", root_path: rootPath });
    return useSessionStore.getState().create("Preview cookie test", result.project.id);
  }, root);
  const stylesheet = page.waitForResponse((response) => response.url().endsWith("/preview.css"));
  await page.getByTestId("app-topbar-preview").click();
  const iframe = page.getByTestId("preview-iframe");
  await expect(iframe).toHaveAttribute("src", new RegExp(`^http://localhost:${AGENT_PORT}/preview/view/[^/]+/index.html$`));
  await expect(page.frameLocator('[data-testid="preview-iframe"]').getByRole("heading"))
    .toHaveText("Authenticated preview fixture");
  await expect(page.frameLocator('[data-testid="preview-iframe"]').getByRole("heading"))
    .toHaveCSS("color", "rgb(0, 128, 0)");
  await expect(page.frameLocator('[data-testid="preview-iframe"]').locator("body"))
    .toHaveAttribute("data-module-ready", "yes");
  await expect(page.frameLocator('[data-testid="preview-iframe"]').locator("body"))
    .toHaveAttribute("data-parent-access", "blocked");
  const css = await stylesheet;
  expect(css.status()).toBe(200);
  expect(css.request().headers().authorization).toBeUndefined();
  expect(await iframe.getAttribute("src")).not.toContain("token");
  const cookies = await context.cookies(`http://localhost:${AGENT_PORT}`);
  expect(cookies).toEqual(expect.arrayContaining([expect.objectContaining({
    name: `minimax_session_${AGENT_PORT}`, httpOnly: true, sameSite: "Strict",
  })]));
});
