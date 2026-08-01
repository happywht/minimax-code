import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarkdownCode } from "../CodeBlock";

vi.mock("../../../lib/shikiLoader", () => ({
  highlight: vi.fn(async () => "<pre><code>highlighted</code></pre>"),
}));

describe("MarkdownCode", () => {
  it("renders a source tag when the fence info includes a file path", async () => {
    render(
      <MarkdownCode className="language-ts src/auth.ts#L10-20">
        {"export function login() {}"}
      </MarkdownCode>,
    );
    const tag = await screen.findByTestId("code-source-tag");
    expect(tag).toHaveTextContent("src/auth.ts");
    expect(tag).toHaveTextContent("#L10-20");
    expect(tag).toHaveAttribute("title", "src/auth.ts#L10-20");
  });

  it("does not render a source tag for plain fences", async () => {
    render(<MarkdownCode className="language-ts">{"const x = 1"}</MarkdownCode>);
    await waitFor(() => {
      expect(screen.queryByTestId("code-source-tag")).toBeNull();
    });
  });
});
