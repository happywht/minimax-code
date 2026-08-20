/**
 * Lightweight form validation utilities.
 *
 * Designed for inline validation in controlled components:
 *   - Each validator returns `""` on success or a descriptive error string.
 *   - `compose(...validators)` chains multiple rules into one function.
 *   - `validateAll(fields)` runs multiple field validators at once.
 *
 * Example:
 *   const nameError = required(name) || minLength(2)(name) || maxLength(50)(name);
 *   const urlError  = required(url) || urlFormat(url);
 */

export type Validator = (value: string) => string;

/** Field must not be empty (after trim). */
export function required(label = "此字段"): Validator {
  return (v) => (v.trim() ? "" : `请填写${label}`);
}

/** Minimum character length (after trim). */
export function minLength(min: number, label = "该值"): Validator {
  return (v) => (v.trim().length >= min ? "" : `${label}长度至少 ${min} 个字符`);
}

/** Maximum character length. */
export function maxLength(max: number, label = "该值"): Validator {
  return (v) => (v.length <= max ? "" : `${label}长度至多 ${max} 个字符`);
}

/** Must be a valid URL (http/https). */
export function urlFormat(label = "URL"): Validator {
  return (v) => {
    if (!v.trim()) return "";
    try {
      const u = new URL(v);
      return u.protocol === "http:" || u.protocol === "https:" ? "" : `${label}必须使用 http 或 https`;
    } catch {
      return `${label}不是有效的 URL`;
    }
  };
}

/** Must be a valid regex pattern. */
export function regexFormat(label = "模式"): Validator {
  return (v) => {
    if (!v.trim()) return "";
    try {
      new RegExp(v);
      return "";
    } catch {
      return `${label}不是有效的正则表达式`;
    }
  };
}

/** Must match a specific regex pattern. */
export function pattern(re: RegExp, message: string): Validator {
  return (v) => (re.test(v) ? "" : message);
}

/** Chain multiple validators — returns first error or "". */
export function compose(...validators: Validator[]): Validator {
  return (v) => {
    for (const fn of validators) {
      const err = fn(v);
      if (err) return err;
    }
    return "";
  };
}

/** Validate multiple fields at once. Returns an object with field names as keys and error strings as values. */
export function validateAll(
  fields: Record<string, [string, Validator]>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const [name, [value, validator]] of Object.entries(fields)) {
    const err = validator(value);
    if (err) errors[name] = err;
  }
  return errors;
}

/** Check if an errors object has any non-empty values. */
export function hasErrors(errors: Record<string, string>): boolean {
  return Object.values(errors).some((e) => e.length > 0);
}
