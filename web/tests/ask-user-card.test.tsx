/**
 * Component tests for the AskUserCard questionnaire.
 *
 * Covers the visibility contract (session match), single/multi select
 * interaction, the free-text "其他" branch, submit gating (all questions
 * must be answered) and the skip path.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AskUserCard } from "../src/components/chat/AskUserCard";
import { useChat } from "../src/stores/chat";
import { useSessionStore } from "../src/stores/sessionStore";
import type { AskUserData } from "../src/types/ipc";

const QUESTIONNAIRE: AskUserData = {
  request_id: "ask_abc",
  session_id: "sess-1",
  timeout_s: 600,
  questions: [
    {
      question: "Which database?",
      header: "Database",
      options: [
        { label: "SQLite", description: "embedded" },
        { label: "PostgreSQL", description: "server" },
      ],
      multiSelect: false,
    },
    {
      question: "Which features?",
      header: "Features",
      options: [
        { label: "Auth", description: "" },
        { label: "Audit", description: "" },
      ],
      multiSelect: true,
    },
  ],
};

const submitSpy = vi.fn().mockResolvedValue(undefined);
const skipSpy = vi.fn().mockResolvedValue(undefined);

function mount(): ReturnType<typeof render> {
  return render(<AskUserCard />);
}

describe("AskUserCard", () => {
  beforeEach(() => {
    submitSpy.mockClear();
    skipSpy.mockClear();
    useChat.setState({
      askUser: QUESTIONNAIRE,
      submitAskUser: submitSpy,
      skipAskUser: skipSpy,
    });
    useSessionStore.setState({ currentSessionId: "sess-1" });
  });

  it("renders nothing without a pending questionnaire", () => {
    useChat.setState({ askUser: null });
    const { container } = mount();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the questionnaire belongs to another session", () => {
    useSessionStore.setState({ currentSessionId: "sess-other" });
    const { container } = mount();
    expect(container).toBeEmptyDOMElement();
  });

  it("renders every question with its options", () => {
    mount();
    expect(screen.getByTestId("ask-user-card")).toHaveAttribute("data-request-id", "ask_abc");
    expect(screen.getByTestId("ask-user-card-header-0")).toHaveTextContent("Database");
    expect(screen.getByTestId("ask-user-card-header-1")).toHaveTextContent("Features");
    expect(screen.getAllByTestId("ask-user-card-option-0")).toHaveLength(2);
    expect(screen.getAllByTestId("ask-user-card-option-1")).toHaveLength(2);
  });

  it("keeps submit disabled until every question is answered", () => {
    mount();
    expect(screen.getByTestId("ask-user-card-submit")).toBeDisabled();
    expect(screen.getByTestId("ask-user-card-hint")).toHaveTextContent("第 1 问尚未选择");

    fireEvent.click(screen.getAllByTestId("ask-user-card-option-0")[0]);
    expect(screen.getByTestId("ask-user-card-submit")).toBeDisabled();
    expect(screen.getByTestId("ask-user-card-hint")).toHaveTextContent("第 2 问尚未选择");

    fireEvent.click(screen.getAllByTestId("ask-user-card-option-1")[0]);
    expect(screen.getByTestId("ask-user-card-submit")).toBeEnabled();
    expect(screen.getByTestId("ask-user-card-hint")).toHaveTextContent("");
  });

  it("submits a label string for single-select and an array for multi-select", async () => {
    mount();
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-0")[1]); // PostgreSQL
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-1")[0]); // Auth
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-1")[1]); // Audit
    fireEvent.click(screen.getByTestId("ask-user-card-submit"));

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy).toHaveBeenCalledWith(["PostgreSQL", ["Auth", "Audit"]]);
  });

  it("multi-select toggles: clicking a selected option deselects it", async () => {
    mount();
    const multi = screen.getAllByTestId("ask-user-card-option-1");
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-0")[0]);
    fireEvent.click(multi[0]);
    fireEvent.click(multi[1]);
    fireEvent.click(multi[1]); // deselect Audit
    fireEvent.click(screen.getByTestId("ask-user-card-submit"));

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy).toHaveBeenCalledWith(["SQLite", ["Auth"]]);
  });

  it("single-select switches: a second click replaces the first choice", async () => {
    mount();
    const single = screen.getAllByTestId("ask-user-card-option-0");
    fireEvent.click(single[0]);
    fireEvent.click(single[1]);
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-1")[0]);
    fireEvent.click(screen.getByTestId("ask-user-card-submit"));

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy).toHaveBeenCalledWith(["PostgreSQL", ["Auth"]]);
  });

  it("其他 free text overrides single-select and appends to multi-select", async () => {
    mount();
    // Q0: choose 其他 and type custom text — replaces chip selection.
    fireEvent.click(screen.getByTestId("ask-user-card-other-0"));
    fireEvent.change(screen.getByTestId("ask-user-card-other-input-0"), {
      target: { value: "MySQL" },
    });
    // Q1: chip + 其他 text both contribute.
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-1")[0]);
    fireEvent.click(screen.getByTestId("ask-user-card-other-1"));
    fireEvent.change(screen.getByTestId("ask-user-card-other-input-1"), {
      target: { value: "SSO" },
    });
    fireEvent.click(screen.getByTestId("ask-user-card-submit"));

    await waitFor(() => expect(submitSpy).toHaveBeenCalledTimes(1));
    expect(submitSpy).toHaveBeenCalledWith(["other: MySQL", ["Auth", "other: SSO"]]);
  });

  it("closing 其他 drops the draft text", () => {
    mount();
    fireEvent.click(screen.getByTestId("ask-user-card-other-0"));
    fireEvent.change(screen.getByTestId("ask-user-card-other-input-0"), {
      target: { value: "MySQL" },
    });
    fireEvent.click(screen.getByTestId("ask-user-card-other-0")); // close
    expect(screen.queryByTestId("ask-user-card-other-input-0")).toBeNull();
    // Q0 unanswered again.
    expect(screen.getByTestId("ask-user-card-hint")).toHaveTextContent("第 1 问尚未选择");
  });

  it("skip routes through skipAskUser", async () => {
    mount();
    fireEvent.click(screen.getByTestId("ask-user-card-skip"));
    await waitFor(() => expect(skipSpy).toHaveBeenCalledTimes(1));
  });

  it("switching questionnaire resets local selections", () => {
    mount();
    fireEvent.click(screen.getAllByTestId("ask-user-card-option-0")[0]);
    expect(screen.getAllByTestId("ask-user-card-option-0")[0]).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    // Wrap the external-store update in act so the reset useEffect flushes
    // before the assertions below.
    act(() => {
      useChat.setState({
        askUser: { ...QUESTIONNAIRE, request_id: "ask_def" },
      });
    });
    // New draft — nothing selected under the new request id.
    expect(screen.getAllByTestId("ask-user-card-option-0")[0]).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    expect(screen.getByTestId("ask-user-card")).toHaveAttribute("data-request-id", "ask_def");
  });
});
