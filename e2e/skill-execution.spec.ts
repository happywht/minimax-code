import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { resolve, join } from "node:path";
import { AGENT_BASE, AGENT_PORT } from "./runtime-config";

test("Native review uses selected Git workspace and reloads its actual durable receipt", async ({ page, request }) => {
  const rpc = async (method: string, params: Record<string, unknown>) => {
    const response = await request.post(`${AGENT_BASE}/rpc`, {data:{jsonrpc:"2.0",id:method,method,params}});
    const body = await response.json();
    expect(body.error).toBeUndefined();
    return body.result;
  };
  const sessions: {id:string; workspace:string; file:string}[] = [];
  for (const suffix of ["a", "b"]) {
    const workspace = resolve("e2e/.runtime", String(AGENT_PORT), "workspace", `review-${suffix}`);
    mkdirSync(workspace, {recursive:true});
    const file = `only_${suffix}.py`;
    writeFileSync(join(workspace,file), "value = 1\n");
    const git = (...args:string[]) => execFileSync("git", args, {cwd:workspace,stdio:"pipe"});
    git("init"); git("add", ".");
    git("-c", "user.name=Offline test", "-c", "user.email=offline@example.invalid", "commit", "-m", "fixture");
    writeFileSync(join(workspace,file), "value = 2\nprint(value)\n");
    const {session} = await rpc("session.create", {title:`Review ${suffix}`,engine:"native",workspace_path:workspace});
    sessions.push({id:session.id, workspace, file});
  }
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  const select = async (id:string) => {
    await page.evaluate(async (sessionId) => {
      // @ts-expect-error Vite source module is available to the browser.
      const {useSessionStore} = await import("/src/stores/sessionStore.ts");
      await useSessionStore.getState().refresh();
      useSessionStore.getState().setCurrent(sessionId);
    }, id);
    await page.getByTestId("right-panel-tab-review").click();
    await expect(page.getByTestId("code-review-binding")).toContainText(sessions.find((s) => s.id === id)!.workspace);
  };
  await select(sessions[1].id);
  await select(sessions[0].id);
  await page.getByTestId("code-review-run-btn").click();
  await expect(page.getByTestId("code-review-run-status")).toContainText("completed");
  const {runs} = await rpc("run.list", {session_id:sessions[0].id});
  expect(runs).toHaveLength(1);
  const {run, result} = await rpc("run.get", {run_id:runs[0].id});
  expect(result.run_id).toBe(run.id);
  expect(result.skill_id).toBe("code-review:code-review");
  expect(result.output).toBe(result.text);
  expect(result.stats).toEqual({files:1,additions:2,deletions:1});
  expect(run.metadata.skill_execution.workspace).toBe(sessions[0].workspace);
  expect(run.metadata.skill_execution.diff).toContain(sessions[0].file);
  expect(run.metadata.skill_execution.diff).not.toContain(sessions[1].file);
  expect((await rpc("run.list", {session_id:sessions[1].id})).runs).toHaveLength(0);
  const key = await page.evaluate((id) => JSON.parse(localStorage.getItem(`minimax-code:reviews:${id}`)!).overview.args.idempotency_key, sessions[0].id);
  await page.reload();
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await select(sessions[0].id);
  await expect(page.getByTestId("code-review-run-status")).toContainText("completed");
  const restored = await page.evaluate((id) => JSON.parse(localStorage.getItem(`minimax-code:reviews:${id}`)!).overview, sessions[0].id);
  expect(restored.runId).toBe(run.id);
  expect(restored.args.idempotency_key).toBe(key);
  expect((await rpc("run.get", {run_id:run.id})).result).toEqual(result);
  await page.getByTestId("code-review-open-run").click();
  await expect(page.getByTestId("right-panel-timeline-body")).toBeVisible();
  await expect(page.getByTestId("right-panel-timeline-panel")).toContainText("completed");
});

test("review controls show controlled interrupted, failed and budget-paused receipts honestly", async ({page, request}) => {
  // Browser display/cancellation contract fixture. The preceding test verifies
  // real admission and SQLite recovery; backend tests own actual budget/process faults.
  const response = await request.post(`${AGENT_BASE}/rpc`, {data:{jsonrpc:"2.0",id:1,method:"session.create",params:{title:"Receipt states",engine:"native"}}});
  const selectedSession = (await response.json()).result.session;
  const sid = selectedSession.id as string;
  await page.goto("/");
  await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  const rows: Record<string, {id:string;session_id:string;mode:string;status:string;title:string;created_at:string;metadata:Record<string,unknown>}> = {};
  const keys = new Set<string>();
  const cancelled:string[]=[];
  await page.route("**/rpc", async (route) => {
    const body = route.request().postDataJSON();
    let result: unknown;
    const params = body.params ?? {};
    if (body.method === "git.diff" && params.session_id === sid) result = {diff:"+++ fixture.py",scope:"working",workspace:"fixture-workspace",session_id:sid};
    else if (body.method === "skill.invoke" && params.session_id === sid) {
      expect(params.expected_workspace).toBe("fixture-workspace");
      keys.add(params.idempotency_key);
      const label = params.label as string;
      rows[label] = {id:`fixture-${label}`,session_id:sid,mode:"execute",status:label==="security"?"running":label==="performance"?"failed":"budget_paused",
        title:`${label} fixture`,created_at:new Date().toISOString(),metadata:{skill_execution:{skill_id:"code-review:code-review",label}}};
      result = {run:rows[label],created:true};
    } else if (body.method === "run.list" && params.session_id === sid) result = {runs:Object.values(rows)};
    else if (body.method === "run.get" && String(params.run_id).startsWith("fixture-")) {
      const row = rows[String(params.run_id).slice("fixture-".length)];
      result = {run:row,result:{run_id:row.id,session_id:sid,skill_id:"code-review:code-review",status:row.status,run_status:row.status,
        text:`Retained ${row.title} text`,output:`Retained ${row.title} text`,iterations:1,tool_calls:0,
        partial:row.status!=="running",cancelled:row.status==="cancelled",truncated:false,budget_exhausted:row.status==="budget_paused",
        ...(row.status==="failed"?{error:{code:-32004,message:"Analyzer storage refused"}}:{})}};
    } else if (body.method === "run.cancel" && String(params.run_id).startsWith("fixture-")) {
      cancelled.push(params.run_id);
      rows[String(params.run_id).slice("fixture-".length)].status="cancelled";
      result={ok:true,requested:true};
    } else { await route.continue(); return; }
    await route.fulfill({json:{jsonrpc:"2.0",id:body.id,result}});
  });
  await page.evaluate(async (selected) => {
    // @ts-expect-error Vite source module is available to the browser.
    const {useSessionStore} = await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().refresh();
    useSessionStore.getState().mergeSessions([selected]);
    useSessionStore.getState().setCurrent(selected.id);
  },selectedSession);
  await page.getByTestId("right-panel-tab-review").click();
  await expect(page.getByTestId("code-review-binding")).toContainText("Receipt states");
  await page.getByTestId("code-review-run-all-btn").click();
  await expect.poll(() => keys.size).toBe(3);
  await page.getByTestId("code-review-tab-security").click();
  await expect(page.getByTestId("code-review-cancel")).toBeVisible();
  await page.getByTestId("code-review-cancel").click();
  await expect(page.getByTestId("code-review-run-status")).toContainText("cancelled");
  expect(cancelled).toEqual(["fixture-security"]);
  await expect(page.getByTestId("code-review-run-status")).toContainText("结果不完整");
  await page.getByTestId("code-review-tab-performance").click();
  await expect(page.getByTestId("code-review-run-status")).toContainText("failed");
  await expect(page.getByTestId("code-review-run-status")).toContainText("Analyzer storage refused");
  await page.getByTestId("code-review-tab-style").click();
  await expect(page.getByTestId("code-review-run-status")).toContainText("budget_paused");
  await expect(page.getByTestId("code-review-run-status")).toContainText("预算暂停");
  await expect(page.getByTestId("code-review-raw")).toContainText("Retained style fixture text");
});
