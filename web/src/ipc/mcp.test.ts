import { describe, expect, it } from "vitest";
import { IPCClient } from "./client";
import { bindTypedIPC } from "./typed";
import type { McpTransport } from "../types/ipc";

describe("MCP HTTP configuration contract", () => {
  it("mock and typed API preserve explicit HTTP transport across create/edit/list", async () => {
    const api = bindTypedIPC(new IPCClient({ mockMode: true }));
    const id = `mcp-${crypto.randomUUID()}`;
    const added = await api.addMcpServer({ id, name: id, transport: "streamable_http",
      url: "http://localhost:3001/mcp", headers: { "X-Explicit": "yes" } });
    expect(added.server.transport).toBe("streamable_http");
    expect(added.server.connected).toBe(false);
    await api.updateMcpServer(id, { url: "http://localhost:3002/mcp" });
    expect((await api.listMcpServers()).servers.find((server) => server.id === id)).toMatchObject({
      transport: "streamable_http", url: "http://localhost:3002/mcp", headers: { "X-Explicit": "yes" },
    });
    await expect(api.updateMcpServer(id, { transport: "unknown" as McpTransport })).rejects.toThrow("transport");
    expect((await api.listMcpServers()).servers.find((server) => server.id === id)?.transport).toBe("streamable_http");
    await api.removeMcpServer(id);
  });

  it("mock rejects unknown transport without creating a server", async () => {
    const api = bindTypedIPC(new IPCClient({ mockMode: true }));
    const id = `invalid-${crypto.randomUUID()}`;
    await expect(api.addMcpServer({ id, name: id, transport: "http" as McpTransport })).rejects.toThrow("transport");
    expect((await api.listMcpServers()).servers.some((server) => server.id === id)).toBe(false);
  });
});
