/**
 * Tests for P1#19 — memory eviction utilities.
 *
 * Verifies:
 *   1. trimArray returns same reference when under limit
 *   2. trimArray keeps last N items when over limit
 *   3. trimArray handles empty array
 *   4. trimArray handles exact-size array
 *   5. evictOldest returns same reference when under limit
 *   6. evictOldest evicts oldest entries by timestamp
 *   7. evictOldest handles exact-size record
 *   8. evictOldest handles empty record
 *   9. Constants are sensible values
 */
import { describe, it, expect } from "vitest";
import {
  trimArray,
  evictOldest,
  MAX_MESSAGES,
  MAX_TASKS,
  MAX_SUBAGENT_RUNS,
  MAX_TEAM_RUNS,
  MAX_NOTIFICATIONS,
} from "../eviction";

describe("eviction utilities (P1#19)", () => {
  // ── trimArray ─────────────────────────────────────────────────────
  describe("trimArray", () => {
    it("returns same reference when under limit", () => {
      const arr = [1, 2, 3];
      const result = trimArray(arr, 5);
      expect(result).toBe(arr); // same reference
    });

    it("returns same reference at exact limit", () => {
      const arr = [1, 2, 3];
      const result = trimArray(arr, 3);
      expect(result).toBe(arr);
    });

    it("keeps last N items when over limit", () => {
      const arr = [1, 2, 3, 4, 5, 6, 7];
      const result = trimArray(arr, 3);
      expect(result).toEqual([5, 6, 7]);
    });

    it("handles empty array", () => {
      const arr: number[] = [];
      const result = trimArray(arr, 5);
      expect(result).toBe(arr); // same reference
    });

    it("trims by 1 when exceeding by 1", () => {
      const arr = [10, 20, 30, 40];
      const result = trimArray(arr, 3);
      expect(result).toEqual([20, 30, 40]);
    });

    it("works with objects", () => {
      const arr = [{ id: 1 }, { id: 2 }, { id: 3 }];
      const result = trimArray(arr, 2);
      expect(result).toEqual([{ id: 2 }, { id: 3 }]);
    });
  });

  // ── evictOldest ───────────────────────────────────────────────────
  describe("evictOldest", () => {
    it("returns same reference when under limit", () => {
      const rec = { a: { ts: 1 }, b: { ts: 2 } };
      const result = evictOldest(rec, 5, (e) => e.ts);
      expect(result).toBe(rec);
    });

    it("returns same reference at exact limit", () => {
      const rec = { a: { ts: 1 }, b: { ts: 2 } };
      const result = evictOldest(rec, 2, (e) => e.ts);
      expect(result).toBe(rec);
    });

    it("evicts oldest entries by timestamp", () => {
      const rec = {
        a: { ts: 100 },
        b: { ts: 300 },
        c: { ts: 200 },
        d: { ts: 400 },
      };
      const result = evictOldest(rec, 2, (e) => e.ts);
      // Keep 2 newest: b(300) and d(400)
      expect(Object.keys(result)).toHaveLength(2);
      expect(result.b).toEqual({ ts: 300 });
      expect(result.d).toEqual({ ts: 400 });
    });

    it("handles empty record", () => {
      const rec: Record<string, { ts: number }> = {};
      const result = evictOldest(rec, 5, (e) => e.ts);
      expect(result).toBe(rec);
    });

    it("evicts multiple entries at once", () => {
      const rec: Record<string, { ts: number }> = {};
      for (let i = 0; i < 10; i++) {
        rec[`k${i}`] = { ts: i * 100 };
      }
      const result = evictOldest(rec, 3, (e) => e.ts);
      expect(Object.keys(result)).toEqual(["k7", "k8", "k9"]);
    });
  });

  // ── Constants ─────────────────────────────────────────────────────
  describe("constants", () => {
    it("MAX_MESSAGES is reasonable (100–2000)", () => {
      expect(MAX_MESSAGES).toBeGreaterThanOrEqual(100);
      expect(MAX_MESSAGES).toBeLessThanOrEqual(2000);
    });

    it("MAX_TASKS is reasonable (10–500)", () => {
      expect(MAX_TASKS).toBeGreaterThanOrEqual(10);
      expect(MAX_TASKS).toBeLessThanOrEqual(500);
    });

    it("MAX_SUBAGENT_RUNS is reasonable (10–200)", () => {
      expect(MAX_SUBAGENT_RUNS).toBeGreaterThanOrEqual(10);
      expect(MAX_SUBAGENT_RUNS).toBeLessThanOrEqual(200);
    });

    it("MAX_TEAM_RUNS is reasonable (5–100)", () => {
      expect(MAX_TEAM_RUNS).toBeGreaterThanOrEqual(5);
      expect(MAX_TEAM_RUNS).toBeLessThanOrEqual(100);
    });

    it("MAX_NOTIFICATIONS is reasonable (50–500)", () => {
      expect(MAX_NOTIFICATIONS).toBeGreaterThanOrEqual(50);
      expect(MAX_NOTIFICATIONS).toBeLessThanOrEqual(500);
    });
  });
});
