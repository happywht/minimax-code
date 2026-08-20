/**
 * Tests for P2#34: unified time formatting helpers.
 *
 * Covers formatRelative, formatDateTime, and formatTime with
 * both epoch-ms number and ISO-string inputs.
 */
import { describe, it, expect } from "vitest";
import { formatRelative, formatDateTime, formatTime } from "../time";

describe("formatRelative", () => {
  const NOW = 1_700_000_000_000; // fixed reference point

  it("shows '刚刚' for timestamps within 60 seconds", () => {
    expect(formatRelative(NOW, NOW)).toBe("刚刚");
    expect(formatRelative(NOW - 30_000, NOW)).toBe("刚刚");
    expect(formatRelative(NOW - 59_999, NOW)).toBe("刚刚");
  });

  it("shows minutes for timestamps within 60 minutes", () => {
    expect(formatRelative(NOW - 60_000, NOW)).toBe("1 分钟前");
    expect(formatRelative(NOW - 3_599_999, NOW)).toBe("59 分钟前");
  });

  it("shows hours for timestamps within 24 hours", () => {
    expect(formatRelative(NOW - 3_600_000, NOW)).toBe("1 小时前");
    expect(formatRelative(NOW - 86_399_999, NOW)).toBe("23 小时前");
  });

  it("shows days for timestamps within 7 days", () => {
    expect(formatRelative(NOW - 86_400_000, NOW)).toBe("1 天前");
    expect(formatRelative(NOW - 6 * 86_400_000, NOW)).toBe("6 天前");
  });

  it("shows 'M月D日' for older timestamps in the same year", () => {
    // 10 days ago
    const ts = NOW - 10 * 86_400_000;
    const result = formatRelative(ts, NOW);
    // Should be like "11月4日" — exact month depends on the date
    expect(result).toMatch(/^\d{1,2}月\d{1,2}日$/);
  });

  it("shows 'YYYY年M月D日' for different year", () => {
    // NOW = 1_700_000_000_000 ≈ Nov 14, 2023
    // Use a date in 2022 — clearly a different year, > 7 days ago
    const oldTs = new Date("2022-03-15T12:00:00Z").getTime();
    const result = formatRelative(oldTs, NOW);
    expect(result).toContain("2022");
    expect(result).toMatch(/^\d{4}年\d{1,2}月\d{1,2}日$/);
  });

  it("accepts ISO string input", () => {
    const iso = new Date(NOW - 5 * 60_000).toISOString();
    expect(formatRelative(iso, NOW)).toBe("5 分钟前");
  });

  it("clamps negative deltas (clock skew) to '刚刚'", () => {
    // Timestamp in the future
    expect(formatRelative(NOW + 300_000, NOW)).toBe("刚刚");
  });

  it("returns '—' for invalid input", () => {
    expect(formatRelative("not-a-date", NOW)).toBe("—");
    expect(formatRelative(NaN, NOW)).toBe("—");
  });
});

describe("formatDateTime", () => {
  it("formats epoch-ms as YYYY-MM-DD HH:mm", () => {
    // 2023-12-25 10:30 local time
    const d = new Date(2023, 11, 25, 10, 30);
    const result = formatDateTime(d.getTime());
    expect(result).toBe("2023-12-25 10:30");
  });

  it("formats ISO string input", () => {
    const iso = "2024-06-15T14:45:00";
    const d = new Date(iso);
    const expected = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    expect(formatDateTime(iso)).toBe(expected);
  });

  it("pads single-digit months, days, hours, minutes", () => {
    const d = new Date(2024, 0, 5, 3, 7); // Jan 5, 03:07
    expect(formatDateTime(d.getTime())).toBe("2024-01-05 03:07");
  });

  it("returns '—' for invalid input", () => {
    expect(formatDateTime("garbage")).toBe("—");
    expect(formatDateTime(NaN)).toBe("—");
  });
});

describe("formatTime", () => {
  it("formats epoch-ms as HH:mm", () => {
    const d = new Date(2024, 5, 10, 9, 5);
    expect(formatTime(d.getTime())).toBe("09:05");
  });

  it("formats ISO string input", () => {
    const iso = "2024-12-31T23:59:00";
    const d = new Date(iso);
    const expected = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    expect(formatTime(iso)).toBe(expected);
  });

  it("pads single-digit hours and minutes", () => {
    const d = new Date(2024, 0, 1, 1, 2);
    expect(formatTime(d.getTime())).toBe("01:02");
  });

  it("returns '—' for invalid input", () => {
    expect(formatTime("bad")).toBe("—");
    expect(formatTime(NaN)).toBe("—");
  });
});
