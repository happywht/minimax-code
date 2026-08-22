/**
 * MCP Servers tab — manage external MCP server configurations.
 *
 * v0.11.0: supports stdio and SSE transports, authentication
 * (bearer token / headers / OAuth), per-tool enablement, and
 * in-place editing via the shared `McpServerForm` (add and edit
 * render the same validated fields; only the submit label differs).
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, FlaskConical, Pencil, Plus, Trash2 } from "lucide-react";
import { Button, Checkbox, EmptyState, ErrorBanner, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type {
  InvokeMcpToolResult,
  McpServer,
  McpTool,
} from "../../types/ipc";
import { strings } from "../../ui/strings";
import { Field, TabHeader } from "./fields";
import { McpServerForm, serverToFormValues } from "./McpServerForm";
import type { McpServerFormOptions } from "./McpServerForm";

export { McpServersTab };

function McpServersTab(): JSX.Element {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [toolsByServer, setToolsByServer] = useState<Record<string, McpTool[]>>({});
  const [toolsLoading, setToolsLoading] = useState<Record<string, boolean>>({});

  const refresh = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const result = await typedIPC.listMcpServers();
      setServers(result.servers);
    } catch (err) {
      // Inline banner: a failed load must stay visible so the empty
      // list below is never read as "no servers configured".
      const message = err instanceof Error ? err.message : String(err);
      setLoadError(`${strings.settings.mcp.loadFailed}: ${message}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const handleAdd = async (opts: McpServerFormOptions) => {
    try {
      const id = opts.name.toLowerCase().replace(/\s+/g, "-");
      await typedIPC.addMcpServer({ id, ...opts });
      setShowForm(false);
      await refresh();
      toast.success(strings.settings.mcp.addedToast);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.addFailed, message);
    }
  };

  const handleUpdate = async (server: McpServer, opts: McpServerFormOptions) => {
    try {
      await typedIPC.updateMcpServer(server.id, opts);
      setEditingId(null);
      await refresh();
      toast.success(strings.settings.mcp.updatedToast, server.name);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.updateFailed, message);
    }
  };

  const handleDelete = async (server: McpServer) => {
    const accepted = await requestConfirmation({
      title: strings.settings.mcp.deleteTitle(server.name),
      description: strings.settings.mcp.deleteDesc,
      confirmLabel: strings.settings.mcp.deleteLabel,
    });
    if (!accepted) return;
    try {
      await typedIPC.removeMcpServer(server.id);
      await refresh();
      toast.success(strings.settings.mcp.deletedToast);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.deleteFailed, message);
    }
  };

  const toggleEnabled = async (server: McpServer) => {
    try {
      await typedIPC.updateMcpServer(server.id, { enabled: !server.enabled });
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.updateFailed, message);
    }
  };

  const loadTools = async (server: McpServer) => {
    setToolsLoading((prev) => ({ ...prev, [server.id]: true }));
    try {
      const result = await typedIPC.listMcpTools(server.name);
      setToolsByServer((prev) => ({ ...prev, [server.id]: result.tools }));
    } catch (err) {
      setToolsByServer((prev) => ({ ...prev, [server.id]: [] }));
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.listToolsFailed(server.name), message);
    } finally {
      setToolsLoading((prev) => ({ ...prev, [server.id]: false }));
    }
  };

  const toggleTool = async (server: McpServer, toolName: string, enabled: boolean) => {
    const next = { ...(server.tool_states ?? {}), [toolName]: enabled };
    try {
      await typedIPC.updateMcpServer(server.id, { tool_states: next });
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.toggleToolFailed, message);
    }
  };

  const toggleExpanded = (server: McpServer) => {
    const next = expandedId === server.id ? null : server.id;
    setExpandedId(next);
    if (next && !(server.id in toolsByServer)) {
      void loadTools(server);
    }
  };

  return (
    <section data-testid="settings-mcp-servers" className="min-w-0 space-y-4">
      <TabHeader
        title={strings.settings.mcp.title}
        hint={strings.settings.mcp.hint}
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-mcp-add"
            onClick={() => {
              // Add and edit are mutually exclusive: opening one closes the other.
              setEditingId(null);
              setShowForm((v) => !v);
            }}
            icon={<Plus />}
          >
            {strings.settings.mcp.addServer}
          </Button>
        }
      />

      {showForm && (
        <McpServerForm
          mode="add"
          idPrefix="settings-mcp"
          onSubmit={handleAdd}
          onCancel={() => setShowForm(false)}
        />
      )}

      {loadError && (
        <ErrorBanner
          message={loadError}
          onRetry={() => void refresh()}
          testId="settings-mcp-error"
        />
      )}

      {loading && servers.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.settings.mcp.loading}
        </div>
      ) : servers.length === 0 && !loadError ? (
        <EmptyState title="暂无 MCP Server" hint="点击「添加 Server」连接外部工具。" />
      ) : (
        <ul className="space-y-2" data-testid="settings-mcp-list">
          {servers.map((server) => (
            <li
              key={server.id}
              className="rounded-md border border-line bg-surface-1 p-3"
              data-testid={`settings-mcp-server-${server.id}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink-0">{server.name}</span>
                    <span className="rounded bg-surface-2 px-1.5 py-0 text-[11px] text-ink-2">
                      {server.transport}
                    </span>
                    {server.connected ? (
                      <span className="rounded bg-emerald-500/10 px-1.5 py-0 text-[11px] text-emerald-500">
                        {strings.settings.mcp.connected}
                      </span>
                    ) : (
                      <span className="rounded bg-surface-2 px-1.5 py-0 text-[11px] text-ink-2">
                        {strings.settings.mcp.disconnected}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 truncate font-mono text-[11px] text-ink-2">
                    {server.transport === "stdio"
                      ? server.command?.join(" ") ?? "—"
                      : server.url ?? "—"}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => toggleExpanded(server)}
                    data-testid={`settings-mcp-expand-${server.id}`}
                    icon={expandedId === server.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  >
                    {strings.settings.mcp.tools}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={strings.settings.mcp.editAria}
                    onClick={() => {
                      // Add and edit are mutually exclusive.
                      setShowForm(false);
                      setEditingId((v) => (v === server.id ? null : server.id));
                    }}
                    data-testid={`settings-mcp-edit-${server.id}`}
                  >
                    <Pencil size={14} />
                  </Button>
                  <label className="flex cursor-pointer items-center gap-1.5 px-2 text-[11px] text-ink-2">
                    <Checkbox
                      checked={server.enabled}
                      onChange={() => void toggleEnabled(server)}
                      data-testid={`settings-mcp-enabled-${server.id}`}
                    />
                    {strings.settings.mcp.enabled}
                  </label>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={strings.settings.mcp.deleteAria}
                    onClick={() => void handleDelete(server)}
                    data-testid={`settings-mcp-delete-${server.id}`}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>

              {editingId === server.id && (
                <div className="mt-3 border-t border-line pt-3" data-testid={`settings-mcp-edit-form-${server.id}`}>
                  <McpServerForm
                    mode="edit"
                    idPrefix="settings-mcp-edit"
                    initial={serverToFormValues(server)}
                    onSubmit={(opts) => handleUpdate(server, opts)}
                    onCancel={() => setEditingId(null)}
                  />
                </div>
              )}

              {expandedId === server.id && (
                <div className="mt-3 border-t border-line pt-3">
                  {toolsLoading[server.id] ? (
                    <div className="flex items-center gap-2 text-xs text-ink-2">
                      <Spinner size={12} /> {strings.settings.mcp.loadingTools}
                    </div>
                  ) : (
                    <ToolsList
                      server={server}
                      tools={toolsByServer[server.id] ?? []}
                      onToggle={toggleTool}
                    />
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

interface ToolsListProps {
  server: McpServer;
  tools: McpTool[];
  onToggle: (server: McpServer, toolName: string, enabled: boolean) => void;
}

function ToolsList({ server, tools, onToggle }: ToolsListProps): JSX.Element {
  const [testingTool, setTestingTool] = useState<string | null>(null);

  if (tools.length === 0) {
    return (
      <p className="text-[11px] text-ink-2">{strings.settings.mcp.noTools}</p>
    );
  }
  return (
    <ul className="space-y-1.5" data-testid={`settings-mcp-tools-${server.id}`}>
      {tools.map((tool) => {
        const enabled = server.tool_states?.[tool.name] ?? true;
        const isTesting = testingTool === tool.name;
        return (
          <li
            key={tool.name}
            className="space-y-1.5 rounded bg-surface-0 px-2 py-1.5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[11px] font-medium text-ink-0">{tool.name}</div>
                {tool.description && (
                  <div className="text-[11px] text-ink-2">{tool.description}</div>
                )}
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  size="sm"
                  variant={isTesting ? "subtle" : "ghost"}
                  aria-label={strings.settings.mcp.testAria(tool.name)}
                  icon={<FlaskConical size={12} />}
                  onClick={() => setTestingTool(isTesting ? null : tool.name)}
                  data-testid={`settings-mcp-test-${server.id}-${tool.name}`}
                >
                  {strings.settings.mcp.test}
                </Button>
                <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-ink-2">
                  <Checkbox
                    checked={enabled}
                    onChange={(e) => onToggle(server, tool.name, e.target.checked)}
                    data-testid={`settings-mcp-tool-${server.id}-${tool.name}`}
                  />
                  {enabled ? strings.settings.mcp.on : strings.settings.mcp.off}
                </label>
              </div>
            </div>
            {isTesting && <ToolTester server={server} tool={tool} />}
          </li>
        );
      })}
    </ul>
  );
}

interface ToolTesterProps {
  server: McpServer;
  tool: McpTool;
}

/**
 * Inline MCP tool runner — edit a JSON arguments payload, invoke the tool
 * via `mcp.invoke_tool`, and inspect the raw text / structured content.
 */
function ToolTester({ server, tool }: ToolTesterProps): JSX.Element {
  const [argsText, setArgsText] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<InvokeMcpToolResult | null>(null);

  const schemaKeys = useMemo(() => {
    const props = tool.inputSchema?.properties;
    if (props && typeof props === "object" && !Array.isArray(props)) {
      return Object.keys(props).join(", ");
    }
    return null;
  }, [tool.inputSchema]);

  const run = async () => {
    setError(null);
    setResult(null);
    let args: Record<string, unknown> = {};
    if (argsText.trim()) {
      try {
        const parsed = JSON.parse(argsText) as unknown;
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
          setError(strings.settings.mcp.testArgsInvalid);
          return;
        }
        args = parsed as Record<string, unknown>;
      } catch {
        setError(strings.settings.mcp.testArgsInvalid);
        return;
      }
    }
    setRunning(true);
    try {
      const r = await typedIPC.invokeMcpTool(server.name, tool.name, args);
      setResult(r);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setError(`${strings.settings.mcp.testInvokeFailed}: ${message}`);
    } finally {
      setRunning(false);
    }
  };

  const testerId = `${server.id}-${tool.name}`;
  return (
    <div
      data-testid={`settings-mcp-tester-${testerId}`}
      className="space-y-2 rounded-md border border-line bg-surface-1 p-2"
    >
      <Field
        label={strings.settings.mcp.testArgsLabel}
        htmlFor={`mcp-tester-args-${testerId}`}
      >
        <textarea
          id={`mcp-tester-args-${testerId}`}
          data-testid={`settings-mcp-tester-args-${testerId}`}
          rows={3}
          className="w-full rounded-md border border-line bg-surface-0 px-2 py-1.5 font-mono text-[11px] text-ink-0 focus:border-accent focus:outline-none"
          placeholder={strings.settings.mcp.testArgsPlaceholder}
          value={argsText}
          onChange={(e) => setArgsText(e.target.value)}
        />
      </Field>
      {schemaKeys && (
        <p className="text-[11px] text-ink-2">
          {strings.settings.mcp.testSchemaHint(schemaKeys)}
        </p>
      )}
      <Button
        size="sm"
        variant="primary"
        disabled={running}
        onClick={() => void run()}
        data-testid={`settings-mcp-tester-run-${testerId}`}
      >
        {running ? strings.settings.mcp.testRunning : strings.settings.mcp.testRun}
      </Button>
      {error && (
        <p
          role="alert"
          data-testid={`settings-mcp-tester-error-${testerId}`}
          className="rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1.5 text-[11px] text-red-400"
        >
          {error}
        </p>
      )}
      {result && (
        <div data-testid={`settings-mcp-tester-result-${testerId}`}>
          <p
            className={
              "text-[11px] font-medium " +
              (result.isError ? "text-red-400" : "text-ink-1")
            }
          >
            {result.isError
              ? strings.settings.mcp.testToolError
              : strings.settings.mcp.testResultTitle}
          </p>
          <pre
            className={
              "mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border px-2 py-1.5 font-mono text-[11px] " +
              (result.isError
                ? "border-red-500/30 bg-red-500/10 text-red-400"
                : "border-line bg-surface-0 text-ink-0")
            }
          >
            {result.text ??
              (result.content
                ? JSON.stringify(result.content, null, 2)
                : strings.settings.mcp.testResultEmpty)}
          </pre>
        </div>
      )}
    </div>
  );
}
