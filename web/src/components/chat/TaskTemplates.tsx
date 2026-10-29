/** Editable starting points; choosing one only inserts text into the current draft. */
export const TASK_TEMPLATES = [
  {
    id: "research",
    label: "专题研究",
    text: `请围绕以下主题开展研究：
主题与用途：（请补充）
关注范围与时间：（请补充）

先检查当前项目中相关的已登记附件；需要最新公开信息时，使用联网搜索并读取相关网页原文。区分事实、观点和推断，优先交叉核实关键结论。每项关键事实注明来源标题、链接和访问时间；附件注明名称及对应位置。列出资料不足、冲突和不确定之处，无法获取的来源不要编造。`,
  },
  {
    id: "draft",
    label: "资料成稿",
    text: `请根据资料撰写以下内容：
主题、读者与用途：（请补充）
篇幅、格式和重点：（请补充）

先列出并阅读当前项目中相关的已登记附件，必要时检索并核实公开来源。围绕资料成稿，标注关键事实的来源和未核实之处；缺少信息时请向我说明，不要编造。根据需要生成 DOCX 和 PDF 文件并登记到当前项目附件；说明是否完成事实核验和版式检查，不要把文件生成当作质量验收通过。`,
  },
  {
    id: "table",
    label: "表格处理",
    text: `请处理当前项目中的表格资料：
目标、所需字段与汇总指标：（请补充）
来源附件、工作表及图表要求：（请补充）

先列出并读取相关项目附件，确认 CSV 或 XLSX 的字段和范围；按要求清理、汇总并核对结果，生成 XLSX 表格和适合的柱状图或折线图。标明数据来源、计算口径和不确定之处，保持原始附件不变。若文件或格式超出工具支持范围，明确说明并询问我，不要凭空补数。`,
  },
] as const;

interface TaskTemplatesProps {
  disabled: boolean;
  codex: boolean;
  onSelect: (text: string) => void;
}

export function TaskTemplates({ disabled, codex, onSelect }: TaskTemplatesProps): JSX.Element {
  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-line/60 px-2.5 py-2" aria-label="任务模板">
      <span className="text-[11px] text-ink-2">任务模板 · 添加到输入框</span>
      {TASK_TEMPLATES.map((template) => (
        <button
          key={template.id}
          type="button"
          disabled={disabled}
          onClick={() => onSelect(template.text)}
          className="rounded border border-line px-2 py-1 text-[11px] text-ink-1 hover:border-accent hover:text-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {template.label}
        </button>
      ))}
      {codex && <span className="text-[11px] text-ink-2">当前引擎的附件和办公工具能力可能不同</span>}
    </div>
  );
}
