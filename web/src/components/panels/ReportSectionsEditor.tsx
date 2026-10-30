import { useState } from "react";
import type { DocxBlock, DocxSection } from "../../types/ipc";
import { Button, Input, Textarea } from "../../ui";

// The JSON editor remains authoritative. Refuse to rewrite shapes the visual
// editor cannot faithfully represent, so switching views never drops content.
export function readEditableSections(text: string): DocxSection[] | null {
  try {
    const value: unknown = JSON.parse(text);
    if (!Array.isArray(value) || !value.length || value.length > 50) return null;
    for (const section of value) {
      if (!section || typeof section !== "object" || Object.keys(section).some((k) => !["heading", "blocks"].includes(k)) ||
          typeof section.heading !== "string" || !Array.isArray(section.blocks) || !section.blocks.length || section.blocks.length > 100) return null;
      for (const block of section.blocks) {
        if (!block || typeof block !== "object") return null;
        if (block.kind === "paragraph") {
          if (Object.keys(block).some((k) => !["kind", "text"].includes(k)) || typeof block.text !== "string") return null;
        } else if (block.kind === "table") {
          if (Object.keys(block).some((k) => !["kind", "rows", "repeat_header"].includes(k)) ||
              (block.repeat_header !== undefined && typeof block.repeat_header !== "boolean") || !Array.isArray(block.rows) || !block.rows.length || block.rows.length > 100 ||
              !Array.isArray(block.rows[0]) || !block.rows[0].length || block.rows[0].length > 20 ||
              block.rows.some((row: unknown) => !Array.isArray(row) || row.length !== block.rows[0].length || row.some((cell: unknown) => typeof cell !== "string"))) return null;
        } else return null;
      }
    }
    return value as DocxSection[];
  } catch { return null; }
}

export function ReportSectionsEditor({ value, onChange }: { value: DocxSection[]; onChange: (sections: DocxSection[]) => void }): JSX.Element {
  const [copyNotice, setCopyNotice] = useState("");
  const update = (index: number, section: DocxSection) => onChange(value.map((item, i) => i === index ? section : item));
  const move = <T,>(items: T[], from: number, to: number): T[] => {
    const next = [...items]; [next[from], next[to]] = [next[to], next[from]]; return next;
  };
  const moveBlockToSection = (source: number, blockIndex: number, target: number) => {
    if (source === target || value[source].blocks.length <= 1 || value[target].blocks.length >= 100) return;
    const block = value[source].blocks[blockIndex];
    onChange(value.map((section, index) => {
      if (index === source) return { ...section, blocks: section.blocks.filter((_, i) => i !== blockIndex) };
      if (index === target) return { ...section, blocks: [...section.blocks, block] };
      return section;
    }));
  };
  const copyBlock = (block: DocxBlock): DocxBlock => block.kind === "paragraph" ? { ...block } : { ...block, rows: block.rows.map((row) => [...row]) };
  const applyCopy = (next: DocxSection[]) => {
    const blocks = next.reduce((count, section) => count + section.blocks.length, 0);
    const cells = next.reduce((count, section) => count + section.blocks.reduce((sum, block) => sum +
      (block.kind === "table" ? block.rows.reduce((total, row) => total + row.length, 0) : 0), 0), 0);
    if (next.length > 50 || next.some((section) => section.blocks.length > 100) || blocks > 100 || cells > 2000 ||
        new TextEncoder().encode(JSON.stringify(next)).length > 256 * 1024) {
      setCopyNotice("复制后将超过章节、内容块、单元格或草稿大小限制；原草稿未更改。"); return;
    }
    onChange(next); setCopyNotice("已插入副本；标题、表格、引用和内容请按需要编辑并核对。尚未生成文件。");
  };
  return <div className="space-y-3">
    <p>按顺序编辑章节、段落和表格。选择目标章节可将内容移至该章节末尾；每章至少保留一项内容，且最多 100 项。表格为纯文本；DOCX 每表最多 100 行、20 列，PDF 另有 10 列、1000 格的限制。重复表头始终使用当前首行，移动或删除首行后请重新核对。生成不会核验内容或版式。</p>
    {value.map((section, si) => {
      const updateBlock = (bi: number, block: DocxBlock) => update(si, { ...section, blocks: section.blocks.map((item, i) => i === bi ? block : item) });
      return <fieldset key={si} className="space-y-2 rounded border border-line p-2">
        <legend>章节 {si + 1}</legend>
        <Input aria-label={`章节 ${si + 1} 标题`} placeholder="章节标题" value={section.heading} onChange={(e) => update(si, { ...section, heading: e.target.value })} />
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" disabled={si === 0} onClick={() => onChange(move(value, si, si - 1))}>章节上移</Button>
          <Button type="button" variant="secondary" disabled={si === value.length - 1} onClick={() => onChange(move(value, si, si + 1))}>章节下移</Button>
          <Button type="button" variant="secondary" onClick={() => {
            const copy = { ...section, blocks: section.blocks.map(copyBlock) };
            applyCopy([...value.slice(0, si + 1), copy, ...value.slice(si + 1)]);
          }}>复制此章节</Button>
          <Button type="button" variant="secondary" disabled={value.length === 1} onClick={() => onChange(value.filter((_, i) => i !== si))}>删除章节</Button>
        </div>
        {section.blocks.map((block, bi) => <div key={bi} className="space-y-1 rounded border border-line p-2">
          <p>{block.kind === "paragraph" ? "段落" : "表格"} {bi + 1}</p>
          {block.kind === "paragraph" ? <Textarea aria-label={`章节 ${si + 1} 段落 ${bi + 1}`} rows={3} value={block.text} onChange={(e) => updateBlock(bi, { kind: "paragraph", text: e.target.value })} /> : <>
            <div className="overflow-x-auto"><table><thead><tr><th scope="col">行</th>{block.rows[0].map((_, ci) => <th scope="col" key={ci}>
              <span>第 {ci + 1} 列</span><div className="flex flex-wrap gap-1">
                <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 在第 ${ci + 1} 列前插入列`} disabled={block.rows[0].length >= 20} onClick={() => updateBlock(bi, { ...block, rows: block.rows.map((row) => [...row.slice(0, ci), "", ...row.slice(ci)]) })}>前插列</button>
                <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 第 ${ci + 1} 列左移`} disabled={ci === 0} onClick={() => updateBlock(bi, { ...block, rows: block.rows.map((row) => move(row, ci, ci - 1)) })}>左移</button>
                <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 第 ${ci + 1} 列右移`} disabled={ci === block.rows[0].length - 1} onClick={() => updateBlock(bi, { ...block, rows: block.rows.map((row) => move(row, ci, ci + 1)) })}>右移</button>
                <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 删除第 ${ci + 1} 列`} disabled={block.rows[0].length <= 1} onClick={() => updateBlock(bi, { ...block, rows: block.rows.map((row) => row.filter((_, index) => index !== ci)) })}>删列</button>
              </div></th>)}<th scope="col">行操作</th></tr></thead><tbody>{block.rows.map((row, ri) => <tr key={ri}><th scope="row">{ri + 1}</th>{row.map((cell, ci) => <td key={ci}>
              <Input aria-label={`章节 ${si + 1} 表格 ${bi + 1} 第 ${ri + 1} 行第 ${ci + 1} 列`} value={cell} onChange={(e) => updateBlock(bi, { ...block, rows: block.rows.map((r, rix) => rix === ri ? r.map((c, cix) => cix === ci ? e.target.value : c) : r) })} />
            </td>)}<td><div className="flex flex-wrap gap-1">
              <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 在第 ${ri + 1} 行前插入行`} disabled={block.rows.length >= 100} onClick={() => updateBlock(bi, { ...block, rows: [...block.rows.slice(0, ri), block.rows[0].map(() => ""), ...block.rows.slice(ri)] })}>前插行</button>
              <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 第 ${ri + 1} 行上移`} disabled={ri === 0} onClick={() => updateBlock(bi, { ...block, rows: move(block.rows, ri, ri - 1) })}>上移</button>
              <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 第 ${ri + 1} 行下移`} disabled={ri === block.rows.length - 1} onClick={() => updateBlock(bi, { ...block, rows: move(block.rows, ri, ri + 1) })}>下移</button>
              <button type="button" aria-label={`章节 ${si + 1} 表格 ${bi + 1} 删除第 ${ri + 1} 行`} disabled={block.rows.length <= 1} onClick={() => updateBlock(bi, { ...block, rows: block.rows.filter((_, index) => index !== ri) })}>删行</button>
            </div></td></tr>)}</tbody></table></div>
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="secondary" disabled={block.rows.length >= 100} onClick={() => updateBlock(bi, { ...block, rows: [...block.rows, block.rows[0].map(() => "")] })}>添加行</Button>
              <Button type="button" variant="secondary" disabled={block.rows.length <= 1} onClick={() => updateBlock(bi, { ...block, rows: block.rows.slice(0, -1) })}>删除末行</Button>
              <Button type="button" variant="secondary" disabled={block.rows[0].length >= 20} onClick={() => updateBlock(bi, { ...block, rows: block.rows.map((r) => [...r, ""]) })}>添加列</Button>
              <Button type="button" variant="secondary" disabled={block.rows[0].length <= 1} onClick={() => updateBlock(bi, { ...block, rows: block.rows.map((r) => r.slice(0, -1)) })}>删除末列</Button>
            </div>
            <label><input type="checkbox" checked={block.repeat_header ?? false} onChange={(e) => updateBlock(bi, { ...block, repeat_header: e.target.checked })} /> 首行作为表头并在跨页时重复</label>
          </>}
          <div className="flex flex-wrap gap-2">
            <Button type="button" variant="secondary" disabled={bi === 0} onClick={() => update(si, { ...section, blocks: move(section.blocks, bi, bi - 1) })}>内容上移</Button>
            <Button type="button" variant="secondary" disabled={bi === section.blocks.length - 1} onClick={() => update(si, { ...section, blocks: move(section.blocks, bi, bi + 1) })}>内容下移</Button>
            <Button type="button" variant="secondary" onClick={() => {
              const blocks = [...section.blocks.slice(0, bi + 1), copyBlock(block), ...section.blocks.slice(bi + 1)];
              applyCopy(value.map((item, index) => index === si ? { ...section, blocks } : item));
            }}>复制此内容</Button>
            <Button type="button" variant="secondary" disabled={section.blocks.length === 1} onClick={() => update(si, { ...section, blocks: section.blocks.filter((_, i) => i !== bi) })}>删除内容</Button>
            <label>移动到章节 <select aria-label={`移动章节 ${si + 1} 的内容 ${bi + 1} 到章节`} value="" disabled={section.blocks.length <= 1 || value.length <= 1 || value.every((item, i) => i === si || item.blocks.length >= 100)} onChange={(e) => {
              if (e.target.value !== "") moveBlockToSection(si, bi, Number(e.target.value));
            }}>
              <option value="">选择目标章节</option>
              {value.map((target, ti) => ti !== si && <option key={ti} value={ti} disabled={target.blocks.length >= 100}>章节 {ti + 1}{target.heading ? `：${target.heading}` : ""}{target.blocks.length >= 100 ? "（已满）" : ""}</option>)}
            </select></label>
          </div>
        </div>)}
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="secondary" disabled={section.blocks.length >= 100} onClick={() => update(si, { ...section, blocks: [...section.blocks, { kind: "paragraph", text: "" }] })}>添加段落</Button>
          <Button type="button" variant="secondary" disabled={section.blocks.length >= 100} onClick={() => update(si, { ...section, blocks: [...section.blocks, { kind: "table", rows: [[""]] }] })}>添加表格</Button>
        </div>
      </fieldset>;
    })}
    <Button type="button" variant="secondary" disabled={value.length >= 50} onClick={() => onChange([...value, { heading: "", blocks: [{ kind: "paragraph", text: "" }] }])}>添加章节</Button>
    {copyNotice && <p role="status">{copyNotice}</p>}
  </div>;
}
