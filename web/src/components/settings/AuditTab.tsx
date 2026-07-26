/**
 * Audit tab — audit log viewer with stats, filter, and pagination.
 */
import { useEffect } from "react";
import { RefreshCw } from "lucide-react";
import { Badge, Button, EmptyState } from "../../ui";
import type { BadgeTone } from "../../ui";
import { SkeletonTable } from "../layout/Skeleton";
import { useAuditStore } from "../../stores";
import type { AuditEntry } from "../../types/ipc";
import { formatDateTime } from "../../lib/time";
import { Select, TabHeader } from "./fields";

export { AuditTab };

const STATUS_TONES: Record<string, BadgeTone> = {
  success: "success",
  fail: "error",
  timeout: "warning",
  denied: "warning",
};

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
      <TabHeader
        title="Audit Log"
        hint="Every tool dispatch is recorded for full traceability. Use this to review what the agent did and when."
        action={
          <Button
            size="sm"
            variant="secondary"
            icon={<RefreshCw />}
            onClick={() => { refresh(); loadStats(); }}
          >
            Refresh
          </Button>
        }
      />

      {/* Stats dashboard */}
      {stats && stats.total > 0 && (
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
            <div className="text-lg font-semibold text-ink-0">{stats.total}</div>
            <div className="text-[11px] text-ink-2">Total Calls</div>
          </div>
          <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
            <div className="text-lg font-semibold text-ink-0">{Object.keys(stats.by_tool).length}</div>
            <div className="text-[11px] text-ink-2">Tools Used</div>
          </div>
          <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
            <div className="text-lg font-semibold text-status-success">
              {stats.by_status.success ?? 0}
            </div>
            <div className="text-[11px] text-ink-2">Successes</div>
          </div>
        </div>
      )}

      {/* Filter */}
      <div className="flex items-center gap-2">
        <label htmlFor="audit-filter-tool" className="shrink-0 text-[11px] text-ink-2">
          Filter by tool:
        </label>
        <Select
          id="audit-filter-tool"
          name="audit-filter-tool"
          data-testid="audit-filter-tool"
          className="w-auto"
          value={filterTool ?? ""}
          onChange={(e) => setFilterTool(e.target.value || null)}
        >
          <option value="">All</option>
          {stats && Object.keys(stats.by_tool).map((t) => (
            <option key={t} value={t}>{t} ({stats.by_tool[t]})</option>
          ))}
        </Select>
      </div>

      {/* Table */}
      {loading ? (
        <SkeletonTable rows={5} />
      ) : entries.length === 0 ? (
        <EmptyState
          title="No audit entries yet."
          hint="Tool calls will appear here once the agent executes tools."
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-left text-[11px]">
            <thead>
              <tr className="border-b border-line bg-surface-1 text-ink-2">
                <th className="px-2 py-1.5 font-medium">Time</th>
                <th className="px-2 py-1.5 font-medium">Tool</th>
                <th className="px-2 py-1.5 font-medium">Status</th>
                <th className="px-2 py-1.5 font-medium">Duration</th>
                <th className="px-2 py-1.5 font-medium">Permission</th>
                <th className="px-2 py-1.5 font-medium">Error</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e: AuditEntry) => (
                <tr key={e.id} className="border-b border-line text-ink-1 last:border-b-0 hover:bg-surface-2">
                  <td className="whitespace-nowrap px-2 py-1.5">{e.created_at ? formatDateTime(e.created_at) : "—"}</td>
                  <td className="px-2 py-1.5 font-mono">{e.tool_name}</td>
                  <td className="px-2 py-1.5">
                    <Badge tone={STATUS_TONES[e.result_status] ?? "neutral"}>{e.result_status}</Badge>
                  </td>
                  <td className="px-2 py-1.5">{e.duration_ms != null ? `${e.duration_ms}ms` : "—"}</td>
                  <td className="px-2 py-1.5">{e.permission ?? "—"}</td>
                  <td className="max-w-[200px] truncate px-2 py-1.5 text-status-error" title={e.error ?? ""}>{e.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 text-xs text-ink-2">
          <Button
            size="sm"
            variant="secondary"
            disabled={page === 0}
            onClick={() => setPage(page - 1)}
          >
            ← Prev
          </Button>
          <span>Page {page + 1} of {totalPages}</span>
          <Button
            size="sm"
            variant="secondary"
            disabled={page + 1 >= totalPages}
            onClick={() => setPage(page + 1)}
          >
            Next →
          </Button>
        </div>
      )}
    </section>
  );
}
