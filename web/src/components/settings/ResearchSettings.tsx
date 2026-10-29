import { useEffect, useState } from "react";
import { typedIPC } from "../../ipc/client";
import type { ResearchConfigResult } from "../../types/ipc";
import { Button, Input } from "../../ui";

export function ResearchSettings(): JSX.Element {
  const [config, setConfig] = useState<ResearchConfigResult | null>(null);
  const [draft, setDraft] = useState("");
  const [provider, setProvider] = useState<"searxng" | "brave">("searxng");
  const [braveKey, setBraveKey] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    typedIPC.getResearchConfig().then((result) => {
      if (active) { setConfig(result); setDraft(result.saved_url ?? ""); setProvider(result.provider ?? "searxng"); }
    }).catch(() => { if (active) setError("无法读取联网研究设置。"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);

  async function save(value: string | null, clearKey = false) {
    setBusy(true);
    setError("");
    try {
      const result = await typedIPC.setResearchConfig(value, { provider, ...(braveKey ? { brave_key: braveKey } : {}), ...(clearKey ? { clear_brave_key: true } : {}) });
      setConfig(result);
      setDraft(result.saved_url ?? ""); setBraveKey("");
    } catch {
      setError("保存失败。请检查 SearXNG 地址和密钥格式，或确认系统密钥环可用；不会显示密钥。");
    } finally { setBusy(false); }
  }

  return <section className="space-y-3 border-t border-line pt-4" aria-labelledby="research-settings-title">
    <h3 id="research-settings-title" className="text-xs font-semibold text-ink-1">联网研究</h3>
    <p className="text-xs text-ink-2">选择支持 JSON 的 SearXNG，或使用 Brave Search API。直接读取网页无需配置；联网仍需授权。配置成功不代表网络或服务可用。</p>
    <label className="block text-xs text-ink-2">搜索提供方 <select aria-label="搜索提供方" value={provider} disabled={busy} onChange={(event) => setProvider(event.target.value as "searxng" | "brave")}><option value="searxng">SearXNG</option><option value="brave">Brave Search API</option></select></label>
    {provider === "brave" && <><label htmlFor="brave-search-key" className="block text-xs text-ink-2">Brave Search API 密钥（留空保留现有密钥）</label>
      <Input id="brave-search-key" type="password" autoComplete="off" value={braveKey} onChange={(event) => setBraveKey(event.target.value)} disabled={busy} />
      <p className="text-xs text-ink-2">密钥只保存至当前服务主机的系统密钥环，不回显或写入配置文件。搜索会把查询发送给 Brave，可能产生费用；请查看提供方的条款和配额。</p>
      <p className="text-xs text-ink-2">{config?.brave_key_configured ? "密钥已配置；未检查有效性。" : "未检测到密钥。"}</p>
      <Button size="sm" variant="secondary" disabled={busy || !config?.brave_key_configured || !!braveKey} onClick={() => void save(draft.trim() || null, true)}>清除 Brave 密钥</Button></>}
    <label htmlFor="research-url" className="block text-xs text-ink-2">搜索服务地址</label>
    <Input id="research-url" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy}
      placeholder="https://search.example.org/search" />
    <div className="flex gap-2">
      <Button size="sm" disabled={busy || (provider === "searxng" && !draft.trim())} onClick={() => void save(draft.trim() || null)}>保存</Button>
      <Button size="sm" variant="secondary" disabled={busy || !config?.saved_url} onClick={() => void save(null)}>清除已保存地址</Button>
    </div>
    {config && <p role="status" className="break-all text-xs text-ink-2">
      {config.provider === "brave" ? "当前选择 Brave Search；SearXNG 配置保留备用。" : config.source === "settings" ? "当前使用已保存地址：" : config.source === "environment" ? "当前使用环境变量回退" : config.source === "environment_invalid" ? "环境变量中的搜索地址无效" : "未配置搜索服务"}
      {config.provider !== "brave" ? config.effective_url ?? "" : ""}
    </p>}
    {error && <p role="alert" className="text-xs text-status-error">{error}</p>}
  </section>;
}
