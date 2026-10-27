import { expect, test } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, join } from "node:path";
import { AGENT_BASE, AGENT_PORT } from "./runtime-config";

test("current project registers versions, escapes preview and downloads immutable original bytes", async ({ page, request }) => {
  const root = resolve("e2e/.runtime", String(AGENT_PORT), "workspace", "text-artifact-project");
  mkdirSync(join(root, "reports"), { recursive: true });
  const original = Buffer.from("\ufeff# Original\r\n<script>window.artifactExploit=true</script>\r\n", "utf8");
  const source = join(root, "reports", "report.md");
  writeFileSync(source, original);
  const created = await request.post(`${AGENT_BASE}/rpc`, { data: {
    jsonrpc: "2.0", id: "project", method: "project.create", params: { name: "Text artifact fixture", root_path: root },
  } });
  const body = await created.json();
  expect(body.error).toBeUndefined();
  const projectId = body.result.project.id;
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await page.evaluate(async (id) => {
    // @ts-expect-error Vite source module is resolved in the browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().loadProjects();
    useSessionStore.getState().setCurrentProject(id);
  }, projectId);
  await page.getByTestId("sidebar-nav-artifacts").click();
  const panel = page.getByRole("region", { name: "项目文本成果" });
  await expect(panel.getByRole("heading")).toContainText("Text artifact fixture");
  await panel.getByLabel("项目相对文件路径").fill("reports/report.md");
  await panel.getByRole("button", { name: "登记新版本" }).click();
  await expect(panel.getByLabel("成果纯文本预览")).toHaveText(original.toString("utf8"));
  expect(await page.evaluate(() => (window as Window & { artifactExploit?: boolean }).artifactExploit)).toBeUndefined();
  writeFileSync(source, "Changed source\n", "utf8");
  await panel.getByRole("button", { name: "登记新版本" }).click();
  await expect(panel.getByRole("button", { name: /report.md · v2/ })).toBeVisible();
  await panel.getByRole("button", { name: /report.md · v1/ }).click();
  await expect(panel.getByLabel("成果纯文本预览")).toHaveText(original.toString("utf8"));
  const contentRequest = page.waitForRequest((req) => req.url().includes("/artifacts/") && req.url().includes("/content?"));
  const downloadEvent = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载原始字节" }).click();
  const download = await downloadEvent;
  const path = await download.path();
  expect(path).not.toBeNull();
  expect(readFileSync(path!)).toEqual(original);
  expect(readFileSync(source, "utf8")).toBe("Changed source\n");
  const downloaded = await contentRequest;
  expect(downloaded.url()).not.toMatch(/token|auth/i);
  expect(downloaded.headers().authorization).toMatch(/^Bearer /);
  await page.reload();
  await page.getByTestId("sidebar-nav-artifacts").click();
  await expect(page.getByRole("button", { name: /report.md · v1/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /report.md · v2/ })).toBeVisible();
});
