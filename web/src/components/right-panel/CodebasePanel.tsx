/**
 * Codebase panel — index status, search, and summaries (v0.11.0 Milestone 2).
 */
import { useEffect, useState } from "react";
import { Search, RefreshCw, FileCode } from "lucide-react";
import { Button, Input, Spinner, EmptyState } from "../../ui";
import { typedIPC } from "../../ipc";
import { toast } from "../layout/ErrorBoundary";
import type {
  CodebaseIndexStatus,
  CodebaseSearchResult,
  CodebaseSummarizeResult,
} from "../../types/ipc";

export interface CodebasePanelProps {
  testId?: string;
}

interface Status {
  status: CodebaseIndexStatus;
  processed: number;
  total: number;
  percent: number;
  message: string;
  error: string | null;
  stats: {
    total_chunks: number;
    total_files: number;
    latest_updated_at: string | null;
  };
}

export function CodebasePanel({ testId = "codebase" }: CodebasePanelProps): JSX.Element {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<CodebaseSearchResult[]>([]);
  const [summaryPath, setSummaryPath] = useState("");
  const [summary, setSummary] = useState<CodebaseSummarizeResult | null>(null);

  const refreshStatus = async () => {
    try {
      const s = await typedIPC.getCodebaseStatus();
      setStatus(s);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load codebase status", message);
    }
  };

  useEffect(() => {
    void refreshStatus();
    const id = setInterval(() => void refreshStatus(), 2000);
    return () => clearInterval(id);
  }, []);

  const handleBuild = async () => {
    setLoading(true);
    try {
      const s = await typedIPC.buildCodebaseIndex({ force: true });
      setStatus(s);
      toast.success("Codebase index started");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to build codebase index", message);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    try {
      const res = await typedIPC.searchCodebase(query.trim());
      setResults(res.results);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Search failed", message);
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  const handleSummarize = async () => {
    if (!summaryPath.trim()) return;
    try {
      const s = await typedIPC.summarizeCodebasePath(summaryPath.trim());
      setSummary(s);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Summarize failed", message);
      setSummary(null);
    }
  };

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
            <span className="font-medium capitalize text-ink-0">{status.status}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink-2">Progress</span>
            <span className="text-ink-0">{status.percent}%</span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-surface-2">
            <div
              className="h-1.5 rounded-full bg-minimax-500 transition-all"
              style={{ width: `${status.percent}%` }}
            />
          </div>
          <div className="text-ink-2">{status.message}</div>
          <div className="flex items-center justify-between text-ink-2">
            <span>Files: {status.stats.total_files}</span>
            <span>Chunks: {status.stats.total_chunks}</span>
          </div>
          {status.status !== "indexing" && (
            <Button
              size="sm"
              variant="primary"
              onClick={() => void handleBuild()}
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
          onKeyDown={(e) => e.key === "Enter" && void handleSearch()}
          data-testid={`${testId}-search`}
        />
        <Button size="sm" variant="primary" onClick={() => void handleSearch()} loading={searching}>
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
            <li
              key={r.chunk_id}
              className="rounded-md border border-line bg-surface-1 p-2 text-xs"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium text-minimax-300">{r.file_path}</span>
                <span className="text-ink-2">
                  {r.language} L{r.start_line}-{r.end_line}
                </span>
              </div>
              <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-1.5 text-ink-1">
                {r.snippet}
              </pre>
            </li>
          ))}
        </ul>
      )}

      <div className="flex gap-2 border-t border-line pt-2">
        <Input
          placeholder="Summarize path (e.g. src/auth.ts)"
          value={summaryPath}
          onChange={(e) => setSummaryPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void handleSummarize()}
          data-testid={`${testId}-summary-path`}
        />
        <Button size="sm" variant="subtle" onClick={() => void handleSummarize()}>
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
