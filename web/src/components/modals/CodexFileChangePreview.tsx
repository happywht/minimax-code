/** The App Server supplies file diffs separately from the approval request. */
export function CodexFileChangePreview({ tool, args }: {
  tool: string;
  args: Record<string, unknown>;
}): JSX.Element | null {
  if (tool !== "codex:item/fileChange/requestApproval") return null;
  const raw = args.file_change_preview;
  const preview = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const changes = Array.isArray(preview.changes) ? preview.changes : [];
  return <div className="mt-2 rounded border border-line bg-surface-0/60 p-2 text-xs" data-testid="codex-file-change-preview">
    <p className="font-medium text-ink-0">Codex 请求修改的文件</p>
    {typeof args.grantRoot === "string" && <p className="mt-1 break-all text-status-warning">请求额外写入范围：{args.grantRoot}。该请求可能涉及本会话后续写入。</p>}
    {changes.length === 0 && <p className="mt-1 text-status-warning">未收到文件变更详情，请先核对工作区再决定是否授权。</p>}
    <div className="max-h-64 space-y-2 overflow-auto">
      {changes.map((value: unknown, index: number) => {
        if (!value || typeof value !== "object") return null;
        const change = value as Record<string, unknown>;
        if (typeof change.path !== "string" || typeof change.diff !== "string") return null;
        return <div key={index} className="mt-1">
          <p className="break-all font-mono text-ink-1">{change.path}</p>
          <pre className="whitespace-pre-wrap break-all font-mono text-ink-0">{change.diff || "（无差异内容）"}</pre>
        </div>;
      })}
    </div>
    {preview.truncated === true && <p className="mt-1 text-status-warning">预览已截断，未显示全部变更。</p>}
  </div>;
}

export function permissionArgsWithoutCodexPreview(args: Record<string, unknown>): Record<string, unknown> {
  const rest = { ...args };
  delete rest.file_change_preview;
  return rest;
}
