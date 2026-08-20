/**
 * Tests for P1#15 — form validation utilities.
 * Verifies:
 *   1. required() rejects empty/whitespace strings, accepts non-empty
 *   2. minLength() enforces minimum length
 *   3. maxLength() enforces maximum length
 *   4. urlFormat() validates http/https URLs
 *   5. regexFormat() validates regex patterns
 *   6. pattern() tests against custom regex
 *   7. compose() chains validators, returns first error
 *   8. validateAll() validates multiple fields
 *   9. hasErrors() detects non-empty errors
 */
import { describe, it, expect } from "vitest";
import {
  required,
  minLength,
  maxLength,
  urlFormat,
  regexFormat,
  pattern,
  compose,
  validateAll,
  hasErrors,
} from "../validators";

describe("validators (P1#15)", () => {
  // required
  it("required returns error for empty string", () => {
    expect(required()("")).toBeTruthy();
    expect(required()("  ")).toBeTruthy();
  });

  it("required returns empty string for non-empty value", () => {
    expect(required()("hello")).toBe("");
  });

  it("required uses custom label", () => {
    expect(required("Name")("")).toContain("Name");
  });

  // minLength
  it("minLength rejects short strings", () => {
    expect(minLength(3)("ab")).toBeTruthy();
  });

  it("minLength accepts strings meeting the threshold", () => {
    expect(minLength(3)("abc")).toBe("");
  });

  // maxLength
  it("maxLength rejects long strings", () => {
    expect(maxLength(5)("abcdef")).toBeTruthy();
  });

  it("maxLength accepts strings within limit", () => {
    expect(maxLength(5)("abc")).toBe("");
  });

  // urlFormat
  it("urlFormat accepts valid http/https URLs", () => {
    expect(urlFormat()("https://example.com")).toBe("");
    expect(urlFormat()("http://localhost:3000/path")).toBe("");
  });

  it("urlFormat rejects invalid URLs", () => {
    expect(urlFormat()("not-a-url")).toBeTruthy();
  });

  it("urlFormat rejects non-http protocols", () => {
    expect(urlFormat()("ftp://example.com")).toBeTruthy();
  });

  it("urlFormat returns empty for empty string (optional field)", () => {
    expect(urlFormat()("")).toBe("");
  });

  // regexFormat
  it("regexFormat accepts valid regex patterns", () => {
    expect(regexFormat()("^\\d+$")).toBe("");
    expect(regexFormat()(".*")).toBe("");
  });

  it("regexFormat rejects invalid regex", () => {
    expect(regexFormat()("[invalid")).toBeTruthy();
  });

  it("regexFormat returns empty for empty string", () => {
    expect(regexFormat()("")).toBe("");
  });

  // pattern
  it("pattern tests against custom regex", () => {
    const v = pattern(/^[a-z]+$/, "Must be lowercase letters only");
    expect(v("hello")).toBe("");
    expect(v("Hello")).toBeTruthy();
  });

  // compose
  it("compose chains validators and returns first error", () => {
    const v = compose(required("Name"), minLength(3, "Name"));
    expect(v("")).toContain("请填写");
    expect(v("ab")).toContain("至少 3");
    expect(v("abc")).toBe("");
  });

  // validateAll
  it("validateAll returns errors for invalid fields", () => {
    const errors = validateAll({
      name: ["", required("Name")],
      email: ["bad", urlFormat("Email")],
    });
    expect(errors.name).toBeTruthy();
    expect(errors.email).toBeTruthy();
  });

  it("validateAll returns empty object for valid fields", () => {
    const errors = validateAll({
      name: ["John", required("Name")],
    });
    expect(Object.keys(errors)).toHaveLength(0);
  });

  // hasErrors
  it("hasErrors returns true when errors exist", () => {
    expect(hasErrors({ name: "Required" })).toBe(true);
  });

  it("hasErrors returns false when no errors", () => {
    expect(hasErrors({})).toBe(false);
    expect(hasErrors({ name: "" })).toBe(false);
  });
});
