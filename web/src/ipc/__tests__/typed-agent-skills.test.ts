import { describe, it, expect } from "vitest";
import { bindTypedIPC } from "../typed";
import { mockRequest } from "../mock";
import { IPCClient } from "../client";
import { mockSkills } from "../mockData";

describe("Agent skill declarations", () => {
  it("keeps ordered dependency declarations on create/update wire", async () => {
    const calls:unknown[]=[];
    const api=bindTypedIPC({request:(method:string,params:unknown) => {calls.push({method,params}); return Promise.resolve({});}} as unknown as IPCClient);
    await api.createAgent({name:"a",system_prompt:"Agent",skills:["pack:one","pack:two"]});
    await api.updateAgent({name:"a",skills:[]});
    expect(calls).toEqual([{method:"agent.create",params:{name:"a",system_prompt:"Agent",skills:["pack:one","pack:two"]}},
      {method:"agent.update",params:{name:"a",skills:[]}}]);
  });
  it("mock preserves canonical bindings and replays before current dependency validation", async () => {
    const client=new IPCClient();
    const source=await mockRequest("session.create",{},client) as {session_id:string};
    const name=`bindings-${crypto.randomUUID()}`;
    await mockRequest("agent.create",{name,system_prompt:"Agent",skills:["commit-helper"]},client);
    const args={name,request:"inspect",session_id:source.session_id,idempotency_key:crypto.randomUUID()};
    const first=await mockRequest("agent.invoke",args,client) as {run_id:string};
    const saved=await mockRequest("run.get",{run_id:first.run_id},client) as {run:{metadata:{agent_execution:{skill_bindings:{skills:{id:string}[]}}}}};
    expect(saved.run.metadata.agent_execution.skill_bindings.skills.map((b) => b.id)).toEqual(["commit-helper"]);
    await mockRequest("agent.update",{name,skills:["missing"]},client);
    expect(await mockRequest("agent.invoke",args,client)).toEqual(first);
    await expect(mockRequest("agent.invoke",{...args,idempotency_key:crypto.randomUUID()},client)).rejects.toThrow("dependency");
  });
  it("mock reports disabled, duplicate and allowlist dependency conflicts", async () => {
    const client=new IPCClient();
    const source=await mockRequest("session.create",{},client) as {session_id:string};
    const name=`conflicts-${crypto.randomUUID()}`;
    await expect(mockRequest("agent.create",{name,system_prompt:"Agent",skills:[""]},client)).rejects.toThrow("non-blank");
    await mockRequest("agent.create",{name,system_prompt:"Agent",skills:["commit-helper"],tool_allowlist:[]},client);
    const args={name,request:"inspect",session_id:source.session_id,idempotency_key:crypto.randomUUID()};
    await expect(mockRequest("agent.invoke",args,client)).rejects.toThrow("required skill tools");
    const dependency=mockSkills.find((s) => s.id==="commit-helper")!;
    dependency.enabled=false;
    try { await expect(mockRequest("agent.invoke",args,client)).rejects.toThrow("disabled"); }
    finally { dependency.enabled=true; }
    await mockRequest("agent.update",{name,skills:["commit-helper",dependency.name]},client);
    await expect(mockRequest("agent.invoke",args,client)).rejects.toThrow("Duplicate canonical");
  });
});
