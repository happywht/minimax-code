/**
 * Tests for the Zustand stores — verifies state transitions, error
 * surfacing, and store isolation.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import {
  useChat,
  useSessionStore,
  useModelStore,
  usePermissionStore,
  useTaskStore,
} from "../src/stores";

vi.mock("../src/components/layout/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

describe("sessionStore", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useChat.getState().reset();
    useSessionStore.setState({
      sessions: [],
      currentSessionId: null,
      loading: false,
      filter: "all",
      selectedSessionIds: new Set(),
    });
  });

  it("starts with an empty list and 'all' filter", () => {
    const s = useSessionStore.getState();
    expect(s.sessions).toEqual([]);
    expect(s.filter).toBe("all");
  });

  it("setFilter updates the filter", () => {
    useSessionStore.getState().setFilter("archived");
    expect(useSessionStore.getState().filter).toBe("archived");
  });

  it("setCurrent updates the active id", () => {
    useChat.getState().addLocalMessage("previous session");
    useSessionStore.getState().setCurrent("ses_1");
    expect(useSessionStore.getState().currentSessionId).toBe("ses_1");
    expect(useChat.getState().messages).toEqual([]);
    expect(window.localStorage.getItem("minimax-code:current-session")).toBe("ses_1");
  });

  it("manages session selection state", () => {
    const { selectSession, toggleSessionSelection, selectAllVisible, clearSessionSelection } =
      useSessionStore.getState();

    selectSession("a");
    expect(useSessionStore.getState().selectedSessionIds).toEqual(new Set(["a"]));

    toggleSessionSelection("b");
    expect(useSessionStore.getState().selectedSessionIds).toEqual(new Set(["a", "b"]));

    toggleSessionSelection("a");
    expect(useSessionStore.getState().selectedSessionIds).toEqual(new Set(["b"]));

    selectAllVisible(["x", "y"]);
    expect(useSessionStore.getState().selectedSessionIds).toEqual(new Set(["x", "y"]));

    clearSessionSelection();
    expect(useSessionStore.getState().selectedSessionIds).toEqual(new Set());
  });
});

describe("modelStore", () => {
  beforeEach(() => {
    useModelStore.setState({ models: [], current: null, loading: false });
  });

  it("starts empty", () => {
    expect(useModelStore.getState().models).toEqual([]);
  });
});

describe("permissionStore", () => {
  beforeEach(() => {
    usePermissionStore.setState({
      alwaysAllow: false,
      rules: [],
      loading: false,
    });
  });

  it("toggles alwaysAllow", () => {
    expect(usePermissionStore.getState().alwaysAllow).toBe(false);
    usePermissionStore.getState().setAlwaysAllow(true);
    expect(usePermissionStore.getState().alwaysAllow).toBe(true);
  });
});

describe("taskStore", () => {
  beforeEach(() => {
    useTaskStore.setState({ tasks: {}, collapsed: false });
  });

  it("upserts a task and clamps progress to [0,1]", () => {
    useTaskStore.getState().upsert({
      task_id: "t1",
      progress: 1.5,
      status: "running",
      message: "x",
    });
    expect(useTaskStore.getState().tasks.t1.progress).toBe(1);

    useTaskStore.getState().upsert({
      task_id: "t2",
      progress: -1,
      status: "running",
    });
    expect(useTaskStore.getState().tasks.t2.progress).toBe(0);
  });

  it("removes a task", () => {
    useTaskStore.getState().upsert({ task_id: "t3", progress: 0.5, status: "running" });
    useTaskStore.getState().remove("t3");
    expect(useTaskStore.getState().tasks.t3).toBeUndefined();
  });

  it("toggles the collapsed flag", () => {
    expect(useTaskStore.getState().collapsed).toBe(false);
    useTaskStore.getState().setCollapsed(true);
    expect(useTaskStore.getState().collapsed).toBe(true);
  });
});

describe("chatStore", () => {
  beforeEach(() => {
    useChat.setState({ messages: [], status: "idle", error: null, agentReady: false });
  });

  it("reset clears messages and error", () => {
    useChat.setState({ messages: [{ id: "x", role: "user", text: "y", streaming: false, created_at: 1 }], error: "x" });
    useChat.getState().reset();
    const s = useChat.getState();
    expect(s.messages).toEqual([]);
    expect(s.error).toBeNull();
    expect(s.status).toBe("idle");
  });
});
