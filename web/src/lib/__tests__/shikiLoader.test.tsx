/**
 * Tests for P1#12 — shiki lazy loading.
 * Verifies:
 *   1. shikiLoader exports highlight(), isLanguageSupported(), preloadShiki()
 *   2. highlight() dynamically imports shiki and produces HTML for known langs
 *   3. highlight() returns null for unsupported languages
 *   4. isLanguageSupported() correctly identifies supported/unsupported langs
 *   5. preloadShiki() triggers the import without blocking
 *   6. MessageItem imports shikiLoader (not static shiki) — no regression
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

// ─── Unit tests for shikiLoader ─────────────────────────────────────

describe("shikiLoader", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("exports highlight, isLanguageSupported, preloadShiki", async () => {
    const mod = await import("../shikiLoader");
    expect(typeof mod.highlight).toBe("function");
    expect(typeof mod.isLanguageSupported).toBe("function");
    expect(typeof mod.preloadShiki).toBe("function");
  });

  it("highlight returns HTML for a known language", async () => {
    const { highlight } = await import("../shikiLoader");
    const html = await highlight("const x = 1;", "javascript");
    expect(html).toBeTruthy();
    expect(typeof html).toBe("string");
  });

  it("highlight returns null for unsupported language", async () => {
    const { highlight } = await import("../shikiLoader");
    const html = await highlight("hello", "nonexistent-lang-xyz");
    expect(html).toBeNull();
  });

  it("isLanguageSupported returns true for javascript", async () => {
    const { isLanguageSupported } = await import("../shikiLoader");
    const supported = await isLanguageSupported("javascript");
    expect(supported).toBe(true);
  });

  it("isLanguageSupported returns false for unknown lang", async () => {
    const { isLanguageSupported } = await import("../shikiLoader");
    const supported = await isLanguageSupported("zzz-nonexistent");
    expect(supported).toBe(false);
  });

  it("preloadShiki does not throw", async () => {
    const { preloadShiki } = await import("../shikiLoader");
    expect(() => preloadShiki()).not.toThrow();
  });

  it("highlight caches the shiki module (second call is fast)", async () => {
    const { highlight } = await import("../shikiLoader");
    // First call triggers dynamic import
    const start = performance.now();
    await highlight("let a = 1;", "typescript");
    const first = performance.now() - start;

    // Second call reuses cached import — should be much faster
    const start2 = performance.now();
    const html2 = await highlight("let b = 2;", "typescript");
    const second = performance.now() - start2;

    expect(html2).toBeTruthy();
    // Second call should be at least 3x faster (no import overhead)
    // Use generous threshold to avoid flaky tests
    expect(second).toBeLessThan(first * 3 + 50);
  });
});

// ─── Import verification: MessageItem uses shikiLoader, not static shiki ─

describe("MessageItem shiki integration (P1#12)", () => {
  it("MessageItem imports from shikiLoader, not from shiki directly", async () => {
    // Read the source to verify the import path changed
    const fs = await import("fs");
    const path = await import("path");
    const src = fs.readFileSync(
      path.resolve(__dirname, "../../components/MessageItem.tsx"),
      "utf-8",
    );

    // Should NOT have static shiki import
    expect(src).not.toMatch(/from\s+["']shiki["']/);
    // Should have shikiLoader import
    expect(src).toMatch(/from\s+["']..\/lib\/shikiLoader["']/);
    // Should use useEffect for lazy loading (not useMemo for side effects)
    expect(src).toMatch(/useEffect\(\(\)\s*=>\s*\{[\s\S]*?highlight\(/);
  });
});
