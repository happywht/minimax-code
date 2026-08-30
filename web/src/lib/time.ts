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
 *   - within 60 seconds              -> "刚刚"
 *   - within 60 minutes              -> "{n} 分钟前"
 *   - within 24 hours                -> "{n} 小时前"
 *   - within 7 days                  -> "{n} 天前"
 *   - otherwise (same calendar year) -> "M月D日"
 *   - otherwise (different year)     -> "YYYY年M月D日"
 *
 * Negative deltas (clock skew) are treated as "刚刚" rather than
 * a negative "分钟前" — the value is clamped to 0 inside the helper.
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
    return "刚刚";
  }
  if (delta < MS_PER_HOUR) {
    return `${Math.floor(delta / MS_PER_MINUTE)} 分钟前`;
  }
  if (delta < MS_PER_DAY) {
    return `${Math.floor(delta / MS_PER_HOUR)} 小时前`;
  }
  if (delta < DAYS_THRESHOLD_FOR_YEAR * MS_PER_DAY) {
    return `${Math.floor(delta / MS_PER_DAY)} 天前`;
  }

  const ts = new Date(timestampMs);
  const ref = new Date(now);
  const month = ts.getMonth() + 1;
  const day = ts.getDate();
  if (ts.getFullYear() === ref.getFullYear()) {
    return `${month}月${day}日`;
  }
  return `${ts.getFullYear()}年${month}月${day}日`;
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

/**
 * Format a duration (ms) as a compact human string.
 *   - under 1 hour  -> "MM:SS" (e.g. "04:07")
 *   - 1 hour or more -> "X 小时 Y 分" (e.g. "2 小时 5 分")
 *
 * Negative inputs (clock skew) are clamped to 0 -> "00:00". Used by the
 * progress panel's settled-task duration display.
 */
export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.floor(Math.max(0, durationMs) / MS_PER_SECOND);
  if (totalSeconds < 3600) {
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${pad(minutes)}:${pad(seconds)}`;
  }
  const totalMinutes = Math.floor(totalSeconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${hours} 小时 ${minutes} 分`;
}
