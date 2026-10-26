import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { resolve, join } from "node:path";
import { AGENT_BASE, AGENT_PORT } from "./runtime-config";

test("public agent uses selected source, one actual durable copy and reloadable timeline", async ({page, request}) => {
  const rpc = async (method:string, params:Record<string,unknown>) => {
    const response = await request.post(`${AGENT_BASE}/rpc`, {data:{jsonrpc:"2.0",id:method,method,params}});
    const body = await response.json(); expect(body.error).toBeUndefined(); return body.result;
  };
  const sources = [];
  for (const suffix of ["a", "b"]) {
    const path = resolve("e2e/.runtime", String(AGENT_PORT), "workspace", `agent-${suffix}`);
    mkdirSync(path, {recursive:true}); writeFileSync(join(path, "source.txt"), suffix);
    const git = (...args:string[]) => execFileSync("git", args, {cwd:path,stdio:"pipe"});
    git("init"); git("add", ".");
    git("-c", "user.name=Offline test", "-c", "user.email=offline@example.invalid", "commit", "-m", "fixture");
    sources.push((await rpc("session.create", {title:`Agent source ${suffix}`,engine:"native",workspace_path:path})).session);
  }
  const {agent} = await rpc("agent.create", {name:"durable-browser-worker",system_prompt:"Inspect the selected source",tool_allowlist:["read_file"]});
  await page.goto("/");
  const input = page.getByPlaceholder(/问 MiniMax 任何问题/); await expect(input).toBeVisible();
  // Select actual loaded rows. A programmatic refresh immediately after mount
  // can be superseded by App's bootstrap refresh; setCurrent then invalidates
  // that newer response and fabricates a selection absent from the session map.
  await page.getByTestId(`sidebar-session-${sources[1].id}`).click();
  await page.getByTestId(`sidebar-session-${sources[0].id}`).click();
  await input.fill("@durable-browser");
  await page.getByTestId(`message-input-mention-picker-item-agent-${agent.id}`).click();
  await expect.poll(async () => page.evaluate(() => {
    const rows = JSON.parse(localStorage.getItem("minimax-code:agent-roots:v1") ?? "{}");
    return Object.values(rows).map((row:any) => row.run_status);
  })).toContain("completed");
  const saved:any = await page.evaluate(() => Object.values(JSON.parse(localStorage.getItem("minimax-code:agent-roots:v1")!))[0]);
  const {run,result} = await rpc("run.get", {run_id:saved.run_id});
  expect(run.session_id).not.toBe(sources[0].id);
  expect(run.metadata.agent_execution.source_workspace).toBe(sources[0].workspace_path);
  expect(result.agent_run_id).toBe(run.id); expect(result.run_status).toBe("completed");
  expect(readFileSync(join(result.workspace.path, "source.txt"), "utf8")).toBe("a");
  expect((await rpc("run.list", {session_id:sources[1].id})).runs).toHaveLength(0);
  const before = (await rpc("run.list", {})).runs.length;
  await rpc("agent.delete", {name:agent.name});
  const replay = await rpc("agent.spawn_subagent", {name:agent.name,agent_id:agent.id,request:saved.request.prompt,
    session_id:saved.request.session_id,parent_session_id:saved.request.parent_session_id,
    display_name:saved.request.display_name,idempotency_key:saved.request.idempotency_key,admission_only:true});
  expect(replay.run.id).toBe(run.id); expect(replay.created).toBe(false);
  expect((await rpc("run.list", {})).runs).toHaveLength(before);
  await page.reload(); await expect(input).toBeVisible();
  await page.getByTestId(`sidebar-session-${sources[0].id}`).click();
  await page.getByTestId("right-panel-tab-subagents").click();
  await expect(page.getByTestId(`right-panel-sub-panel-row-${run.id}-summary`)).toContainText("completed");
  const restored:any = await page.evaluate(() => Object.values(JSON.parse(localStorage.getItem("minimax-code:agent-roots:v1")!))[0]);
  expect(restored.request.idempotency_key).toBe(saved.request.idempotency_key);
  await page.getByTestId(`right-panel-sub-panel-row-${run.id}-header`).click();
  await page.getByTestId(`right-panel-sub-panel-row-${run.id}-detail`).getByText("查看任务",{exact:true}).click();
  await expect(page.getByTestId("right-panel-timeline-panel")).toContainText("completed");
});

test("agent controls retain uncertain admission, cancellation and partial/budget receipts", async ({page, request}) => {
  // Controlled HTTP receipts prove UI behaviour only. Runtime faults/process ownership
  // are separately exercised by actual SDK/SQLite/OS backend tests.
  const create = await request.post(`${AGENT_BASE}/rpc`, {data:{jsonrpc:"2.0",id:1,method:"session.create",params:{title:"Agent receipt states",engine:"native"}}});
  const source = (await create.json()).result.session;
  const rows:Record<string,any> = {}; const keys:string[]=[]; let loseAck=true;
  await page.route("**/rpc", async (route) => {
    const body=route.request().postDataJSON(), p=body.params??{}; let result:any;
    if (body.method==="agent.spawn_subagent" && p.session_id===source.id) {
      keys.push(p.idempotency_key);
      rows[p.idempotency_key] ??= {id:`ui-${p.idempotency_key}`,session_id:`exec-${p.idempotency_key}`,status:"running",mode:"execute",title:"Controlled agent",created_at:new Date().toISOString(),metadata:{}};
      if (loseAck) { loseAck=false; await route.abort(); return; }
      result={run:rows[p.idempotency_key],created:false};
    } else if (body.method==="run.get" && String(p.run_id).startsWith("ui-")) {
      const row=rows[String(p.run_id).slice(3)];
      result={run:row,result:{run_id:row.id,session_id:row.session_id,status:row.status,run_status:row.status,text:"Retained partial text",partial:row.status!=="running",verification:{requires_review:row.status!=="running"}}};
    } else if (body.method==="agent.cancel_subagent" && String(p.run_id).startsWith("ui-")) result={cancelled:true,cancel_requested:true,run_status:"running"};
    else { await route.continue(); return; }
    await route.fulfill({json:{jsonrpc:"2.0",id:body.id,result}});
  });
  await page.goto("/"); await expect(page.getByPlaceholder(/问 MiniMax 任何问题/)).toBeVisible();
  await page.evaluate(async (session) => {
    // @ts-expect-error Vite source module is available in this browser fixture.
    const {useSessionStore}=await import("/src/stores/sessionStore.ts");
    await useSessionStore.getState().refresh(); useSessionStore.getState().mergeSessions([session]); useSessionStore.getState().setCurrent(session.id);
    // @ts-expect-error Vite source module is available in this browser fixture.
    const {useSubAgentStore}=await import("/src/stores/subAgent.ts");
    await useSubAgentStore.getState().init();
    await useSubAgentStore.getState().spawn({agent_id:"controlled",agent_name:"controlled",prompt:"inspect",session_id:session.id,idempotency_key:"uncertain"});
  },source);
  await page.getByTestId("right-panel-tab-subagents").click();
  await page.getByTestId("right-panel-sub-panel-row-pending_uncertain-header").click();
  await page.getByText("使用原请求重试",{exact:true}).click();
  await expect(page.getByTestId("right-panel-sub-panel-row-ui-uncertain-summary")).toContainText("running");
  expect(new Set(keys).size).toBe(1);
  await page.getByTestId("right-panel-sub-panel-row-ui-uncertain-cancel").click();
  await expect(page.getByTestId("right-panel-sub-panel-row-ui-uncertain-summary")).toContainText("running");
  for (const state of ["cancelled","budget_paused","failed"]) {
    rows.uncertain.status=state;
    await page.evaluate(async () => {
      // @ts-expect-error Vite source module is available in this browser fixture.
      const {useSubAgentStore}=await import("/src/stores/subAgent.ts");
      await useSubAgentStore.getState().refresh();
    });
    await expect(page.getByTestId("right-panel-sub-panel-row-ui-uncertain-summary")).toContainText(`${state} · 部分结果`);
  }
});
