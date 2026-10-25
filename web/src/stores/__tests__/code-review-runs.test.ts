import { beforeEach, describe, expect, it, vi } from "vitest";
import type { AgentRun, SkillInvokeArgs } from "../../types/ipc";
const api = vi.hoisted(() => ({ gitDiff:vi.fn(), admitSkill:vi.fn(), listRuns:vi.fn(), getRun:vi.fn(),
  cancelRun:vi.fn(), getRunSteps:vi.fn() }));
vi.mock("../../ipc", () => ({typedIPC:api,ipc:{on:vi.fn(() => () => undefined),onSideCar:vi.fn(() => () => undefined)}}));
import { useCodeReviewStore as store } from "../codeReviewStore";
import { useSessionStore } from "../sessionStore";
import { useRunTimelineStore } from "../runStore";

function deferred<T>() { let resolve!:(value:T) => void; const promise = new Promise<T>((yes) => {resolve=yes;}); return {resolve,promise}; }
const run = (id="r1", status:AgentRun["status"]="completed", label="overview"):AgentRun => ({id,session_id:"s1",mode:"execute",status,
  title:"Review",created_at:"2026-10-26T00:00:00Z",request_key:`key-${id}`,
  metadata:{skill_execution:{skill_id:"code-review:code-review",label}}});
const snapshot = (row=run(), partial=false) => ({run:row,result:{run_id:row.id,session_id:row.session_id,
  skill_id:"code-review:code-review",status:row.status,run_status:row.status,text:`text-${row.id}`,output:`text-${row.id}`,
  iterations:1,tool_calls:0,cancelled:false,truncated:false,partial,comments:[],stats:{files:1,additions:2,deletions:1}}});
beforeEach(async () => {
  vi.resetAllMocks(); localStorage.clear();
  useSessionStore.setState({currentSessionId:"s1",sessions:[{id:"s1",title:"A",engine:"native",archived:false,created_at:0,updated_at:0,model_id:null,workspace_path:"/a"},
    {id:"s2",title:"B",engine:"native",archived:false,created_at:0,updated_at:0,model_id:null,workspace_path:"/b"}]});
  api.listRuns.mockResolvedValue({runs:[]});
  api.gitDiff.mockResolvedValue({diff:"+++ a.py",scope:"working",session_id:"s1",workspace:"/a"});
  api.admitSkill.mockResolvedValue({run:run(),created:true});
  api.getRun.mockResolvedValue(snapshot());
  await store.getState().bindSession("s1");
});

describe("durable review run store", () => {
  it("does not downgrade a confirmed same-key retry after an older retry fails", async () => {
    api.admitSkill.mockRejectedValueOnce(new Error("lost ACK"));
    await store.getState().runReview();
    let reject!:(reason:Error) => void;
    const old = new Promise<never>((_yes, no) => {reject=no;});
    api.admitSkill.mockReturnValueOnce(old);
    const stale = store.getState().retryPending("overview");
    await store.getState().retryPending("overview");
    const confirmed = structuredClone(store.getState().invocations.overview);
    reject(new Error("late failure")); await stale;
    expect(store.getState().invocations.overview).toEqual(confirmed);
    expect(store.getState().dimensions.overview.status).toBe("completed");
    expect(store.getState().error).toBeNull();
  });

  it("does not open a stale run after its session changes", async () => {
    await store.getState().runReview();
    const old = deferred<{run:AgentRun;steps:never[];cursor:number}>();
    api.getRunSteps.mockReturnValueOnce(old.promise);
    const opening = store.getState().openRun("overview");
    useSessionStore.setState({currentSessionId:"s2"});
    await store.getState().bindSession("s2");
    old.resolve({run:run(),steps:[],cursor:1});
    expect(await opening).toBe(false);
  });

  it("does not overwrite a newer same-session run/key with a delayed old receipt", async () => {
    await store.getState().runReview();
    const old = deferred<ReturnType<typeof snapshot>>();
    const entered = deferred<void>();
    api.getRun.mockImplementationOnce(() => { entered.resolve(); return old.promise; });
    const stale = store.getState().refresh();
    await entered.promise;
    api.admitSkill.mockResolvedValue({run:run("r2"),created:true});
    api.getRun.mockResolvedValue(snapshot(run("r2")));
    await store.getState().runReview();
    const latest = structuredClone(store.getState().invocations.overview);
    old.resolve(snapshot()); await stale;
    expect(store.getState().invocations.overview).toEqual(latest);
    expect(store.getState().rawText).toBe("text-r2");
    expect(JSON.parse(localStorage.getItem("minimax-code:reviews:s1")!).overview).toEqual(latest);
  });

  it("preserves an active run and refuses replacement when refresh fails", async () => {
    api.admitSkill.mockResolvedValue({run:run("r1","running"),created:true});
    api.getRun.mockResolvedValue(snapshot(run("r1","running")));
    await store.getState().runReview();
    const latest = structuredClone(store.getState().invocations.overview);
    api.getRun.mockRejectedValue(new Error("temporary disconnect"));
    await store.getState().refresh();
    expect(store.getState().loading).toBe(true);
    await store.getState().runReview();
    expect(api.admitSkill).toHaveBeenCalledTimes(1);
    expect(store.getState().invocations.overview).toEqual(latest);
    expect(store.getState().loading).toBe(true);
  });
  it("binds the same explicit session/workspace to git and admission, then reads stored result", async () => {
    await store.getState().runReview();
    expect(api.gitDiff).toHaveBeenCalledWith({session_id:"s1",scope:"working"});
    expect(api.admitSkill).toHaveBeenCalledWith("code-review:code-review", expect.objectContaining({session_id:"s1",
      request:"Review this unified diff",diff:"+++ a.py",expected_workspace:"/a",idempotency_key:expect.any(String),label:"overview"}));
    expect(store.getState().rawText).toBe("text-r1");
    expect(store.getState().dimensions.overview.status).toBe("completed");
    expect(store.getState().invocations.overview?.runId).toBe("r1");
  });

  it("persists uncertain admission key before send and retries identical intent after reload", async () => {
    const sent:SkillInvokeArgs[]=[];
    api.admitSkill.mockImplementationOnce(async (_skill:string,args:SkillInvokeArgs) => {
      sent.push(args);
      expect(localStorage.getItem("minimax-code:reviews:s1")).toContain(args.idempotency_key);
      throw new Error("lost ACK");
    });
    await store.getState().runReview();
    expect(store.getState().invocations.overview?.status).toBe("admission_unknown");
    await store.getState().bindSession("s1");
    await store.getState().retryPending("overview");
    expect(api.admitSkill.mock.calls[1][1]).toEqual(sent[0]);
    expect(api.gitDiff).toHaveBeenCalledTimes(1);
    expect(store.getState().rawText).toBe("text-r1");
    expect(store.getState().error).toBeNull();
  });

  it("recovers actual review runs without browser cache and opens the retained run", async () => {
    api.listRuns.mockResolvedValue({runs:[run()]});
    await store.getState().bindSession("s1");
    expect(store.getState().rawText).toBe("text-r1");
    api.getRunSteps.mockResolvedValue({run:run(),steps:[],cursor:1});
    await store.getState().openRun("overview");
    expect(useRunTimelineStore.getState().order[0]).toBe("r1");
  });

  it("rejects Codex/unselected sessions before git or admission", async () => {
    useSessionStore.setState({currentSessionId:null});
    await store.getState().runReview();
    expect(api.gitDiff).not.toHaveBeenCalled();
    expect(store.getState().error).toContain("Native");
  });

  it("ignores delayed session-A diff after switching to B", async () => {
    const delayed=deferred<{diff:string;workspace:string;session_id:string}>();
    api.gitDiff.mockReturnValueOnce(delayed.promise);
    const pending=store.getState().runReview();
    useSessionStore.setState({currentSessionId:"s2"});
    await store.getState().bindSession("s2");
    delayed.resolve({diff:"A",workspace:"/a",session_id:"s1"}); await pending;
    expect(api.admitSkill).not.toHaveBeenCalled();
    expect(store.getState().sessionId).toBe("s2");
    expect(store.getState().rawText).toBe("");
  });

  it("ignores delayed A receipt after B binds and keeps cancellation target captured", async () => {
    await store.getState().runReview();
    const delayed=deferred<ReturnType<typeof snapshot>>();
    api.getRun.mockReturnValueOnce(delayed.promise);
    const pending=store.getState().refresh();
    await Promise.resolve();
    useSessionStore.setState({currentSessionId:"s2"});
    await store.getState().bindSession("s2");
    delayed.resolve(snapshot()); await pending;
    expect(store.getState().rawText).toBe("");
    await store.getState().cancel("overview");
    expect(api.cancelRun).not.toHaveBeenCalled();
  });

  it("retains three identifiable independent runs and truthful failed/partial/budget dimensions", async () => {
    api.admitSkill.mockImplementation(async (_skill:string,args:SkillInvokeArgs) => ({run:run(args.label,args.label==="performance"?"failed":
      args.label==="style"?"budget_paused":"completed",args.label),created:true}));
    api.getRun.mockImplementation(async (id:string) => snapshot(run(id,id==="performance"?"failed":id==="style"?"budget_paused":"completed",id),id!=="security"));
    await store.getState().runAllChecks();
    expect(api.admitSkill).toHaveBeenCalledTimes(3);
    const args=api.admitSkill.mock.calls.map((call) => call[1] as SkillInvokeArgs);
    expect(new Set(args.map((item) => item.idempotency_key)).size).toBe(3);
    expect(args.map((item) => item.label)).toEqual(["security","performance","style"]);
    expect(args.every((item) => item.session_id==="s1" && item.expected_workspace==="/a")).toBe(true);
    expect(store.getState().dimensions.performance).toMatchObject({status:"failed",partial:true});
    expect(store.getState().dimensions.style.status).toBe("budget_paused");
    expect(store.getState().loading).toBe(false);
  });

  it("cancels the admitted run id and refreshes its durable status", async () => {
    api.getRun.mockResolvedValue(snapshot(run("r1","running")));
    await store.getState().runReview();
    api.cancelRun.mockResolvedValue({ok:true,requested:true});
    api.getRun.mockResolvedValue(snapshot(run("r1","cancelled"),true));
    await store.getState().cancel("overview");
    expect(api.cancelRun).toHaveBeenCalledWith("r1");
    expect(store.getState().dimensions.overview).toMatchObject({status:"cancelled",partial:true});
  });
});
