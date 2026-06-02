/**
 * Small time formatting helpers used by the sidebar / panels.
 *
 * `formatRelative(ms)` renders a session's `updated_at` (or any
 * epoch-ms timestamp) as a short human-friendly string for the
 * sidebar history list.
 *
 * Rules (each branch is exclusive):
 *   - within 60 seconds              -> "just now"
 *   - within 60 minutes              -> "{n}m ago"
 *   - within 24 hours                -> "{n}h ago"
 *   - within 7 days                  -> "{n}d ago"
 *   - otherwise (same calendar year) -> "MMM D"   (e.g. "Jun 2")
 *   - otherwise (different year)     -> "MMM D, YYYY"
 *
 * Negative deltas (clock skew) are treated as "just now" rather than
 * "−5m ago" — the value is clamped to 0 inside the helper.
 *
 * The function is pure (no `Date.now()` calls); pass `now` explicitly
 * to make tests deterministic. The default arg is convenient for UI
 * code that always wants wall-clock time.
 */

const MS_PER_SECOND = 1_000;
const MS_PER_MINUTE = 60 * MS_PER_SECOND;
const MS_PER_HOUR = 60 * MS_PER_MINUTE;
const MS_PER_DAY = 24 * MS_PER_HOUR;
const DAYS_THRESHOLD_FOR_YEAR = 7;

const MONTH_NAMES = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

export function formatRelative(
  timestampMs: number,
  now: number = Date.now(),
): string {
  if (!Number.isFinite(timestampMs)) return "—";

  const delta = Math.max(0, now - timestampMs);

  if (delta < MS_PER_MINUTE) {
    return "just now";
  }
  if (delta < MS_PER_HOUR) {
    return `${Math.floor(delta / MS_PER_MINUTE)}m ago`;
  }
  if (delta < MS_PER_DAY) {
    return `${Math.floor(delta / MS_PER_HOUR)}h ago`;
  }
  if (delta < DAYS_THRESHOLD_FOR_YEAR * MS_PER_DAY) {
    return `${Math.floor(delta / MS_PER_DAY)}d ago`;
  }

  const ts = new Date(timestampMs);
  const ref = new Date(now);
  const month = MONTH_NAMES[ts.getMonth()] ?? "—";
  const day = ts.getDate();
  if (ts.getFullYear() === ref.getFullYear()) {
    return `${month} ${day}`;
  }
  return `${month} ${day}, ${ts.getFullYear()}`;
}
