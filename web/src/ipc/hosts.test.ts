import { describe, expect, it, vi } from "vitest";
import { IPCClient } from "./client";
import { bindTypedIPC } from "./typed";

describe("remote host contract", () => {
  it("maps every host method and preserves the pre-recorded upload ID", async () => {
    const request = vi.fn().mockResolvedValue({});
    const api = bindTypedIPC({ request } as unknown as IPCClient);
    const config = { name: "build", hostname: "linux", username: "user" };
    const transfer = { host_id: "h", local_path: "source", remote_path: "/target", transfer_id: "a".repeat(32) };
    await api.createHost(config);
    await api.listHosts({ enabled: true });
    await api.getHost("h");
    await api.updateHost("h", { port: 2222 });
    await api.probeHost("h");
    await api.connectHost({ host_id: "h", remote_port: 8765 });
    await api.getHostConnection("h");
    await api.disconnectHost("h");
    await api.execHost({ host_id: "h", command: "pwd", timeout_s: 10 });
    await api.pushHostFile(transfer);
    await api.getHostTransferStatus({ host_id: "h", remote_path: "/target", transfer_id: transfer.transfer_id });
    await api.fetchHostFile({ host_id: "h", remote_path: "/target", local_path: "out" });
    await api.deleteHost("h");
    expect(request.mock.calls).toEqual([
      ["host.create", config], ["host.list", { enabled: true }],
      ["host.get", { host_id: "h" }], ["host.update", { host_id: "h", port: 2222 }],
      ["host.probe", { host_id: "h" }],
      ["host.connect", { host_id: "h", remote_port: 8765 }],
      ["host.connection", { host_id: "h" }], ["host.disconnect", { host_id: "h" }],
      ["host.exec", { host_id: "h", command: "pwd", timeout_s: 10 }],
      ["host.push", transfer],
      ["host.transfer_status", { host_id: "h", remote_path: "/target", transfer_id: transfer.transfer_id }],
      ["host.fetch", { host_id: "h", remote_path: "/target", local_path: "out" }],
      ["host.delete", { host_id: "h" }],
    ]);
  });

  it("mock CRUD persists host fields, filters disabled hosts and rejects unknown IDs", async () => {
    const api = bindTypedIPC(new IPCClient({ mockMode: true }));
    const config = { name: `host-${crypto.randomUUID()}`, hostname: "linux", username: "user" };
    const { host } = await api.createHost(config);
    try {
      await expect(api.createHost(config)).rejects.toThrow("already taken");
      expect((await api.getHost(host.id)).host).toMatchObject({ ...config, enabled: true, port: 22 });
      await api.updateHost(host.id, { enabled: false, port: 2222, remote_port: 9999 });
      expect((await api.getHost(host.id)).host.remote_port).toBe(9999);
      await expect(api.updateHost(host.id, { remote_port: 0 })).rejects.toThrow("port");
      expect((await api.listHosts({ enabled: false })).hosts).toContainEqual(expect.objectContaining({ id: host.id, port: 2222 }));
      expect((await api.listHosts({ enabled: true })).hosts.some((h) => h.id === host.id)).toBe(false);
      await expect(api.probeHost(host.id)).rejects.toThrow("disabled");
      await expect(api.getHostTransferStatus({ host_id: host.id, remote_path: "/target", transfer_id: "a".repeat(32) })).rejects.toThrow("disabled");
    } finally {
      await api.deleteHost(host.id);
    }
    await expect(api.getHost(host.id)).rejects.toThrow("unknown host_id");
  });

  it("mock probe, execution and file transfer never invent remote success", async () => {
    const api = bindTypedIPC(new IPCClient({ mockMode: true }));
    const { host } = await api.createHost({ name: `host-${crypto.randomUUID()}`, hostname: "linux", username: "user" });
    try {
      const probe = await api.probeHost(host.id);
      expect(probe.probe.ok).toBe(false);
      expect(probe.host.last_probe_ok).toBe(false);
      expect(await api.execHost({ host_id: host.id, command: "anything" })).toMatchObject({ ok: false, stderr: expect.stringContaining("mock mode") });
      const transfer = { host_id: host.id, local_path: "source", remote_path: "/target", transfer_id: "b".repeat(32) };
      expect(await api.pushHostFile(transfer)).toMatchObject({ ok: false, transfer_id: transfer.transfer_id, publication_state: "unknown", backup_path: null });
      expect(await api.getHostTransferStatus(transfer)).toMatchObject({ ok: false, transfer_id: transfer.transfer_id, publication_state: "unknown" });
      expect(await api.fetchHostFile(transfer)).toMatchObject({ ok: false, local_path: "source" });
    } finally {
      await api.deleteHost(host.id);
    }
  });
});
