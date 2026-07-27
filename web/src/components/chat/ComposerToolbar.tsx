/**
 * ComposerToolbar — the bottom strip of the composer: the always-allow
 * permission switch, the "stopping" hint, the context indicator, the
 * character counter + usage meter, and the inline model picker.
 */
import { Download, Shield, ShieldCheck } from "lucide-react";
import { Button, IconButton } from "../../ui";
import { usePermissionStore, useSessionStore } from "../../stores";
import { typedIPC } from "../../ipc";
import { toast } from "../layout/ErrorBoundary";
import { ContextIndicator } from "./ContextIndicator";
import { ModelSelector } from "./ModelSelector";
import { MAX_INPUT_CHARS } from "./constants";

export interface ComposerToolbarProps {
  valueLength: number;
  overLimit: boolean;
  usagePct: number;
  nearLimit: boolean;
  critical: boolean;
  cancelling: boolean;
}

export function ComposerToolbar({
  valueLength,
  overLimit,
  usagePct,
  nearLimit,
  critical,
  cancelling,
}: ComposerToolbarProps): JSX.Element {
  const alwaysAllow = usePermissionStore((s) => s.alwaysAllow);
  const setAlwaysAllow = usePermissionStore((s) => s.setAlwaysAllow);
  const currentSessionId = useSessionStore((s) => s.currentSessionId);

  const tokenToneClass = overLimit
    ? "text-status-error font-semibold"
    : critical
      ? "text-status-error"
      : nearLimit
        ? "text-status-warning"
        : "text-ink-2";
  const meterToneClass = overLimit
    ? "bg-status-error"
    : critical
      ? "bg-status-error"
      : nearLimit
        ? "bg-status-warning"
        : "bg-accent";

  const handleExportSession = async () => {
    if (!currentSessionId) {
      toast.error("无法导出", "当前没有选中的会话");
      return;
    }
    try {
      const { markdown } = await typedIPC.sessionExport({ session_id: currentSessionId });
      const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
      a.href = url;
      a.download = `minimax-${currentSessionId.slice(0, 8)}-${ts}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success("会话已导出", "Markdown 文件开始下载");
    } catch (err) {
      toast.error("导出失败", err instanceof Error ? err.message : String(err));
    }
  };

  const handleToggleAlwaysAllow = () => {
    const next = !alwaysAllow;
    setAlwaysAllow(next);
    toast.info(
      next ? "始终授权已开启" : "始终授权已关闭",
      next
        ? "所有 tool 调用将自动放行（仅当前会话）"
        : "tool 调用将再次弹窗询问",
    );
  };

  return (
    <div className="flex flex-wrap sm:flex-nowrap items-center gap-x-2 gap-y-1 sm:gap-y-0 border-t border-line/60 px-2.5 py-1.5">
      <Button
        variant="ghost"
        size="sm"
        role="switch"
        aria-checked={alwaysAllow}
        data-testid="chat-input-always-allow"
        onClick={handleToggleAlwaysAllow}
        icon={alwaysAllow ? <ShieldCheck size={11} /> : <Shield size={11} />}
        className={
          "px-1.5 text-[11px] [&>span>svg]:h-[11px] [&>span>svg]:w-[11px] " +
          (alwaysAllow
            ? "text-status-success hover:bg-[var(--status-success-subtle)] hover:text-status-success"
            : "")
        }
        title={
          alwaysAllow
            ? "始终授权已开启 — tool 调用将自动放行"
            : "始终授权：下次 tool 调用前不再询问"
        }
      >
        {alwaysAllow ? "始终授权：开" : "始终授权"}
      </Button>
      <IconButton
        size="sm"
        aria-label="Export current session as Markdown"
        title="导出当前会话"
        data-testid="chat-input-export-session"
        onClick={() => void handleExportSession()}
        className="text-[11px] text-ink-2 hover:text-ink-0"
      >
        <Download size={12} />
      </IconButton>
      <div className="flex min-w-0 flex-1 flex-wrap sm:flex-nowrap items-center justify-end gap-x-2 gap-y-1 sm:gap-y-0">
        {cancelling && (
          <span
            data-testid="message-input-stopping"
            className="text-[11px] text-status-warning transition-opacity duration-200"
          >
            正在停止...
          </span>
        )}
        <ContextIndicator />
        <div className="flex min-w-[72px] flex-col items-end gap-1 sm:min-w-[92px]">
          <span
            data-testid="message-input-token-count"
            aria-label={`输入字符 ${valueLength}/${MAX_INPUT_CHARS}`}
            className={`text-[11px] transition-colors duration-200 ${tokenToneClass}`}
          >
            {valueLength}/{MAX_INPUT_CHARS}
          </span>
          <div
            data-testid="message-input-token-meter"
            className="h-0.5 w-full overflow-hidden rounded-full bg-surface-3"
            aria-hidden
          >
            <div
              className={`h-full rounded-full transition-all duration-200 ${meterToneClass}`}
              style={{ width: `${usagePct}%` }}
            />
          </div>
        </div>
        {nearLimit && (
          <span
            data-testid="message-input-token-warning"
            className={`hidden min-w-[74px] text-right text-[11px] sm:inline ${
              critical ? "text-status-error" : "text-status-warning"
            }`}
            aria-live="polite"
          >
            {overLimit ? "已超限" : critical ? "即将超限" : "接近上限"}
          </span>
        )}
        <ModelSelector variant="inline" />
      </div>
    </div>
  );
}
