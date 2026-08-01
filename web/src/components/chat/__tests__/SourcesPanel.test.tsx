import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SourcesPanel } from "../SourcesPanel";
import type { SourceAnnotation } from "../../../types/ipc";

const SOURCES: SourceAnnotation[] = [
  { file_path: "src/auth.ts", line_range: "L10-20" },
  { file_path: "README.md", line_range: null },
];

describe("SourcesPanel", () => {
  it("renders nothing when there are no sources", () => {
    const { container } = render(<SourcesPanel sources={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a collapsed summary with the source count", () => {
    render(<SourcesPanel sources={SOURCES} />);
    expect(screen.getByText("2 来源")).toBeInTheDocument();
    expect(screen.queryByText("src/auth.ts")).not.toBeInTheDocument();
  });

  it("expands to show source chips when clicked", async () => {
    render(<SourcesPanel sources={SOURCES} />);
    await userEvent.click(screen.getByRole("button", { name: "Sources" }));
    expect(screen.getByText("src/auth.ts")).toBeInTheDocument();
    expect(screen.getByText("#L10-20")).toBeInTheDocument();
    expect(screen.getByText("README.md")).toBeInTheDocument();
  });

  it("uses the provided test id", () => {
    render(<SourcesPanel sources={SOURCES} testId="custom-sources" />);
    expect(screen.getByTestId("custom-sources")).toBeInTheDocument();
  });
});
