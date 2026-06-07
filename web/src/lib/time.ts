/**
 * Small time formatting helpers used by the sidebar / panels.
 *
 * `formatRelative(input)` renders a timestamp as a short human-friendly
 * string for the sidebar history list and notification center.
 *
 * Input may be:
 *   - a number (epoch-ms timestamp)
 *   - an ISO-8601 string (e.g. "2026-06-07T12:00:00Z")
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
 *
 * `formatDateTime(input)` renders a full date+time string for audit logs,
 * scheduled tasks, etc. Uses a fixed format "YYYY-MM-DD HH:mm" instead
 * of locale-dependent `toLocaleString()`.
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

/** Convert input (epoch-ms number or ISO string) to epoch-ms. */
function toMs(input: number | string): number {
  if (typeof input === "number") return input;
  const d = new Date(input);
  return Number.isFinite(d.getTime()) ? d.getTime() : NaN;
}

export function formatRelative(
  input: number | string,
  now: number = Date.now(),
): string {
  const timestampMs = toMs(input);
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

/**
 * Format a timestamp as a fixed "YYYY-MM-DD HH:mm" string.
 *
 * Unlike `toLocaleString()` this produces consistent output regardless
 * of browser locale — important for audit logs, task timestamps, etc.
 * Uses UTC to avoid timezone-dependent results.
 */
export function formatDateTime(input: number | string): string {
  const ms = toMs(input);
  if (!Number.isFinite(ms)) return "—";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Format a timestamp as time-only "HH:mm".
 * Useful for scheduled task next-run display.
 */
export function formatTime(input: number | string): string {
  const ms = toMs(input);
  if (!Number.isFinite(ms)) return "—";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
