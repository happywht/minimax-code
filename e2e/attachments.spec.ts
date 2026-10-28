import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
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
  await expect(panel.getByRole("button", { name: /paper.pdf.*解析失败 \/ 未验证/ })).toBeVisible();
  await expect(panel.getByText(/解析算法 v2.*inert_text.*原因 invalid_package/)).toBeVisible();
  await expect(panel.getByText("当前状态不提供文本预览；可下载保存的原件。")).toBeVisible();
  const badDownloadEvent = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载附件原始字节" }).click();
  expect(readFileSync((await (await badDownloadEvent).path())!)).toEqual(Buffer.from("%PDF-1.7\noriginal only"));
  await page.reload(); await page.getByTestId("sidebar-nav-attachments").click();
  await expect(page.getByRole("button", { name: /report.txt · v1/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /report.txt · v2/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /paper.pdf.*解析失败/ })).toBeVisible();
});

test("DOCX body scope, table positions and original bytes survive upload and reload", async ({ page, request }) => {
  const response = await request.post(`${AGENT_BASE}/rpc`, { data: {
    jsonrpc: "2.0", id: "docx-project", method: "project.create", params: { name: "DOCX body" },
  } });
  const projectId = (await response.json()).result.project.id;
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await page.evaluate(async (id) => {
    // @ts-expect-error Vite source module resolves inside browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().loadProjects(); useSessionStore.getState().setCurrentProject(id);
  }, projectId);
  await page.getByTestId("sidebar-nav-attachments").click();
  const panel = page.getByRole("region", { name: "项目附件" });
  const original = readFileSync(fileURLToPath(new URL("./fixtures/body-text.docx", import.meta.url)));
  await panel.getByLabel("选择附件原件").setInputFiles({ name: "body.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: original });
  await panel.getByRole("button", { name: "上传原件" }).click();
  const preview = panel.getByLabel("附件纯文本预览");
  await expect(preview).toContainText("[body/1] 中文开头");
  await expect(preview).toContainText("[body/2/row1/cell1/1] 表格正文");
  await expect(preview).toContainText("[body/3] 中文结尾");
  await expect(preview).not.toContainText("不得出现的页眉");
  await expect(panel.getByText(/解析算法 v2.*docx_body_paragraphs_tables.*原因 ok/)).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载附件原始字节" }).click();
  expect(readFileSync((await (await downloadEvent).path())!)).toEqual(original);
  await expect(panel.getByText(`SHA-256: ${createHash("sha256").update(original).digest("hex")}`)).toBeVisible();
  await page.reload(); await page.getByTestId("sidebar-nav-attachments").click();
  await page.getByRole("button", { name: /body.docx.*已解析 \/ 未验证/ }).click();
  await expect(page.getByLabel("附件纯文本预览")).toContainText("[body/2/row1/cell1/1] 表格正文");
});

test("PDF physical page text, blank-page OCR boundary and original bytes survive reload", async ({ page, request }) => {
  const response = await request.post(`${AGENT_BASE}/rpc`, { data: {
    jsonrpc: "2.0", id: "pdf-project", method: "project.create", params: { name: "PDF page text" },
  } });
  const body = await response.json(); expect(body.error).toBeUndefined();
  const projectId = body.result.project.id;
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await page.evaluate(async (id) => {
    // @ts-expect-error Vite source module resolves inside browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().loadProjects(); useSessionStore.getState().setCurrentProject(id);
  }, projectId);
  await page.getByTestId("sidebar-nav-attachments").click();
  const panel = page.getByRole("region", { name: "项目附件" });
  await expect(panel.getByText("当前项目还没有附件原件。")).toBeVisible();
  const original = readFileSync(fileURLToPath(new URL("./fixtures/body-text.pdf", import.meta.url)));
  const digest = createHash("sha256").update(original).digest("hex");
  await panel.getByLabel("选择附件原件").setInputFiles({ name: "pages.pdf", mimeType: "application/pdf", buffer: original });
  await panel.getByRole("button", { name: "上传原件" }).click();
  await expect(panel.getByRole("button", { name: /pages.pdf.*已解析 \/ 未验证/ })).toBeVisible();
  const preview = panel.getByLabel("附件纯文本预览");
  const expected = "[page 1]\nASCII first page\n[page 2]\n[no extracted text; OCR not attempted]\n[page 3]\n中文第三页\n";
  await expect(preview).toHaveText(expected);
  await expect(panel.getByText(/解析算法 v2.*inert_text.*原因 ok/)).toBeVisible();
  await expect(panel.getByText(/识别候选: pdf.*已解析.*未验证/)).toBeVisible();
  await expect(panel.getByText(/页码为物理页序号.*未尝试 OCR/)).toBeVisible();
  await expect(panel.getByText(`SHA-256: ${digest}`)).toBeVisible();
  await page.reload();
  await page.getByTestId("sidebar-nav-attachments").click();
  await panel.getByRole("button", { name: /pages.pdf.*已解析 \/ 未验证/ }).click();
  await expect(preview).toHaveText(expected);
  await expect(panel.getByText(/解析算法 v2.*inert_text.*原因 ok/)).toBeVisible();
  const downloadEvent = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载附件原始字节" }).click();
  const downloaded = readFileSync((await (await downloadEvent).path())!);
  expect(downloaded).toEqual(original);
  expect(createHash("sha256").update(downloaded).digest("hex")).toBe(digest);
  await expect(panel.getByText(`SHA-256: ${digest}`)).toBeVisible();
});
