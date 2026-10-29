import { useEffect, useState } from "react";
import { typedIPC } from "../../ipc/client";
import type { ResearchConfigResult } from "../../types/ipc";
import { Button, Input } from "../../ui";

export function ResearchSettings(): JSX.Element {
  const [config, setConfig] = useState<ResearchConfigResult | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    typedIPC.getResearchConfig().then((result) => {
      if (active) { setConfig(result); setDraft(result.saved_url ?? ""); }
    }).catch(() => { if (active) setError("无法读取联网研究设置。"); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, []);

  async function save(value: string | null) {
    setBusy(true);
    setError("");
    try {
      const result = await typedIPC.setResearchConfig(value);
      setConfig(result);
      setDraft(result.saved_url ?? "");
    } catch {
      setError("保存失败。请输入不含凭据或查询参数的 HTTPS SearXNG /search 地址。");
    } finally { setBusy(false); }
  }

  return <section className="space-y-3 border-t border-line pt-4" aria-labelledby="research-settings-title">
    <h3 id="research-settings-title" className="text-xs font-semibold text-ink-1">联网研究</h3>
    <p className="text-xs text-ink-2">配置支持 JSON 的公开 SearXNG 搜索端点。直接读取网页无需配置；联网仍需授权。</p>
    <label htmlFor="research-url" className="block text-xs text-ink-2">搜索服务地址</label>
    <Input id="research-url" value={draft} onChange={(e) => setDraft(e.target.value)} disabled={busy}
      placeholder="https://search.example.org/search" />
    <div className="flex gap-2">
      <Button size="sm" disabled={busy || !draft.trim()} onClick={() => void save(draft.trim())}>保存</Button>
      <Button size="sm" variant="secondary" disabled={busy || !config?.saved_url} onClick={() => void save(null)}>清除已保存地址</Button>
    </div>
    {config && <p role="status" className="break-all text-xs text-ink-2">
      {config.source === "settings" ? "当前使用已保存地址：" : config.source === "environment" ? "当前使用环境变量回退" : config.source === "environment_invalid" ? "环境变量中的搜索地址无效" : "未配置搜索服务"}
      {config.effective_url ?? ""}
    </p>}
    {error && <p role="alert" className="text-xs text-status-error">{error}</p>}
  </section>;
}
