import { useState } from "react";
import { Button } from "../../ui";

/** The selection is limited to the already-loaded, possibly truncated preview. */
export function ReportTextExcerpt({ text, truncated, onInsert }: {
  text: string; truncated: boolean; onInsert: (text: string, location: string) => void;
}): JSX.Element {
  const [selection, setSelection] = useState({ start: 0, end: 0 });
  const excerpt = text.slice(selection.start, selection.end);
  return <div className="space-y-2 rounded border border-line p-3">
    <p className="text-xs text-ink-1">在下方预览中选中文字，再追加到报告正文。仅使用已加载预览{truncated ? "（已截断）" : ""}，不代表原件全文；内容未经核验。</p>
    <textarea aria-label="选择文本附件摘录" readOnly rows={5} value={text}
      className="w-full rounded border border-line bg-surface-1 p-2"
      onSelect={(event) => setSelection({ start: event.currentTarget.selectionStart, end: event.currentTarget.selectionEnd })} />
    <Button type="button" variant="secondary" disabled={!excerpt.trim() || excerpt.length > 4000}
      onClick={() => onInsert(excerpt, `文本预览选区 ${selection.start}–${selection.end}${truncated ? "，预览已截断" : ""}`)}>将选中文字追加到报告正文</Button>
    {excerpt.length > 4000 && <p role="status">每次最多选择 4000 个字符，请缩小选区。</p>}
  </div>;
}
