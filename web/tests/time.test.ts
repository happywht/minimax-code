/**
 * Tests for `formatRelative` — covers each branch of the relative-time
 * ladder and a few edge cases (clock skew, invalid input, year
 * boundary).
 */
import { describe, expect, it } from "vitest";
import { formatRelative } from "../src/lib/time";

const NOW = new Date("2026-06-02T12:00:00Z").getTime();
const SEC = 1_000;
const MIN = 60 * SEC;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

describe("formatRelative", () => {
  it('returns "just now" for events within the last minute', () => {
    expect(formatRelative(NOW, NOW)).toBe("just now");
    expect(formatRelative(NOW - 30 * SEC, NOW)).toBe("just now");
    expect(formatRelative(NOW - 59 * SEC, NOW)).toBe("just now");
  });

  it('returns "{n}m ago" for events within the last hour', () => {
    expect(formatRelative(NOW - 5 * MIN, NOW)).toBe("5m ago");
    expect(formatRelative(NOW - 45 * MIN, NOW)).toBe("45m ago");
    // Boundary: 59m 59s is still in the minute bucket, but our 1m
    // floor covers any sub-minute remainder from the division.
    expect(formatRelative(NOW - 59 * MIN, NOW)).toBe("59m ago");
  });

  it('returns "{n}h ago" for events within the last 24 hours', () => {
    expect(formatRelative(NOW - 2 * HOUR, NOW)).toBe("2h ago");
    expect(formatRelative(NOW - 23 * HOUR, NOW)).toBe("23h ago");
  });

  it('returns "{n}d ago" for events within the last 7 days', () => {
    expect(formatRelative(NOW - 1 * DAY, NOW)).toBe("1d ago");
    expect(formatRelative(NOW - 3 * DAY, NOW)).toBe("3d ago");
    expect(formatRelative(NOW - 6 * DAY, NOW)).toBe("6d ago");
  });

  it("renders same-year dates older than 7 days as 'Mon D'", () => {
    // 8 days back = 2026-05-25
    expect(formatRelative(NOW - 8 * DAY, NOW)).toBe("May 25");
  });

  it("renders prior-year dates with the year suffix", () => {
    // 400 days back from 2026-06-02 is 2025-04-28.
    expect(formatRelative(NOW - 400 * DAY, NOW)).toBe("Apr 28, 2025");
  });

  it("clamps clock skew (future timestamps) to 'just now'", () => {
    // 5 minutes in the future — should not say "-5m ago".
    expect(formatRelative(NOW + 5 * MIN, NOW)).toBe("just now");
  });

  it("returns a placeholder for non-finite input", () => {
    expect(formatRelative(Number.NaN, NOW)).toBe("—");
    expect(formatRelative(Number.POSITIVE_INFINITY, NOW)).toBe("—");
  });
});
