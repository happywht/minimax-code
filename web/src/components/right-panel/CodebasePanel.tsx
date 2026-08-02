/**
 * Codebase panel — index status, search, hot files and summaries (v0.11.0 Milestone 2).
 */
import { useEffect } from "react";
import { Search, RefreshCw, FileCode, Clock, TrendingUp } from "lucide-react";
import { Button, Input, Spinner, EmptyState } from "../../ui";
import { useCodebaseStore, startCodebaseStatusPoller } from "../../stores";
import type { CodebaseSearchResult } from "../../types/ipc";

export interface CodebasePanelProps {
  testId?: string;
}

function ResultRow({
  r,
  testId,
  onClick,
}: {
  r: CodebaseSearchResult;
  testId: string;
  onClick?: () => void;
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
          {r.language} L{r.start_line}-{r.end_line}
        </span>
      </button>
      <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-1.5 text-ink-1">
        {r.snippet}
      </pre>
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
        <h3 className="text-sm font-medium text-ink-0">Codebase</h3>
        <Button
          size="sm"
          variant="subtle"
          onClick={() => void refreshStatus()}
          loading={loading}
          data-testid={`${testId}-refresh`}
        >
          <RefreshCw size={12} className="mr-1" />
          Refresh
        </Button>
      </div>

      {status ? (
        <div className="space-y-2 rounded-md border border-line bg-surface-1 p-3 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-ink-2">Status</span>
            <span className="font-medium capitalize text-ink-0" data-testid={`${testId}-status`}>{status.status}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink-2">Progress</span>
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
            <span data-testid={`${testId}-files`}>Files: {status.stats.total_files}</span>
            <span data-testid={`${testId}-chunks`}>Chunks: {status.stats.total_chunks}</span>
          </div>
          {status.status !== "indexing" && (
            <Button
              size="sm"
              variant="primary"
              onClick={handleBuild}
              loading={loading}
              data-testid={`${testId}-build`}
            >
              Build Index
            </Button>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Loading status…
        </div>
      )}

      <div className="flex gap-2">
        <Input
          placeholder="Search code (e.g. auth flow)"
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
          aria-label="Search"
          data-testid={`${testId}-search-btn`}
        >
          <Search size={12} />
        </Button>
      </div>

      {searching && results.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Searching…
        </div>
      ) : results.length === 0 ? (
        <EmptyState
          icon={<FileCode size={20} />}
          title="暂无搜索结果"
          hint="输入关键词并点击搜索，或先 Build Index。"
        />
      ) : (
        <ul className="flex-1 space-y-2 overflow-auto" data-testid={`${testId}-results`}>
          {results.map((r) => (
            <ResultRow key={r.chunk_id} r={r} testId={testId} onClick={() => touchFile(r.file_path)} />
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
                <span className="shrink-0 text-ink-2">{f.count} matches</span>
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
          placeholder="Summarize path (e.g. src/auth.ts)"
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
          Summary
        </Button>
      </div>

      {summary && (
        <div className="rounded-md border border-line bg-surface-1 p-3 text-xs">
          <div className="font-medium text-ink-0">{summary.path}</div>
          <div className="text-ink-2">
            {summary.kind} · {summary.total_lines} lines · {summary.file_count} file(s)
          </div>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-1.5 text-ink-1">
            {summary.snippet}
          </pre>
        </div>
      )}
    </section>
  );
}
