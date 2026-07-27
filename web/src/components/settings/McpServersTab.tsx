/**
 * MCP Servers tab — manage external MCP server configurations.
 *
 * v0.11.0 Milestone 1: stdio transport only; SSE is reserved in the schema.
 */
import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Button, EmptyState, Input, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { toast } from "../layout/ErrorBoundary";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import type { McpServer } from "../../types/ipc";
import { TabHeader } from "./fields";

export { McpServersTab };

function parseCommand(value: string): string[] {
  return value.trim().split(/\s+/).filter(Boolean);
}

function McpServersTab(): JSX.Element {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [env, setEnv] = useState("");

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

  const handleAdd = async () => {
    const argv = parseCommand(command);
    if (!name.trim() || argv.length === 0) {
      toast.error("Invalid input", "Name and command are required.");
      return;
    }
    let envObj: Record<string, string> | undefined;
    if (env.trim()) {
      try {
        envObj = JSON.parse(env) as Record<string, string>;
      } catch {
        toast.error("Invalid env", "Env must be a JSON object.");
        return;
      }
    }
    try {
      const id = name.trim().toLowerCase().replace(/\s+/g, "-");
      await typedIPC.addMcpServer({
        id,
        name: name.trim(),
        transport: "stdio",
        command: argv,
        env: envObj,
        enabled: true,
      });
      setName("");
      setCommand("");
      setEnv("");
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
        <div className="space-y-2 rounded-md border border-line bg-surface-1 p-3">
          <Input
            placeholder="Server name (e.g. filesystem)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            data-testid="settings-mcp-name"
          />
          <Input
            placeholder="Command (e.g. npx @modelcontextprotocol/server-filesystem .)"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            data-testid="settings-mcp-command"
          />
          <Input
            placeholder='Env JSON object (optional) e.g. {"KEY":"value"}'
            value={env}
            onChange={(e) => setEnv(e.target.value)}
            data-testid="settings-mcp-env"
          />
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
              className="flex items-start justify-between gap-3 rounded-md border border-line bg-surface-1 p-3"
            >
              <div className="min-w-0">
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
                  {server.command?.join(" ")}
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                aria-label="Delete"
                onClick={() => void handleDelete(server)}
                data-testid={`settings-mcp-delete-${server.id}`}
              >
                <Trash2 size={14} />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
