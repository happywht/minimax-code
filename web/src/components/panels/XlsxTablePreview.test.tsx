import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import type { AttachmentOriginal } from "../../types/ipc";
import { XlsxTablePreview, parseXlsxProjection } from "./XlsxTablePreview";

const attachment = { id: "a", sha256: "hash", declared_type: "xlsx", recognized_type: "xlsx", parser_version: 2,
  parser_scope: "inert_text", parser_status: "truncated", preview_truncated: true } as AttachmentOriginal;
const projection = [
  { sheet: 1, name: "预算", state: "visible", epoch: "1900", window: "A1:T100", values: "stored_raw" },
  { cell: "A1", kind: "number", stored_type: "n", raw: "123.400" },
  { cell: "C1", kind: "formula", stored_type: "n", formula: "SUM(A1:A2)", cached_present: true, cached_raw: "999", cached_state: "stored", cache_verified: false, recalculated: false },
  { cell: "C2", kind: "formula", stored_type: "n", formula: "A1+1", cached_present: false, cached_raw: null, cached_state: "missing", cache_verified: false, recalculated: false },
  { sheet: 2, name: "隐藏", state: "hidden", epoch: "1904", window: "A1:T100", values: "stored_raw" },
  { cell: "B2", kind: "iso_date", stored_type: "d", raw: "1904-01-01" },
].map((record) => JSON.stringify(record)).join("\n") + "\n";

afterEach(cleanup);

it("renders stored coordinates, missing cells, and unverified formula cache", () => {
  render(<XlsxTablePreview attachment={attachment} preview={projection} />);
  expect(screen.getByText(/不代表单元格为空/)).toBeVisible();
  expect(screen.getByText(/预览已截断/)).toBeVisible();
  const table = screen.getByRole("table", { name: "工作表 预算" });
  expect(within(table).getByLabelText("A1")).toHaveTextContent("123.400");
  expect(within(table).getByLabelText("B1")).toHaveTextContent("—");
  expect(within(table).getByLabelText("C1")).toHaveTextContent("公式：=SUM(A1:A2)");
  expect(within(table).getByLabelText("C1")).toHaveTextContent("缓存（未核验）：999");
  expect(within(table).getByLabelText("C2")).toHaveTextContent("缓存（未核验）：缺失");
  fireEvent.change(screen.getByLabelText("选择工作表"), { target: { value: "2" } });
  expect(screen.getByRole("table", { name: "工作表 隐藏" })).toBeVisible();
  expect(screen.getByText(/日期系统：1904/)).toBeVisible();
  expect(screen.getByLabelText("B2")).toHaveTextContent("1904-01-01");
});

it("rejects malformed or duplicate records rather than inventing cells", () => {
  expect(() => parseXlsxProjection(projection + JSON.stringify({ cell: "B2", kind: "number", raw: "1" }) + "\n")).toThrow();
  render(<XlsxTablePreview attachment={attachment} preview="not JSON" />);
  expect(screen.getByRole("alert")).toHaveTextContent("结构化预览无法读取");
  expect(screen.queryByRole("table")).toBeNull();
});

it("shows extracted text literally without treating it as HTML or a formula", () => {
  const preview = [{ sheet: 1, name: "Data", state: "visible", epoch: "1900", window: "A1:T100", values: "stored_raw" },
    { cell: "A1", kind: "string", stored_type: "inlineStr", raw: "<script>bad</script>=1+1" }].map((item) => JSON.stringify(item)).join("\n") + "\n";
  const { container } = render(<XlsxTablePreview attachment={attachment} preview={preview} />);
  expect(screen.getByLabelText("A1")).toHaveTextContent("<script>bad</script>=1+1");
  expect(container.querySelector("script")).toBeNull();
});
