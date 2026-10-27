import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { AGENT_BASE } from "./runtime-config";

test("attachment originals survive reload, display honest states and download exact version bytes", async ({ page, request }) => {
  const created = await request.post(`${AGENT_BASE}/rpc`, { data: {
    jsonrpc: "2.0", id: "project", method: "project.create", params: { name: "Attachment originals" },
  } });
  const body = await created.json(); expect(body.error).toBeUndefined();
  const projectId = body.result.project.id;
  await page.goto("/"); await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await page.evaluate(async (id) => {
    // @ts-expect-error Vite source module resolves inside browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().loadProjects(); useSessionStore.getState().setCurrentProject(id);
  }, projectId);
  await page.getByTestId("sidebar-nav-attachments").click();
  const panel = page.getByRole("region", { name: "项目附件" });
  await expect(panel.getByText("当前项目还没有附件原件。")).toBeVisible();
  const original = Buffer.from("\ufeff<script>window.attachmentExploit=true</script>\r\n=1+1\r\n");
  const uploadRequest = page.waitForRequest((req) => req.method() === "POST" && req.url().includes("/attachments?"));
  await panel.getByLabel("选择附件原件").setInputFiles({ name: "report.txt", mimeType: "text/plain", buffer: original });
  await panel.getByRole("button", { name: "上传原件" }).click();
  await expect(panel.getByLabel("附件纯文本预览")).toHaveText(original.toString("utf8"));
  expect(await page.evaluate(() => (window as Window & { attachmentExploit?: boolean }).attachmentExploit)).toBeUndefined();
  const sent = await uploadRequest;
  expect(sent.headers()["x-attachment-service-id"]).toMatch(/^svc_/); expect(sent.headers().authorization).toMatch(/^Bearer /);
  // Chromium omits File-body postDataBuffer; verify persisted raw bytes below.
  expect(sent.url()).not.toMatch(/token|auth/i);
  await expect(panel.getByText(`SHA-256: ${createHash("sha256").update(original).digest("hex")}`)).toBeVisible();
  await panel.getByLabel("选择附件原件").setInputFiles({ name: "report.txt", mimeType: "text/plain", buffer: Buffer.from("version two") });
  await panel.getByRole("button", { name: "上传原件" }).click();
  await expect(panel.getByRole("button", { name: /report.txt · v2/ })).toBeVisible();
  await panel.getByRole("button", { name: /report.txt · v1/ }).click();
  await expect(panel.getByLabel("附件纯文本预览")).toHaveText(original.toString("utf8"));
  const downloadEvent = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载附件原始字节" }).click();
  const downloaded = await downloadEvent, path = await downloaded.path(); expect(path).not.toBeNull();
  const bytes = readFileSync(path!); expect(bytes).toEqual(original);
  await expect(panel.getByText(`SHA-256: ${createHash("sha256").update(bytes).digest("hex")}`)).toBeVisible();
  await panel.getByLabel("选择附件原件").setInputFiles({ name: "paper.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.7\noriginal only") });
  await panel.getByRole("button", { name: "上传原件" }).click();
  await expect(panel.getByRole("button", { name: /paper.pdf.*解析器不可用 \/ 未验证/ })).toBeVisible();
  await expect(panel.getByText("当前状态不提供文本预览；可下载保存的原件。")).toBeVisible();
  await page.reload(); await page.getByTestId("sidebar-nav-attachments").click();
  await expect(page.getByRole("button", { name: /report.txt · v1/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /report.txt · v2/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /paper.pdf.*解析器不可用/ })).toBeVisible();
});
