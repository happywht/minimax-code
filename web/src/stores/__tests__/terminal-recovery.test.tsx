import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import type { TerminalReadResult, TerminalSession } from "../../types/ipc";

const api = vi.hoisted(() => ({ listTerminals: vi.fn(), readTerminal: vi.fn(), startTerminal: vi.fn() }));
vi.mock("../../ipc", () => ({ typedIPC: api }));
import { useTerminalStore } from "../terminalStore";
import { TerminalPanel } from "../../components/right-panel/TerminalPanel";

const session: TerminalSession = { id: "t1", command: "test", cwd: "/tmp", status: "completed",
  started_at: 1, updated_at: 3, completed_at: 3, exit_code: 0, error: null, next_seq: 3 };
const chunk = (seq: number) => ({ seq, stream: "stdout" as const, text: `${seq}`, received_at: seq });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

beforeEach(() => {
  useTerminalStore.getState().reset();
  vi.resetAllMocks();
});

describe("terminal output recovery", () => {
  it("loads output for a completed session discovered after reconnect", async () => {
    api.listTerminals.mockResolvedValue({ sessions: [session] });
    api.readTerminal.mockResolvedValue({ session, chunks: [chunk(1)] });
    const view = render(<TerminalPanel />);
    await waitFor(() => expect(view.getByTestId("terminal-panel-output").textContent).toContain("1"));
    expect(api.readTerminal).toHaveBeenCalledWith({ session_id: "t1", after_seq: 0 });
    view.unmount();
  });

  it("merges overlapping reads and does not regress the cursor or session state", async () => {
    const old = deferred<TerminalReadResult>();
    const recent = deferred<TerminalReadResult>();
    api.readTerminal.mockReturnValueOnce(old.promise).mockReturnValueOnce(recent.promise);
    const first = useTerminalStore.getState().read("t1");
    const second = useTerminalStore.getState().read("t1");
    recent.resolve({ session, chunks: [chunk(1), chunk(2)] });
    await second;
    old.resolve({ session: { ...session, status: "running", updated_at: 2, completed_at: null, exit_code: null }, chunks: [chunk(1)] });
    await first;
    expect(useTerminalStore.getState().chunks.t1.map((item) => item.seq)).toEqual([1, 2]);
    expect(useTerminalStore.getState().lastSeq.t1).toBe(2);
    expect(useTerminalStore.getState().sessions.t1.status).toBe("completed");
  });

  it("preserves newer reads and sessions added during a list, then removes absent sessions on a fresh list", async () => {
    const running = { ...session, status: "running" as const, updated_at: 2, completed_at: null, exit_code: null };
    const other = { ...session, id: "t2" };
    useTerminalStore.getState().adopt(running);
    const pending = deferred<{ sessions: TerminalSession[] }>();
    api.listTerminals.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ sessions: [] });
    api.readTerminal.mockResolvedValueOnce({ session, chunks: [chunk(1)] })
      .mockResolvedValueOnce({ session: other, chunks: [] });
    api.startTerminal.mockResolvedValue({ session: other });
    const listing = useTerminalStore.getState().list();
    await useTerminalStore.getState().read("t1");
    await useTerminalStore.getState().start({ command: "test" });
    pending.resolve({ sessions: [running] });
    await listing;
    expect(useTerminalStore.getState().sessions.t1.status).toBe("completed");
    expect(useTerminalStore.getState().sessions.t2).toEqual(other);
    expect(useTerminalStore.getState().activeId).toBe("t2");
    await useTerminalStore.getState().list();
    expect(useTerminalStore.getState().sessions).toEqual({});
    expect(useTerminalStore.getState().activeId).toBeNull();
  });
});
