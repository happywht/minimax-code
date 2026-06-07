/**
 * Audit tab — audit log viewer with stats, filter, and pagination.
 * Includes StatusBadge sub-component.
 */
import { useEffect } from "react";
import { SkeletonTable } from "../Skeleton";
import { useAuditStore } from "../../stores";
import type { AuditEntry } from "../../types/ipc";

export { AuditTab };

function AuditTab(): JSX.Element {
  const { entries, total, stats, loading, page, pageSize, filterTool, refresh, loadStats, setPage, setFilterTool } = useAuditStore();

  useEffect(() => {
    refresh();
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only fetch
  }, []);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <section data-testid="settings-audit-section" className="space-y-4">
      <h2 className="text-sm font-semibold">Audit Log</h2>
      <p className="text-[11px] text-minimax-muted">
        Every tool dispatch is recorded for full traceability. Use this to review what the agent did and when.
      </p>

      {/* Stats dashboard */}
      {stats && stats.total > 0 && (
        <div className="grid grid-cols-3 gap-3 text-center">
          <div className="rounded border border-minimax-border bg-minimax-panel px-3 py-2">
            <div className="text-lg font-bold">{stats.total}</div>
            <div className="text-[10px] text-minimax-muted">Total Calls</div>
          </div>
          <div className="rounded border border-minimax-border bg-minimax-panel px-3 py-2">
            <div className="text-lg font-bold">{Object.keys(stats.by_tool).length}</div>
            <div className="text-[10px] text-minimax-muted">Tools Used</div>
          </div>
          <div className="rounded border border-minimax-border bg-minimax-panel px-3 py-2">
            <div className="text-lg font-bold text-green-400">
              {stats.by_status.success ?? 0}
            </div>
            <div className="text-[10px] text-minimax-muted">Successes</div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-minimax-muted">Filter by tool:</span>
        <select
          data-testid="audit-filter-tool"
          className="rounded border border-minimax-border bg-minimax-panel px-2 py-1 text-xs"
          value={filterTool ?? ""}
          onChange={(e) => setFilterTool(e.target.value || null)}
        >
          <option value="">All</option>
          {stats && Object.keys(stats.by_tool).map((t) => (
            <option key={t} value={t}>{t} ({stats.by_tool[t]})</option>
          ))}
        </select>
        <button
          type="button"
          className="ml-auto rounded border border-minimax-border px-2 py-1 text-xs hover:bg-minimax-accent/20"
          onClick={() => { refresh(); loadStats(); }}
        >
          Refresh
        </button>
      </div>

      {/* Table */}
      {loading ? (
        <SkeletonTable rows={5} />
      ) : entries.length === 0 ? (
        <div className="py-8 text-center text-xs text-minimax-muted">
          No audit entries yet. Tool calls will appear here once the agent executes tools.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b border-minimax-border text-minimax-muted">
                <th className="px-2 py-1">Time</th>
                <th className="px-2 py-1">Tool</th>
                <th className="px-2 py-1">Status</th>
                <th className="px-2 py-1">Duration</th>
                <th className="px-2 py-1">Permission</th>
                <th className="px-2 py-1">Error</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e: AuditEntry) => (
                <tr key={e.id} className="border-b border-minimax-border/40 hover:bg-minimax-panel">
                  <td className="px-2 py-1 whitespace-nowrap">{e.created_at ? new Date(e.created_at).toLocaleString() : "—"}</td>
                  <td className="px-2 py-1 font-mono">{e.tool_name}</td>
                  <td className="px-2 py-1">
                    <StatusBadge status={e.result_status} />
                  </td>
                  <td className="px-2 py-1">{e.duration_ms != null ? `${e.duration_ms}ms` : "—"}</td>
                  <td className="px-2 py-1">{e.permission ?? "—"}</td>
                  <td className="px-2 py-1 max-w-[200px] truncate text-red-400" title={e.error ?? ""}>{e.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 text-xs text-minimax-muted">
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 disabled:opacity-40"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            ← Prev
          </button>
          <span>Page {page + 1} of {totalPages}</span>
          <button
            type="button"
            className="rounded border border-minimax-border px-2 py-1 disabled:opacity-40"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage(page + 1)}
          >
            Next →
          </button>
        </div>
      )}
    </section>
  );
}

function StatusBadge({ status }: { status: string }): JSX.Element {
  const colors: Record<string, string> = {
    success: "text-green-400",
    fail: "text-red-400",
    timeout: "text-yellow-400",
    denied: "text-orange-400",
  };
  return <span className={colors[status] ?? "text-minimax-muted"}>{status}</span>;
}
