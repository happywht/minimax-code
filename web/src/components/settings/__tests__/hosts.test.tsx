import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HostsTab } from "../HostsTab";

const api = vi.hoisted(() => ({ listHosts: vi.fn(), getHostConnection: vi.fn(), connectHost: vi.fn(), disconnectHost: vi.fn(), createHost: vi.fn(), updateHost: vi.fn() }));
vi.mock("../../../ipc", () => ({ typedIPC: api }));
const host = { id: "h", name: "build", hostname: "linux", username: "user", port: 22, enabled: true };
const disconnected = { host_id: "h", state: "disconnected", local_url: null, remote_port: null, error: null };

describe("remote host settings", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    api.listHosts.mockResolvedValue({ hosts: [host] });
    api.getHostConnection.mockResolvedValue({ connection: disconnected });
  });
  it("connects, offers a separate isolated tab, and disconnects", async () => {
    api.connectHost.mockResolvedValue({ connection: { ...disconnected, state: "connected", local_url: "http://127.0.0.1:4567", remote_port: 8765 } });
    api.disconnectHost.mockResolvedValue({ connection: disconnected });
    render(<HostsTab />);
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    const link = await screen.findByRole("link", { name: "在新标签页打开远端" });
    expect(link).toHaveAttribute("href", "http://127.0.0.1:4567/");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(api.connectHost).toHaveBeenCalledWith({ host_id: "h", remote_port: 8765 });
    fireEvent.click(screen.getByRole("button", { name: "断开" }));
    await waitFor(() => expect(screen.queryByRole("link")).not.toBeInTheDocument());
    expect(api.disconnectHost).toHaveBeenCalledWith("h");
  });
  it("shows errors and never exposes a non-loopback returned URL", async () => {
    api.getHostConnection.mockResolvedValue({ connection: { ...disconnected, state: "connected", local_url: "https://example.com" } });
    render(<HostsTab />);
    expect(await screen.findByRole("alert")).toHaveTextContent("隧道地址无效");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
  it("enables a disabled existing host and refreshes its state", async () => {
    api.listHosts.mockResolvedValueOnce({ hosts: [{ ...host, enabled: false }] }).mockResolvedValueOnce({ hosts: [host] });
    api.updateHost.mockResolvedValue({ ok: true, host });
    render(<HostsTab />);
    fireEvent.click(await screen.findByRole("button", { name: "启用" }));
    await waitFor(() => expect(api.updateHost).toHaveBeenCalledWith("h", { enabled: true }));
    expect(await screen.findByRole("button", { name: "禁用" })).toBeInTheDocument();
  });
  it("shows failed connection details", async () => {
    api.connectHost.mockResolvedValue({ connection: { ...disconnected, state: "failed", error: "SSH failed" } });
    render(<HostsTab />);
    fireEvent.click(await screen.findByRole("button", { name: "连接" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("SSH failed");
  });
  it("updates the displayed status when a tunnel exits", async () => {
    vi.useFakeTimers();
    try {
      api.getHostConnection.mockResolvedValueOnce({ connection: { ...disconnected, state: "connected", local_url: "http://127.0.0.1:4567" } });
      api.getHostConnection.mockResolvedValue({ connection: { ...disconnected, state: "failed", error: "SSH exited" } });
      await act(async () => { render(<HostsTab />); });
      expect(screen.getByRole("link", { name: "在新标签页打开远端" })).toBeInTheDocument();
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(screen.queryByRole("link")).toBeNull();
      expect(screen.getByRole("alert")).toHaveTextContent("SSH exited");
    } finally { vi.useRealTimers(); }
  });
});
