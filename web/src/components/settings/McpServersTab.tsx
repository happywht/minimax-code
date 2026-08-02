/**
 * MCP Servers tab — manage external MCP server configurations.
 *
 * v0.11.0: supports stdio and SSE transports, authentication
 * (bearer token / headers / OAuth), and per-tool enablement.
 */
import { useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Plus, Trash2 } from "lucide-react";
import { Button, Checkbox, EmptyState, Input, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type { McpServer, McpTool, McpTransport } from "../../types/ipc";
import { Field, Select, TabHeader } from "./fields";

export { McpServersTab };

function parseCommand(value: string): string[] {
  return value.trim().split(/\s+/).filter(Boolean);
}

function parseJsonObject(
  value: string,
): { ok: true; value: Record<string, string> } | { ok: false; error: string } {
  if (!value.trim()) return { ok: true, value: {} };
  try {
    const parsed = JSON.parse(value) as unknown;
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, error: "Must be a JSON object." };
    }
    if (!Object.entries(parsed as Record<string, unknown>).every(([, v]) => typeof v === "string")) {
      return { ok: false, error: "All values must be strings." };
    }
    return { ok: true, value: parsed as Record<string, string> };
  } catch {
    return { ok: false, error: "Invalid JSON." };
  }
}

function parseScopes(value: string): string[] {
  if (!value.trim()) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) {
      return parsed;
    }
  } catch {
    // fall through to comma-separated
  }
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function McpServersTab(): JSX.Element {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);

  const [name, setName] = useState("");
  const [transport, setTransport] = useState<McpTransport>("stdio");
  const [command, setCommand] = useState("");
  const [env, setEnv] = useState("");
  const [url, setUrl] = useState("");
  const [bearerToken, setBearerToken] = useState("");
  const [headers, setHeaders] = useState("");
  const [oauthClientId, setOauthClientId] = useState("");
  const [oauthClientSecret, setOauthClientSecret] = useState("");
  const [oauthScopes, setOauthScopes] = useState("");
  const [oauthCallbackPort, setOauthCallbackPort] = useState("");

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [toolsByServer, setToolsByServer] = useState<Record<string, McpTool[]>>({});
  const [toolsLoading, setToolsLoading] = useState<Record<string, boolean>>({});

  const resetForm = () => {
    setName("");
    setTransport("stdio");
    setCommand("");
    setEnv("");
    setUrl("");
    setBearerToken("");
    setHeaders("");
    setOauthClientId("");
    setOauthClientSecret("");
    setOauthScopes("");
    setOauthCallbackPort("");
  };

  const refresh = async () => {
    setLoading(true);
    try {
      const result = await typedIPC.listMcpServers();
      setServers(result.servers);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load MCP servers", message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const formErrors = useMemo(() => {
    const errors: string[] = [];
    if (!name.trim()) errors.push("Name is required.");
    if (transport === "stdio" && parseCommand(command).length === 0) {
      errors.push("Command is required for stdio transport.");
    }
    if (transport === "sse" && !url.trim()) {
      errors.push("URL is required for SSE transport.");
    }
    const envParsed = parseJsonObject(env);
    if (!envParsed.ok) errors.push(`Env: ${envParsed.error}`);
    const headersParsed = parseJsonObject(headers);
    if (!headersParsed.ok) errors.push(`Headers: ${headersParsed.error}`);
    return errors;
  }, [name, transport, command, url, env, headers]);

  const handleAdd = async () => {
    if (formErrors.length > 0) {
      toast.error("Invalid input", formErrors.join(" "));
      return;
    }
    const envParsed = parseJsonObject(env);
    const headersParsed = parseJsonObject(headers);
    const parseErrors: string[] = [];
    if (!envParsed.ok) parseErrors.push(envParsed.error);
    if (!headersParsed.ok) parseErrors.push(headersParsed.error);
    if (parseErrors.length > 0) {
      toast.error("Invalid input", parseErrors.join(" "));
      return;
    }
    if (!envParsed.ok || !headersParsed.ok) return;
    const envObj = envParsed.value;
    const headersObj = headersParsed.value;
    const scopes = parseScopes(oauthScopes);
    const callbackPort = oauthCallbackPort ? parseInt(oauthCallbackPort, 10) : undefined;
    if (oauthCallbackPort && Number.isNaN(callbackPort)) {
      toast.error("Invalid input", "OAuth callback port must be a number.");
      return;
    }
    try {
      const id = name.trim().toLowerCase().replace(/\s+/g, "-");
      await typedIPC.addMcpServer({
        id,
        name: name.trim(),
        transport,
        command: transport === "stdio" ? parseCommand(command) : undefined,
        url: transport === "sse" ? url.trim() : undefined,
        env: Object.keys(envObj).length > 0 ? envObj : undefined,
        bearer_token: bearerToken.trim() || undefined,
        headers: Object.keys(headersObj).length > 0 ? headersObj : undefined,
        oauth_client_id: oauthClientId.trim() || undefined,
        oauth_client_secret: oauthClientSecret.trim() || undefined,
        oauth_scopes: scopes.length > 0 ? scopes : undefined,
        oauth_callback_port: callbackPort,
      });
      resetForm();
      setShowForm(false);
      await refresh();
      toast.success("MCP server added");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to add MCP server", message);
    }
  };

  const handleDelete = async (server: McpServer) => {
    const accepted = await requestConfirmation({
      title: `Delete ${server.name}?`,
      description: "This removes the persisted MCP server configuration.",
      confirmLabel: "Delete",
    });
    if (!accepted) return;
    try {
      await typedIPC.removeMcpServer(server.id);
      await refresh();
      toast.success("MCP server deleted");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to delete MCP server", message);
    }
  };

  const toggleEnabled = async (server: McpServer) => {
    try {
      await typedIPC.updateMcpServer(server.id, { enabled: !server.enabled });
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Failed to update MCP server", message);
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
      toast.error(`Failed to list tools for ${server.name}`, message);
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
      toast.error("Failed to update tool state", message);
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
        title="MCP Servers"
        hint="Connect external MCP tool servers. Bridged tools appear in the agent's tool registry."
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-mcp-add"
            onClick={() => setShowForm((v) => !v)}
            icon={<Plus />}
          >
            Add Server
          </Button>
        }
      />

      {showForm && (
        <div className="space-y-3 rounded-md border border-line bg-surface-1 p-3">
          <Field label="Name" htmlFor="settings-mcp-name">
            <Input
              id="settings-mcp-name"
              placeholder="Server name (e.g. filesystem)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="settings-mcp-name"
            />
          </Field>

          <Field label="Transport" htmlFor="settings-mcp-transport">
            <Select
              id="settings-mcp-transport"
              value={transport}
              onChange={(e) => setTransport(e.target.value as McpTransport)}
              data-testid="settings-mcp-transport"
            >
              <option value="stdio">stdio (local subprocess)</option>
              <option value="sse">SSE (HTTP stream)</option>
            </Select>
          </Field>

          {transport === "stdio" ? (
            <>
              <Field label="Command" htmlFor="settings-mcp-command" hint="Space-separated argv.">
                <Input
                  id="settings-mcp-command"
                  placeholder="npx @modelcontextprotocol/server-filesystem ."
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  data-testid="settings-mcp-command"
                />
              </Field>
              <Field label="Environment variables" htmlFor="settings-mcp-env" hint="JSON object.">
                <Input
                  id="settings-mcp-env"
                  placeholder='{"KEY":"value"}'
                  value={env}
                  onChange={(e) => setEnv(e.target.value)}
                  data-testid="settings-mcp-env"
                />
              </Field>
            </>
          ) : (
            <>
              <Field label="SSE URL" htmlFor="settings-mcp-url">
                <Input
                  id="settings-mcp-url"
                  placeholder="http://localhost:3001/sse"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  data-testid="settings-mcp-url"
                />
              </Field>
              <Field label="Bearer token" htmlFor="settings-mcp-bearer">
                <Input
                  id="settings-mcp-bearer"
                  type="password"
                  placeholder="Optional bearer token"
                  value={bearerToken}
                  onChange={(e) => setBearerToken(e.target.value)}
                  data-testid="settings-mcp-bearer"
                />
              </Field>
              <Field label="Headers" htmlFor="settings-mcp-headers" hint="JSON object.">
                <Input
                  id="settings-mcp-headers"
                  placeholder='{"X-Custom":"value"}'
                  value={headers}
                  onChange={(e) => setHeaders(e.target.value)}
                  data-testid="settings-mcp-headers"
                />
              </Field>
            </>
          )}

          <div className="space-y-3 rounded-md border border-line bg-surface-0 p-3">
            <p className="text-[11px] font-medium text-ink-1">OAuth (optional)</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Client ID" htmlFor="settings-mcp-oauth-id">
                <Input
                  id="settings-mcp-oauth-id"
                  value={oauthClientId}
                  onChange={(e) => setOauthClientId(e.target.value)}
                  data-testid="settings-mcp-oauth-id"
                />
              </Field>
              <Field label="Client secret" htmlFor="settings-mcp-oauth-secret">
                <Input
                  id="settings-mcp-oauth-secret"
                  type="password"
                  value={oauthClientSecret}
                  onChange={(e) => setOauthClientSecret(e.target.value)}
                  data-testid="settings-mcp-oauth-secret"
                />
              </Field>
            </div>
            <Field label="Scopes" htmlFor="settings-mcp-oauth-scopes" hint="JSON list or comma-separated.">
              <Input
                id="settings-mcp-oauth-scopes"
                placeholder="read,write"
                value={oauthScopes}
                onChange={(e) => setOauthScopes(e.target.value)}
                data-testid="settings-mcp-oauth-scopes"
              />
            </Field>
            <Field label="Callback port" htmlFor="settings-mcp-oauth-port">
              <Input
                id="settings-mcp-oauth-port"
                type="number"
                placeholder="8765"
                value={oauthCallbackPort}
                onChange={(e) => setOauthCallbackPort(e.target.value)}
                data-testid="settings-mcp-oauth-port"
              />
            </Field>
          </div>

          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setShowForm(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={() => void handleAdd()}>
              Save
            </Button>
          </div>
        </div>
      )}

      {loading && servers.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Loading MCP servers…
        </div>
      ) : servers.length === 0 ? (
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
                        connected
                      </span>
                    ) : (
                      <span className="rounded bg-surface-2 px-1.5 py-0 text-[11px] text-ink-2">
                        disconnected
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
                    Tools
                  </Button>
                  <label className="flex cursor-pointer items-center gap-1.5 px-2 text-[11px] text-ink-2">
                    <Checkbox
                      checked={server.enabled}
                      onChange={() => void toggleEnabled(server)}
                      data-testid={`settings-mcp-enabled-${server.id}`}
                    />
                    Enabled
                  </label>
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label="Delete"
                    onClick={() => void handleDelete(server)}
                    data-testid={`settings-mcp-delete-${server.id}`}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>

              {expandedId === server.id && (
                <div className="mt-3 border-t border-line pt-3">
                  {toolsLoading[server.id] ? (
                    <div className="flex items-center gap-2 text-xs text-ink-2">
                      <Spinner size={12} /> Loading tools…
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
  if (tools.length === 0) {
    return (
      <p className="text-[11px] text-ink-2">
        No tools available. The server may be disconnected.
      </p>
    );
  }
  return (
    <ul className="space-y-1.5" data-testid={`settings-mcp-tools-${server.id}`}>
      {tools.map((tool) => {
        const enabled = server.tool_states?.[tool.name] ?? true;
        return (
          <li
            key={tool.name}
            className="flex items-start justify-between gap-3 rounded bg-surface-0 px-2 py-1.5"
          >
            <div className="min-w-0">
              <div className="text-[11px] font-medium text-ink-0">{tool.name}</div>
              {tool.description && (
                <div className="text-[11px] text-ink-2">{tool.description}</div>
              )}
            </div>
            <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[11px] text-ink-2">
              <Checkbox
                checked={enabled}
                onChange={(e) => onToggle(server, tool.name, e.target.checked)}
                data-testid={`settings-mcp-tool-${server.id}-${tool.name}`}
              />
              {enabled ? "On" : "Off"}
            </label>
          </li>
        );
      })}
    </ul>
  );
}
