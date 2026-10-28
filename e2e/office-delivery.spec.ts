import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { AGENT_BASE, AGENT_PORT } from "./runtime-config";

test("manual XLSX generation verifies full bytes, preserves originals, versions and refreshes", async ({ page, request }) => {
  const root = resolve("e2e/.runtime", String(AGENT_PORT), "workspace", "office-project");
  mkdirSync(root, { recursive: true });
  const original = Buffer.from("preexisting workspace original\r\n");
  writeFileSync(join(root, "清单.xlsx"), original);
  const rpc = async (method: string, params: unknown) => {
    const response = await request.post(`${AGENT_BASE}/rpc`, { data: { jsonrpc: "2.0", id: method, method, params } });
    expect(response.status()).toBe(200); return response.json();
  };
  const created = await rpc("project.create", { name: "Office fixture", root_path: root });
  expect(created.error).toBeUndefined();
  const project = created.result.project.id;
  const spec = { sheet_name: "清单", columns: [{ name: "编号", kind: "text" }, { name: "数量", kind: "number" }],
    rows: Array.from({ length: 120 }, (_, index) => [index === 119 ? "=literal tail" : "<script>window.officeExploit=true</script>", index]) };
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await page.evaluate(async (id) => {
    // @ts-expect-error Vite source module is resolved in the browser.
    const { useSessionStore } = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().loadProjects(); useSessionStore.getState().setCurrentProject(id);
  }, project);
  await page.getByTestId("sidebar-nav-artifacts").click();
  await page.getByRole("button", { name: "办公成果", exact: true }).click();
  const panel = page.getByRole("region", { name: "办公成果", exact: true });
  await expect(panel.getByRole("button", { name: "生成并登记 XLSX" })).toBeEnabled();
  await panel.getByLabel("结构化表格 JSON").fill(JSON.stringify(spec));
  await panel.getByRole("button", { name: "生成并登记 XLSX" }).click();
  await expect(panel.getByRole("button", { name: /清单.xlsx · v1/ })).toBeVisible();
  await expect(panel.getByLabel("办公成果纯文本预览")).toContainText("<script>window.officeExploit=true</script>");
  await expect(panel.getByText(/预览已截断/)).toBeVisible();
  expect(await page.evaluate(() => (window as Window & { officeExploit?: boolean }).officeExploit)).toBeUndefined();
  const list = (await rpc("office.list", { project_id: project })).result;
  expect(list.deliveries).toHaveLength(1);
  const first = list.deliveries[0];
  expect(first.delivery.validation_report).toMatchObject({ cell_count: 242, data_rows: 120, business_quality: "unverified", render_status: "not_attempted" });
  const capture = page.waitForRequest((req) => req.url().includes("/attachments/") && req.url().includes("/content?"));
  const event = page.waitForEvent("download");
  await panel.getByRole("button", { name: "下载 XLSX 原始字节" }).click();
  const download = await event, path = await download.path();
  expect(path).not.toBeNull();
  const data = readFileSync(path!);
  expect(createHash("sha256").update(data).digest("hex")).toBe(first.attachment.sha256);
  expect(readFileSync(join(root, "清单.xlsx"))).toEqual(original);
  const downloaded = await capture;
  expect(downloaded.url()).not.toMatch(/token|auth/i);
  expect(downloaded.headers().authorization).toMatch(/^Bearer /);
  const input = join(root, "office-input.json"); writeFileSync(input, JSON.stringify(spec));
  const python = resolve("agent/.venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const report = JSON.parse(execFileSync(python, ["-m", "minimax_code.office_xlsx", "verify", path!, "--spec", input], { cwd: resolve("agent"), encoding: "utf8", timeout: 20_000 }));
  expect(report).toEqual(first.delivery.validation_report);
  await panel.getByRole("button", { name: "生成同名新版本" }).click();
  await expect(panel.getByRole("button", { name: /清单.xlsx · v2/ })).toBeVisible();
  await panel.getByRole("button", { name: /清单.xlsx · v1/ }).click();
  await expect(panel.getByText(`SHA-256: ${first.attachment.sha256}`)).toBeVisible();
  await page.reload();
  await page.getByTestId("sidebar-nav-artifacts").click();
  await page.getByRole("button", { name: "办公成果", exact: true }).click();
  await expect(panel.getByRole("button", { name: /清单.xlsx · v1/ })).toBeVisible();
  await expect(panel.getByRole("button", { name: /清单.xlsx · v2/ })).toBeVisible();
  const rejected = await rpc("office.generate_xlsx", { project_id: project, expected_service_id: list.service_id,
    request_key: "invalid-rows", filename: "bad.xlsx", spec: { ...spec, rows: [[true, 1]] } });
  expect(rejected.error.code).toBe(-32602);
  expect((await rpc("office.list", { project_id: project })).result.deliveries).toHaveLength(2);
  expect(readFileSync(join(root, "清单.xlsx"))).toEqual(original);
});
