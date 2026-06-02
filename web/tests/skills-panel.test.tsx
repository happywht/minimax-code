/**
 * Tests for the SkillsPanel — verifies the panel fetches skills on
 * mount, renders one row per skill with a working enable/disable
 * toggle, and renders the empty state when no skills are installed.
 */
import { describe, expect, it, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SkillsPanel } from "../src/components/SkillsPanel";
import { useSkillStore } from "../src/stores";

vi.mock("../src/components/ErrorBoundary", () => ({
  toast: { error: vi.fn(), info: vi.fn(), success: vi.fn() },
  toastBus: { push: vi.fn(), dismiss: vi.fn() },
}));

vi.mock("../src/ipc", async () => {
  const actual = await vi.importActual<typeof import("../src/ipc")>("../src/ipc");
  return {
    ...actual,
    typedIPC: {
      ...actual.typedIPC,
      listSkills: vi.fn(async () => ({
        skills: [
          {
            id: "commit-helper",
            name: "Commit Helper",
            description: "Draft commit messages from staged diffs",
            enabled: true,
            builtin: true,
          },
          {
            id: "code-review",
            name: "Code Review",
            description: "Review a diff for bugs and style issues",
            enabled: false,
            builtin: true,
          },
        ],
      })),
      enableSkill: vi.fn(async () => ({ ok: true })),
      disableSkill: vi.fn(async () => ({ ok: true })),
    },
  };
});

describe("SkillsPanel", () => {
  beforeEach(() => {
    useSkillStore.setState({ skills: [], loading: false });
  });

  it("calls listSkills on mount and renders one row per skill", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SkillsPanel />);
    await waitFor(() => {
      expect(typedIPC.listSkills).toHaveBeenCalled();
    });
    expect(screen.getByTestId("skills-row-commit-helper")).toBeInTheDocument();
    expect(screen.getByTestId("skills-row-code-review")).toBeInTheDocument();
    expect(screen.getByText("Commit Helper")).toBeInTheDocument();
    expect(screen.getByText("Code Review")).toBeInTheDocument();
    expect(screen.getByTestId("skills-list")).toBeInTheDocument();
  });

  it("toggles a skill on: calls enableSkill and flips the local state", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SkillsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("skills-row-code-review")).toBeInTheDocument();
    });
    const row = screen.getByTestId("skills-row-code-review");
    expect(row).toHaveAttribute("data-enabled", "false");

    const checkbox = screen
      .getByTestId("skills-toggle-code-review")
      .querySelector('input[type="checkbox"]') as HTMLInputElement;
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(typedIPC.enableSkill).toHaveBeenCalledWith("code-review");
    });
    await waitFor(() => {
      expect(
        useSkillStore.getState().skills.find((s) => s.id === "code-review")?.enabled,
      ).toBe(true);
    });
  });

  it("toggles a skill off: calls disableSkill and flips the local state", async () => {
    const { typedIPC } = await import("../src/ipc");
    render(<SkillsPanel />);
    await waitFor(() => {
      expect(screen.getByTestId("skills-row-commit-helper")).toBeInTheDocument();
    });
    const checkbox = screen
      .getByTestId("skills-toggle-commit-helper")
      .querySelector('input[type="checkbox"]') as HTMLInputElement;
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(typedIPC.disableSkill).toHaveBeenCalledWith("commit-helper");
    });
    await waitFor(() => {
      expect(
        useSkillStore.getState().skills.find((s) => s.id === "commit-helper")?.enabled,
      ).toBe(false);
    });
  });

  it("renders the empty state when the store has no skills", async () => {
    // Override the list mock for this case so the on-mount refresh
    // resolves to an empty list (not the default 2 fixtures).
    const { typedIPC } = await import("../src/ipc");
    vi.mocked(typedIPC.listSkills).mockResolvedValueOnce({ skills: [] });
    render(<SkillsPanel />);
    // The panel calls refresh() on mount; the empty state lands
    // after the (mocked) listSkills promise resolves.
    await waitFor(() => {
      expect(screen.getByTestId("skills-empty")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("skills-list")).toBeNull();
  });
});
