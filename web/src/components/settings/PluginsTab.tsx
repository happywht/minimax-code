/**
 * Plugins tab — manage runtime-discovered plugins.
 *
 * Lists every plugin discovered by the agent's plugin registry, shows
 * load status, and lets the user toggle effective enablement or hot-reload
 * the registry from disk.
 */
import { useEffect, useState } from "react";
import { Puzzle, RefreshCw, AlertCircle, CheckCircle2, Link2, Server, ShieldAlert } from "lucide-react";
import { Button, Checkbox, EmptyState, Spinner } from "../../ui";
import { typedIPC } from "../../ipc";
import { toast } from "../layout/ErrorBoundary";
import { TabHeader } from "./fields";
import type { PluginInfo } from "../../types/ipc";

export { PluginsTab };

function PluginsTab(): JSX.Element {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [reloading, setReloading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const result = await typedIPC.listPlugins();
      setPlugins(result.plugins);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to load plugins", msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const toggleEnabled = async (plugin: PluginInfo) => {
    try {
      if (plugin.enabled) {
        await typedIPC.disablePlugin(plugin.name);
      } else {
        await typedIPC.enablePlugin(plugin.name);
      }
      await load();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to toggle plugin", msg);
    }
  };

  const handleReload = async () => {
    setReloading(true);
    try {
      const result = await typedIPC.reloadPlugins();
      setPlugins(result.plugins);
      toast.success(
        "Plugins reloaded",
        `${result.reloaded} loaded, ${result.failed} failed`,
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error("Failed to reload plugins", msg);
    } finally {
      setReloading(false);
    }
  };

  const enabledCount = plugins.filter((p) => p.enabled && p.ok).length;
  const failedCount = plugins.filter((p) => !p.ok).length;

  return (
    <section data-testid="settings-plugins" className="min-w-0 space-y-4">
      <TabHeader
        title="Plugins"
        hint={`${enabledCount} enabled${failedCount > 0 ? `, ${failedCount} failed` : ""}. Runtime toggles are in-memory until manifest persistence lands.`}
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-plugins-reload"
            onClick={() => void handleReload()}
            disabled={reloading}
            icon={reloading ? <Spinner size={12} /> : <RefreshCw size={12} />}
          >
            Reload
          </Button>
        }
      />

      {loading && plugins.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> Loading plugins…
        </div>
      ) : plugins.length === 0 ? (
        <EmptyState title="暂无 Plugin" hint="Plugins are discovered from the configured plugin roots at runtime." />
      ) : (
        <ul className="space-y-2" data-testid="settings-plugins-list">
          {plugins.map((plugin) => (
            <li
              key={plugin.name}
              className="rounded-md border border-line bg-surface-1 p-3"
              data-testid={`settings-plugins-row-${plugin.name}`}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <Puzzle size={14} className="text-ink-2" />
                    <span className="font-medium text-ink-0">{plugin.name}</span>
                    <span className="rounded bg-surface-2 px-1.5 py-0 text-[11px] text-ink-2">
                      {plugin.version || "0.0.0"}
                    </span>
                    {!plugin.ok ? (
                      <span className="flex items-center gap-0.5 rounded bg-status-error/10 px-1.5 py-0 text-[11px] text-status-error">
                        <AlertCircle size={10} />
                        failed
                      </span>
                    ) : plugin.enabled ? (
                      <span className="flex items-center gap-0.5 rounded bg-emerald-500/10 px-1.5 py-0 text-[11px] text-emerald-500">
                        <CheckCircle2 size={10} />
                        enabled
                      </span>
                    ) : (
                      <span className="rounded bg-surface-2 px-1.5 py-0 text-[11px] text-ink-2">
                        disabled
                      </span>
                    )}
                  </div>
                  {plugin.description && (
                    <p className="mt-1 text-[11px] text-ink-2">{plugin.description}</p>
                  )}
                  {!plugin.ok && plugin.error && (
                    <p className="mt-1 text-[11px] text-status-error">{plugin.error}</p>
                  )}
                  <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] text-ink-2">
                    {plugin.has_hooks && (
                      <span className="flex items-center gap-0.5">
                        <Link2 size={10} />
                        hooks
                      </span>
                    )}
                    {plugin.has_mcp && (
                      <span className="flex items-center gap-0.5">
                        <Server size={10} />
                        MCP
                      </span>
                    )}
                    {plugin.has_permissions && (
                      <span className="flex items-center gap-0.5">
                        <ShieldAlert size={10} />
                        permissions
                      </span>
                    )}
                    <span className="truncate font-mono">{plugin.path}</span>
                  </div>
                </div>
                <label className="flex shrink-0 cursor-pointer items-center gap-1.5 px-2 text-[11px] text-ink-2">
                  <Checkbox
                    checked={plugin.enabled}
                    disabled={!plugin.ok}
                    onChange={() => void toggleEnabled(plugin)}
                    data-testid={`settings-plugins-enabled-${plugin.name}`}
                  />
                  {plugin.enabled ? "On" : "Off"}
                </label>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
