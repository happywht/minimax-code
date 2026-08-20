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
import { strings } from "../../ui/strings";
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
      return { ok: false, error: strings.settings.mcp.envNotObject };
    }
    if (!Object.entries(parsed as Record<string, unknown>).every(([, v]) => typeof v === "string")) {
      return { ok: false, error: strings.settings.mcp.envValuesNotStrings };
    }
    return { ok: true, value: parsed as Record<string, string> };
  } catch {
    return { ok: false, error: strings.settings.mcp.invalidJson };
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
      toast.error(strings.settings.mcp.loadFailed, message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const formErrors = useMemo(() => {
    const errors: string[] = [];
    if (!name.trim()) errors.push(strings.settings.mcp.nameRequired);
    if (transport === "stdio" && parseCommand(command).length === 0) {
      errors.push(strings.settings.mcp.commandRequired);
    }
    if (transport === "sse" && !url.trim()) {
      errors.push(strings.settings.mcp.urlRequired);
    }
    const envParsed = parseJsonObject(env);
    if (!envParsed.ok) errors.push(strings.settings.mcp.envError(envParsed.error));
    const headersParsed = parseJsonObject(headers);
    if (!headersParsed.ok) errors.push(strings.settings.mcp.headersError(headersParsed.error));
    return errors;
  }, [name, transport, command, url, env, headers]);

  const handleAdd = async () => {
    if (formErrors.length > 0) {
      toast.error(strings.settings.mcp.invalidInput, formErrors.join(" "));
      return;
    }
    const envParsed = parseJsonObject(env);
    const headersParsed = parseJsonObject(headers);
    const parseErrors: string[] = [];
    if (!envParsed.ok) parseErrors.push(envParsed.error);
    if (!headersParsed.ok) parseErrors.push(headersParsed.error);
    if (parseErrors.length > 0) {
      toast.error(strings.settings.mcp.invalidInput, parseErrors.join(" "));
      return;
    }
    if (!envParsed.ok || !headersParsed.ok) return;
    const envObj = envParsed.value;
    const headersObj = headersParsed.value;
    const scopes = parseScopes(oauthScopes);
    const callbackPort = oauthCallbackPort ? parseInt(oauthCallbackPort, 10) : undefined;
    if (oauthCallbackPort && Number.isNaN(callbackPort)) {
      toast.error(strings.settings.mcp.invalidInput, strings.settings.mcp.portInvalid);
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
      toast.success(strings.settings.mcp.addedToast);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.mcp.addFailed, message);
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
            onClick={() => setShowForm((v) => !v)}
            icon={<Plus />}
          >
            {strings.settings.mcp.addServer}
          </Button>
        }
      />

      {showForm && (
        <div className="space-y-3 rounded-md border border-line bg-surface-1 p-3">
          <Field label={strings.settings.mcp.fieldName} htmlFor="settings-mcp-name">
            <Input
              id="settings-mcp-name"
              placeholder={strings.settings.mcp.placeholderName}
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="settings-mcp-name"
            />
          </Field>

          <Field label={strings.settings.mcp.fieldTransport} htmlFor="settings-mcp-transport">
            <Select
              id="settings-mcp-transport"
              value={transport}
              onChange={(e) => setTransport(e.target.value as McpTransport)}
              data-testid="settings-mcp-transport"
            >
              <option value="stdio">{strings.settings.mcp.transportStdio}</option>
              <option value="sse">{strings.settings.mcp.transportSse}</option>
            </Select>
          </Field>

          {transport === "stdio" ? (
            <>
              <Field
                label={strings.settings.mcp.fieldCommand}
                htmlFor="settings-mcp-command"
                hint={strings.settings.mcp.commandHint}
              >
                <Input
                  id="settings-mcp-command"
                  placeholder="npx @modelcontextprotocol/server-filesystem ."
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  data-testid="settings-mcp-command"
                />
              </Field>
              <Field
                label={strings.settings.mcp.fieldEnv}
                htmlFor="settings-mcp-env"
                hint={strings.settings.mcp.envHint}
              >
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
              <Field label={strings.settings.mcp.fieldUrl} htmlFor="settings-mcp-url">
                <Input
                  id="settings-mcp-url"
                  placeholder="http://localhost:3001/sse"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  data-testid="settings-mcp-url"
                />
              </Field>
              <Field label={strings.settings.mcp.fieldToken} htmlFor="settings-mcp-bearer">
                <Input
                  id="settings-mcp-bearer"
                  type="password"
                  placeholder={strings.settings.mcp.tokenHint}
                  value={bearerToken}
                  onChange={(e) => setBearerToken(e.target.value)}
                  data-testid="settings-mcp-bearer"
                />
              </Field>
              <Field
                label={strings.settings.mcp.fieldHeaders}
                htmlFor="settings-mcp-headers"
                hint={strings.settings.mcp.headersHint}
              >
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
            <p className="text-[11px] font-medium text-ink-1">{strings.settings.mcp.oauthTitle}</p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label={strings.settings.mcp.clientId} htmlFor="settings-mcp-oauth-id">
                <Input
                  id="settings-mcp-oauth-id"
                  value={oauthClientId}
                  onChange={(e) => setOauthClientId(e.target.value)}
                  data-testid="settings-mcp-oauth-id"
                />
              </Field>
              <Field label={strings.settings.mcp.clientSecret} htmlFor="settings-mcp-oauth-secret">
                <Input
                  id="settings-mcp-oauth-secret"
                  type="password"
                  value={oauthClientSecret}
                  onChange={(e) => setOauthClientSecret(e.target.value)}
                  data-testid="settings-mcp-oauth-secret"
                />
              </Field>
            </div>
            <Field
              label={strings.settings.mcp.fieldScopes}
              htmlFor="settings-mcp-oauth-scopes"
              hint={strings.settings.mcp.scopesHint}
            >
              <Input
                id="settings-mcp-oauth-scopes"
                placeholder="read,write"
                value={oauthScopes}
                onChange={(e) => setOauthScopes(e.target.value)}
                data-testid="settings-mcp-oauth-scopes"
              />
            </Field>
            <Field label={strings.settings.mcp.callbackPort} htmlFor="settings-mcp-oauth-port">
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
              {strings.settings.mcp.cancel}
            </Button>
            <Button size="sm" variant="primary" onClick={() => void handleAdd()}>
              {strings.settings.mcp.save}
            </Button>
          </div>
        </div>
      )}

      {loading && servers.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.settings.mcp.loading}
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
  if (tools.length === 0) {
    return (
      <p className="text-[11px] text-ink-2">{strings.settings.mcp.noTools}</p>
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
              {enabled ? strings.settings.mcp.on : strings.settings.mcp.off}
            </label>
          </li>
        );
      })}
    </ul>
  );
}
