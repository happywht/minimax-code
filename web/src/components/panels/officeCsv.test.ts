import { describe, expect, it } from "vitest";
import { csvToOfficeSpec, parseOfficeCsv } from "./officeCsv";

describe("office CSV conversion", () => {
  it("preserves quoted values and converts only selected types", () => {
    const table = parseOfficeCsv('\uFEFF名称,数量,可用\r\n"含,逗号\n和""引号""",12.5,TRUE\r\n=literal,,false\r\n');
    expect(csvToOfficeSpec(table, ["text", "number", "boolean"]).rows).toEqual([
      ['含,逗号\n和"引号"', 12.5, true], ["=literal", null, false],
    ]);
  });
  it("reports malformed rows and unsafe conversion without rounding", () => {
    expect(() => parseOfficeCsv('a,b\n"unclosed')).toThrow("未闭合");
    expect(() => parseOfficeCsv("a,b\n1")).toThrow("第 2 行列数");
    expect(() => csvToOfficeSpec(parseOfficeCsv("编号\n1234567890123456"), ["number"])).toThrow("第 2 行「编号」");
    expect(() => csvToOfficeSpec(parseOfficeCsv("标志\nyes"), ["boolean"])).toThrow("true/false");
  });
  it("rejects oversized edited cells before generation", () => {
    expect(() => csvToOfficeSpec({ headers: ["列"], rows: [["字".repeat(4097)]] }, ["text"]))
      .toThrow("第 2 行「列」超过 4096");
  });
});
