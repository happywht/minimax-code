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
  it('returns "刚刚" for events within the last minute', () => {
    expect(formatRelative(NOW, NOW)).toBe("刚刚");
    expect(formatRelative(NOW - 30 * SEC, NOW)).toBe("刚刚");
    expect(formatRelative(NOW - 59 * SEC, NOW)).toBe("刚刚");
  });

  it('returns "{n} 分钟前" for events within the last hour', () => {
    expect(formatRelative(NOW - 5 * MIN, NOW)).toBe("5 分钟前");
    expect(formatRelative(NOW - 45 * MIN, NOW)).toBe("45 分钟前");
    // Boundary: 59m 59s is still in the minute bucket, but our 1m
    // floor covers any sub-minute remainder from the division.
    expect(formatRelative(NOW - 59 * MIN, NOW)).toBe("59 分钟前");
  });

  it('returns "{n} 小时前" for events within the last 24 hours', () => {
    expect(formatRelative(NOW - 2 * HOUR, NOW)).toBe("2 小时前");
    expect(formatRelative(NOW - 23 * HOUR, NOW)).toBe("23 小时前");
  });

  it('returns "{n} 天前" for events within the last 7 days', () => {
    expect(formatRelative(NOW - 1 * DAY, NOW)).toBe("1 天前");
    expect(formatRelative(NOW - 3 * DAY, NOW)).toBe("3 天前");
    expect(formatRelative(NOW - 6 * DAY, NOW)).toBe("6 天前");
  });

  it("renders same-year dates older than 7 days as 'M月D日'", () => {
    // 8 days back = 2026-05-25
    expect(formatRelative(NOW - 8 * DAY, NOW)).toBe("5月25日");
  });

  it("renders prior-year dates with the year suffix", () => {
    // 400 days back from 2026-06-02 is 2025-04-28.
    expect(formatRelative(NOW - 400 * DAY, NOW)).toBe("2025年4月28日");
  });

  it("clamps clock skew (future timestamps) to '刚刚'", () => {
    // 5 minutes in the future — should not say a negative "分钟前".
    expect(formatRelative(NOW + 5 * MIN, NOW)).toBe("刚刚");
  });

  it("returns a placeholder for non-finite input", () => {
    expect(formatRelative(Number.NaN, NOW)).toBe("—");
    expect(formatRelative(Number.POSITIVE_INFINITY, NOW)).toBe("—");
  });
});
