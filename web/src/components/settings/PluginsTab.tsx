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
import { strings } from "../../ui/strings";
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
      toast.error(strings.settings.plugins.loadFailed, msg);
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
      toast.error(strings.settings.plugins.toggleFailed, msg);
    }
  };

  const handleReload = async () => {
    setReloading(true);
    try {
      const result = await typedIPC.reloadPlugins();
      setPlugins(result.plugins);
      toast.success(
        strings.settings.plugins.reloadedToast,
        strings.settings.plugins.reloadedDetail(result.reloaded, result.failed),
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(strings.settings.plugins.reloadFailed, msg);
    } finally {
      setReloading(false);
    }
  };

  const enabledCount = plugins.filter((p) => p.enabled && p.ok).length;
  const failedCount = plugins.filter((p) => !p.ok).length;

  return (
    <section data-testid="settings-plugins" className="min-w-0 space-y-4">
      <TabHeader
        title={strings.settings.plugins.title}
        hint={strings.settings.plugins.hint(enabledCount, failedCount)}
        action={
          <Button
            size="sm"
            variant="subtle"
            data-testid="settings-plugins-reload"
            onClick={() => void handleReload()}
            disabled={reloading}
            icon={reloading ? <Spinner size={12} /> : <RefreshCw size={12} />}
          >
            {strings.settings.plugins.reload}
          </Button>
        }
      />

      {loading && plugins.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-4 text-xs text-ink-2">
          <Spinner size={12} /> {strings.settings.plugins.loading}
        </div>
      ) : plugins.length === 0 ? (
        <EmptyState title="暂无 Plugin" hint={strings.settings.plugins.emptyHint} />
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
                        {strings.settings.plugins.statusFailed}
                      </span>
                    ) : plugin.enabled ? (
                      <span className="flex items-center gap-0.5 rounded bg-emerald-500/10 px-1.5 py-0 text-[11px] text-emerald-500">
                        <CheckCircle2 size={10} />
                        {strings.settings.plugins.statusEnabled}
                      </span>
                    ) : (
                      <span className="rounded bg-surface-2 px-1.5 py-0 text-[11px] text-ink-2">
                        {strings.settings.plugins.statusDisabled}
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
                        {strings.settings.plugins.hooks}
                      </span>
                    )}
                    {plugin.has_mcp && (
                      <span className="flex items-center gap-0.5">
                        <Server size={10} />
                        {strings.settings.plugins.mcp}
                      </span>
                    )}
                    {plugin.has_permissions && (
                      <span className="flex items-center gap-0.5">
                        <ShieldAlert size={10} />
                        {strings.settings.plugins.permissions}
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
                  {plugin.enabled ? strings.settings.plugins.on : strings.settings.plugins.off}
                </label>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
