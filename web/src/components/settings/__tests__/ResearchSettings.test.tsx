import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ getResearchConfig: vi.fn(), setResearchConfig: vi.fn() }));
vi.mock("../../../ipc/client", () => ({ typedIPC: api }));
import { ResearchSettings } from "../ResearchSettings";

describe("research settings", () => {
  beforeEach(() => {
    api.getResearchConfig.mockReset();
    api.setResearchConfig.mockReset();
  });

  it("loads, saves and clears a configured endpoint", async () => {
    api.getResearchConfig.mockResolvedValue({ saved_url: null, effective_url: null, source: "unconfigured" });
    api.setResearchConfig.mockResolvedValueOnce({ saved_url: "https://search.example/search", effective_url: "https://search.example/search", source: "settings" })
      .mockResolvedValueOnce({ saved_url: null, effective_url: null, source: "environment" });
    render(<ResearchSettings />);
    await screen.findByText("未配置搜索服务");
    fireEvent.change(screen.getByLabelText("搜索服务地址"), { target: { value: "https://search.example/search" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText(/当前使用已保存地址/);
    expect(api.setResearchConfig).toHaveBeenCalledWith("https://search.example/search");
    fireEvent.click(screen.getByRole("button", { name: "清除已保存地址" }));
    await screen.findByText("当前使用环境变量回退");
    expect(api.setResearchConfig).toHaveBeenLastCalledWith(null);
  });

  it("shows validation failure without claiming that it was saved", async () => {
    api.getResearchConfig.mockResolvedValue({ saved_url: null, effective_url: null, source: "unconfigured" });
    api.setResearchConfig.mockRejectedValue(new Error("invalid"));
    render(<ResearchSettings />);
    await screen.findByText("未配置搜索服务");
    fireEvent.change(screen.getByLabelText("搜索服务地址"), { target: { value: "https://bad.example/search?token=x" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("保存失败"));
    expect(screen.getByText("未配置搜索服务")).toBeInTheDocument();
  });
});
