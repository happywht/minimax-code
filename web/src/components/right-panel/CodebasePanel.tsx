/**
 * Codebase panel — index status, search, hot files and summaries (v0.11.0 Milestone 2).
 */
import { useEffect, useState } from "react";
import { Search, RefreshCw, FileCode, Clock, TrendingUp } from "lucide-react";
import { Button, Input, Spinner, EmptyState } from "../../ui";
import { useCodebaseStore, startCodebaseStatusPoller } from "../../stores";
import type { CodebaseSearchResult } from "../../types/ipc";
import { strings } from "../../ui/strings";
import { ipc, typedIPC } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import { toast } from "../layout/ErrorBoundary";

export interface CodebasePanelProps {
  testId?: string;
}

function ResultRow({
  r,
  testId,
  onClick,
  onInsert,
  busy,
}: {
  r: CodebaseSearchResult;
  testId: string;
  onClick?: () => void;
  onInsert: () => void;
  busy: boolean;
}): JSX.Element {
  return (
    <li
      key={r.chunk_id}
      className="rounded-md border border-line bg-surface-1 p-2 text-xs"
      data-testid={`${testId}-result`}
    >
      <button
        type="button"
        onClick={onClick}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="font-medium text-minimax-300">{r.file_path}</span>
        <span className="shrink-0 text-ink-2">
          {r.language} {r.match_line != null ? `命中 L${r.match_line} · ` : ""}L{r.start_line}-{r.end_line}
        </span>
      </button>
      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-1.5 text-ink-1">
        {r.snippet}
      </pre>
      <Button size="sm" variant="subtle" disabled={busy} onClick={onInsert}>读取当前源码并加入草稿</Button>
    </li>
  );
}

export function CodebasePanel({ testId = "codebase" }: CodebasePanelProps): JSX.Element {
  const {
    status,
    loading,
    query,
    searching,
    results,
    resultsProjectId,
    resultsHost,
    resultsServiceId,
    summaryPath,
    summary,
    summarizing,
    hotFiles,
    recentFiles,
    refreshStatus,
    buildIndex,
    setQuery,
    search,
    setSummaryPath,
    summarize,
    touchFile,
  } = useCodebaseStore();
  const [inserting, setInserting] = useState(false);

  const insertCurrent = async (r: CodebaseSearchResult) => {
    const { currentSessionId: session, currentProjectId: project, sessions } = useSessionStore.getState();
    const host = ipc.agentBaseUrl;
    if (!session || !project || project !== resultsProjectId || host !== resultsHost ||
        !sessions.some((s) => s.id === session && s.project_id === project && !s.archived)) {
      toast.error("无法加入草稿", "请在搜索所属项目中选择有效会话并重新搜索。"); return;
    }
    let changed = false;
    const valid = () => {
      const state = useSessionStore.getState();
      return !changed && ipc.agentBaseUrl === host && state.currentSessionId === session &&
        state.currentProjectId === project && state.sessions.some((s) => s.id === session && s.project_id === project && !s.archived);
    };
    const off = useSessionStore.subscribe(() => { if (!valid()) changed = true; });
    const stop = ipc.onSideCar(() => { changed = true; });
    setInserting(true);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (!identity.service_id || identity.service_id !== resultsServiceId || !valid()) throw new Error("服务或会话已变化，请重新搜索。");
      const source = await typedIPC.readCurrentCodebase({ session_id: session, project_id: project,
        expected_service_id: identity.service_id, path: r.file_path, start_line: r.start_line, end_line: r.end_line });
      const after = await typedIPC.getServiceIdentity();
      if (!valid() || after.service_id !== identity.service_id) throw new Error("服务或会话已变化，请重新搜索。");
      const detail: { host: string; project: string; session: string; text: string; result?: string } = {
        host, project, session, text: `当前工作区源码 ${source.path} L${source.start_line}-${source.end_line}（读取时快照，可能随后变化）：\n\n\`\`\`\n${source.content}\n\`\`\`` };
      window.dispatchEvent(new CustomEvent("minimax:append-context", { detail }));
      if (!detail.result) throw new Error("当前聊天输入框不可用，请打开会话后重试。");
      if (!detail.result.startsWith("已添加")) throw new Error(detail.result);
      toast.success("源码已加入草稿", "请审阅并手动发送。");
    } catch (err) {
      toast.error("无法加入草稿", err instanceof Error ? err.message : String(err));
    } finally { off(); stop(); setInserting(false); }
  };

  useEffect(() => {
    return startCodebaseStatusPoller();
  }, []);

  const handleSearch = () => void search();
  const handleBuild = () => void buildIndex(true);

  return (
    <section
      id={`${testId}-panel`}
      role="tabpanel"
      data-testid={`${testId}-body`}
      className="flex h-full flex-col gap-3 p-3"
    >
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-ink-0">{strings.rightPanel.codebase.title}</h3>
        <Button
          size="sm"
          variant="subtle"
          onClick={() => void refreshStatus()}
          loading={loading}
          data-testid={`${testId}-refresh`}
        >
          <RefreshCw size={12} className="mr-1" />
          {strings.rightPanel.codebase.refresh}
        </Button>
      </div>

      {status ? (
        <div className="space-y-2 rounded-md border border-line bg-surface-1 p-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-ink-2">{strings.rightPanel.codebase.status}</span>
            <span className="font-medium capitalize text-ink-0" data-testid={`${testId}-status`}>{status.status}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink-2">{strings.rightPanel.codebase.progress}</span>
            <span className="text-ink-0" data-testid={`${testId}-percent`}>{status.percent}%</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-surface-2">
            <div
              className="h-1.5 rounded-full bg-minimax-500 transition-all"
              style={{ width: `${status.percent}%` }}
            />
          </div>
          <div className="text-ink-2">{status.message}</div>
          <div className="flex items-center justify-between text-ink-2">
            <span data-testid={`${testId}-files`}>
              {strings.rightPanel.codebase.files(status.stats.total_files)}
            </span>
            <span data-testid={`${testId}-chunks`}>
              {strings.rightPanel.codebase.chunks(status.stats.total_chunks)}
            </span>
          </div>
          {status.status !== "indexing" && (
            <Button
              size="sm"
              variant="primary"
              onClick={handleBuild}
              loading={loading}
              data-testid={`${testId}-build`}
            >
              {strings.rightPanel.codebase.buildIndex}
            </Button>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.rightPanel.codebase.loadingStatus}
        </div>
      )}

      <div className="flex gap-2">
        <Input
          placeholder={strings.rightPanel.codebase.searchPlaceholder}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          data-testid={`${testId}-search`}
        />
        <Button
          size="sm"
          variant="primary"
          onClick={handleSearch}
          loading={searching}
          aria-label={strings.rightPanel.codebase.searchAria}
          data-testid={`${testId}-search-btn`}
        >
          <Search size={12} />
        </Button>
      </div>

      {searching && results.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.rightPanel.codebase.searching}
        </div>
      ) : results.length === 0 ? (
        <EmptyState
          icon={<FileCode size={20} />}
          title="暂无搜索结果"
          hint={strings.rightPanel.codebase.searchEmptyHint}
        />
      ) : (
        <ul className="flex-1 space-y-2 overflow-auto" data-testid={`${testId}-results`}>
          {results.map((r) => (
            <ResultRow key={r.chunk_id} r={r} testId={testId} onClick={() => touchFile(r.file_path)} onInsert={() => void insertCurrent(r)} busy={inserting} />
          ))}
        </ul>
      )}

      {hotFiles.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-2">
            <TrendingUp size={12} />
            热门文件
          </div>
          <ul className="space-y-1">
            {hotFiles.map((f) => (
              <li
                key={f.file_path}
                className="flex items-center justify-between rounded bg-surface-1 px-2 py-1 text-xs"
              >
                <span className="truncate text-ink-0">{f.file_path}</span>
                <span className="shrink-0 text-ink-2">
                  {strings.rightPanel.codebase.matchCount(f.count)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {recentFiles.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-ink-2">
            <Clock size={12} />
            最近查看
          </div>
          <ul className="space-y-1">
            {recentFiles.map((f) => (
              <li
                key={f}
                className="flex cursor-pointer items-center gap-1.5 rounded bg-surface-1 px-2 py-1 text-xs text-ink-0 hover:bg-surface-2"
                onClick={() => {
                  setSummaryPath(f);
                  void summarize(f);
                }}
              >
                <FileCode size={12} className="text-ink-2" />
                <span className="truncate">{f}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex gap-2 border-t border-line pt-2">
        <Input
          placeholder={strings.rightPanel.codebase.summaryPathPlaceholder}
          value={summaryPath}
          onChange={(e) => setSummaryPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void summarize()}
          data-testid={`${testId}-summary-path`}
        />
        <Button
          size="sm"
          variant="subtle"
          onClick={() => void summarize()}
          loading={summarizing}
          data-testid={`${testId}-summary-btn`}
        >
          {strings.rightPanel.codebase.summary}
        </Button>
      </div>

      {summary && (
        <div className="rounded-md border border-line bg-surface-1 p-3 text-xs">
          <div className="font-medium text-ink-0">{summary.path}</div>
          <div className="text-ink-2">
            {summary.kind} · {strings.rightPanel.codebase.lineCount(summary.total_lines)} ·{" "}
            {strings.rightPanel.codebase.fileCount(summary.file_count)}
          </div>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-1.5 text-ink-1">
            {summary.snippet}
          </pre>
        </div>
      )}
    </section>
  );
}
