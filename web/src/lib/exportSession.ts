/**
 * Shared "export session as Markdown" action — used by both the chat
 * header menu and the composer toolbar download button. Fetches the
 * rendered markdown from the agent and triggers a browser download.
 */
import { typedIPC } from "../ipc";
import { toast } from "../components/layout/ErrorBoundary";
import { strings } from "../ui/strings";

export async function exportSessionMarkdown(sessionId: string): Promise<void> {
  try {
    const { markdown } = await typedIPC.sessionExport({ session_id: sessionId });
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
    a.href = url;
    a.download = `minimax-${sessionId.slice(0, 8)}-${ts}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success(strings.chat.menu.exportedToast, strings.chat.menu.exportedDetail);
  } catch (err) {
    toast.error(strings.chat.menu.exportFailed, err instanceof Error ? err.message : String(err));
  }
}
