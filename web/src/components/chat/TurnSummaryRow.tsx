/**
 * Per-turn summary row shown at the top of completed assistant
 * bubbles: "思考 N 次 · 查看 M 个文件 · 修改 K 个文件".
 */
import { Brain, Eye, FileEdit } from "lucide-react";
import type { TurnSummary } from "./turnSummary";

export interface TurnSummaryRowProps {
  messageId: string;
  summary: TurnSummary;
}

export function TurnSummaryRow({ messageId, summary }: TurnSummaryRowProps): JSX.Element {
  return (
    <div
      data-testid={`message-summary-${messageId}`}
      className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-line pb-1.5 text-[11px] text-ink-2"
      aria-label="turn summary"
    >
      <span className="inline-flex items-center gap-1">
        <Brain size={10} />
        思考 {summary.thinkingCount} 次
      </span>
      <span className="inline-flex items-center gap-1">
        <Eye size={10} />
        查看 {summary.filesViewed} 个文件
      </span>
      <span className="inline-flex items-center gap-1">
        <FileEdit size={10} />
        修改 {summary.filesModified} 个文件
      </span>
    </div>
  );
}
