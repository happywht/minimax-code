import { describe, it, expect } from "vitest";
import { bindTypedIPC } from "../typed";
import { mockRequest } from "../mock";
import { IPCClient } from "../client";

describe("durable skill wire contract", () => {
  it("sends explicit intent and admission-only mode without object coercion", async () => {
    const calls: unknown[] = [];
    const api = bindTypedIPC({ request: (method: string, params: unknown) => {
      calls.push({method,params}); return Promise.resolve({});
    } } as unknown as IPCClient);
    const args = { request:"review", diff:"+++ fake", session_id:"s", idempotency_key:"once" };
    await api.invokeSkill("code-review:code-review", args);
    await api.admitSkill("code-review:code-review", args);
    expect(calls).toEqual([
      {method:"skill.invoke",params:{skill_id:"code-review:code-review",...args}},
      {method:"skill.invoke",params:{skill_id:"code-review:code-review",...args,admission_only:true}},
    ]);
  });

  it("mock admission retains a durable receipt, replays and rejects changed intent", async () => {
    const client = new IPCClient();
    const session = await mockRequest("session.create", {title:"review"}, client) as {session_id:string};
    const args = {skill_id:"code-review:code-review",request:"review",diff:"+++ x",session_id:session.session_id,
      idempotency_key:crypto.randomUUID(),label:"overview"};
    const admission = await mockRequest("skill.invoke",{...args,admission_only:true},client) as {run:{id:string};created:boolean};
    expect(admission.created).toBe(true);
    const replay = await mockRequest("skill.invoke",args,client) as {run_id:string;output:string;text:string;status:string};
    expect(replay.run_id).toBe(admission.run.id);
    expect(replay.output).toBe(replay.text);
    expect(replay.status).toBe("completed");
    const stored = await mockRequest("run.get",{run_id:replay.run_id},client) as {result:unknown};
    expect(stored.result).toEqual(replay);
    await expect(mockRequest("skill.invoke",{...args,request:"different"},client)).rejects.toThrow("different input");
  });

  it("mock refuses missing explicit Native session/key and non-string input", async () => {
    const client = new IPCClient();
    await expect(mockRequest("skill.invoke",{skill_id:"x",request:{}},client)).rejects.toThrow("stable key");
  });
});
