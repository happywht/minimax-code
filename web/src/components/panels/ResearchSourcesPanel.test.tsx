import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores";
import type { ResearchSource, ResearchSourceScope } from "../../types/ipc";
import { ResearchSourcesPanel } from "./ResearchSourcesPanel";

const scope: ResearchSourceScope = { session_id: "s1", project_id: "p1", service_id: "svc1" };
const source: ResearchSource = { page_source_id: "page1", run_id: "run1", title: "Actual title",
  url: "https://example.org/page", accessed_at: "2026-10-29T00:00:00Z", excerpt: "extracted evidence",
  title_truncated: false, excerpt_truncated: true };
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };

beforeEach(() => {
  useSessionStore.setState({ currentSessionId: "s1", currentProjectId: "p1" });
  vi.spyOn(ipc, "onSideCar").mockReturnValue(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows source details, real URL and the unverified and truncation notices", async () => {
  vi.spyOn(typedIPC, "listResearchSources").mockResolvedValue({ ...scope, sources: [source] });
  vi.spyOn(typedIPC, "getResearchSource").mockResolvedValue({ ...scope, source });
  render(<ResearchSourcesPanel onText={() => {}} onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /Actual title/ }));
  expect(await screen.findByText("extracted evidence")).toBeVisible();
  expect(screen.getByRole("link", { name: "https://example.org/page" })).toHaveAttribute("href", "https://example.org/page");
  expect(screen.getByText(/未自动核验/)).toBeVisible();
  expect(screen.getByText(/摘录已截断/)).toBeVisible();
  expect(screen.getByText(/所属任务：run1/)).toBeVisible();
});

it("drops a list response after switching project and session", async () => {
  const pending = deferred<ResearchSourceScope & { sources: ResearchSource[] }>();
  vi.spyOn(typedIPC, "listResearchSources").mockImplementation((request) => request.project_id === "p1"
    ? pending.promise : Promise.resolve({ session_id: "s2", project_id: "p2", service_id: "svc2", sources: [] }));
  render(<ResearchSourcesPanel onText={() => {}} onClose={() => {}} />);
  act(() => useSessionStore.setState({ currentSessionId: "s2", currentProjectId: "p2" }));
  await act(async () => { pending.resolve({ ...scope, sources: [source] }); await pending.promise; });
  expect(screen.queryByText(/Actual title/)).toBeNull();
  expect(await screen.findByText("当前范围没有匹配的来源。")).toBeVisible();
});

it("rejects a detail response from another service", async () => {
  vi.spyOn(typedIPC, "listResearchSources").mockResolvedValue({ ...scope, sources: [source] });
  vi.spyOn(typedIPC, "getResearchSource").mockResolvedValue({ ...scope, service_id: "other", source });
  render(<ResearchSourcesPanel onText={() => {}} onClose={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /Actual title/ }));
  expect(await screen.findByRole("alert")).toHaveTextContent("关联不匹配");
  expect(screen.queryByText("extracted evidence")).toBeNull();
});

it("discards a response from before a service restart", async () => {
  let restart = () => {};
  vi.mocked(ipc.onSideCar).mockImplementation((callback) => {
    restart = () => callback({ status: "started" } as never);
    return () => {};
  });
  const pending = deferred<ResearchSourceScope & { sources: ResearchSource[] }>();
  vi.spyOn(typedIPC, "listResearchSources").mockReturnValueOnce(pending.promise)
    .mockResolvedValue({ ...scope, service_id: "new", sources: [] });
  render(<ResearchSourcesPanel onText={() => {}} onClose={() => {}} />);
  act(() => restart());
  await act(async () => { pending.resolve({ ...scope, sources: [source] }); await pending.promise; });
  expect(screen.queryByText(/Actual title/)).toBeNull();
  expect(await screen.findByText("当前范围没有匹配的来源。")).toBeVisible();
});

it("keeps the selected source identifiable and links to its details", async () => {
  vi.spyOn(typedIPC, "listResearchSources").mockResolvedValue({ ...scope, sources: [source] });
  vi.spyOn(typedIPC, "getResearchSource").mockResolvedValue({ ...scope, source });
  render(<ResearchSourcesPanel onText={() => {}} onClose={() => {}} />);
  const item = await screen.findByRole("button", { name: /Actual title/ });
  fireEvent.click(item);
  const link = await screen.findByRole("link", { name: "查看已选来源的摘录与操作" });
  expect(item).toHaveAttribute("aria-pressed", "true");
  expect(link).toHaveAttribute("href", "#selected-research-source");
  expect(document.getElementById("selected-research-source")).toContainElement(screen.getByLabelText(/保存的正文摘录/));
});
