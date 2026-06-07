/**
 * Tests for P1#13 — Skeleton placeholder components.
 * Verifies:
 *   1. All 4 skeleton variants render with correct CSS classes
 *   2. Custom className is appended
 *   3. SkeletonTable renders the correct number of rows
 *   4. SkeletonCard renders the correct number of content lines
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  SkeletonLine,
  SkeletonCircle,
  SkeletonCard,
  SkeletonTable,
} from "../Skeleton";

describe("Skeleton components (P1#13)", () => {
  it("SkeletonLine renders with pulse animation", () => {
    const { container } = render(<SkeletonLine />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("animate-pulse");
    expect(el.className).toContain("rounded");
    expect(el.className).toContain("bg-minimax-border/40");
  });

  it("SkeletonLine appends custom className", () => {
    const { container } = render(<SkeletonLine className="w-2/3 h-6" />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("w-2/3");
    expect(el.className).toContain("h-6");
    expect(el.className).toContain("animate-pulse");
  });

  it("SkeletonCircle renders as a round element", () => {
    const { container } = render(<SkeletonCircle />);
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("rounded-full");
    expect(el.className).toContain("animate-pulse");
  });

  it("SkeletonCard renders with header + content lines", () => {
    const { container } = render(<SkeletonCard lines={3} />);
    // Should have header line + 3 content lines = 4 skeleton lines total
    const lines = container.querySelectorAll(".animate-pulse");
    expect(lines.length).toBe(4); // 1 header + 3 content
  });

  it("SkeletonCard wraps in a bordered card container", () => {
    const { container } = render(<SkeletonCard />);
    const card = container.firstElementChild as HTMLElement;
    expect(card.className).toContain("border");
    expect(card.className).toContain("rounded-lg");
  });

  it("SkeletonTable renders the correct number of rows", () => {
    const { container } = render(<SkeletonTable rows={4} />);
    // Each row is a flex container with 3 skeleton lines
    const rows = container.querySelectorAll(".flex.items-center");
    expect(rows.length).toBe(4);
  });

  it("SkeletonTable defaults to 5 rows", () => {
    const { container } = render(<SkeletonTable />);
    const rows = container.querySelectorAll(".flex.items-center");
    expect(rows.length).toBe(5);
  });

  it("SkeletonTable appends custom className", () => {
    const { container } = render(<SkeletonTable className="mt-4" />);
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("mt-4");
  });
});
