import { ipc, typedIPC } from "./index";
import { authHeaders, ensureSession } from "./httpAuth";
import type { RemoteHost } from "../types/ipc";

const LIMIT = 10 * 1024 * 1024;
export async function downloadHostFile(host: RemoteHost, path: string, base: string, signal: AbortSignal): Promise<Blob> {
  const current = () => { if (signal.aborted || ipc.agentBaseUrl !== base) throw new Error("连接变化或下载已取消"); };
  current();
  if (ipc.isMock) throw new Error("模拟模式不支持远端文件下载");
  const { service_id } = await typedIPC.getServiceIdentity();
  current(); await ensureSession(base); current();
  const body = JSON.stringify({ host_id: host.id, hostname: host.hostname, username: host.username,
    port: host.port, identity_file: host.identity_file || "", remote_path: path, service_id });
  const dispatch = () => fetch(`${base}/hosts/download`, { method: "POST", signal, credentials: "include",
    headers: { ...authHeaders(base), "Content-Type": "application/json" }, body });
  let response = await dispatch();
  if (response.status === 401) { await ensureSession(base, true); current(); response = await dispatch(); }
  current();
  if (!response.ok) throw new Error(`远端下载失败（HTTP ${response.status}）`);
  if (response.headers.get("X-Host-Download-Service") !== service_id) throw new Error("服务身份变化");
  const expected = response.headers.get("X-Host-Download-SHA256");
  if (!expected || !/^[0-9a-f]{64}$/.test(expected)) throw new Error("缺少有效的文件校验值");
  const reader = response.body?.getReader();
  if (!reader) throw new Error("下载流不可用");
  const parts: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); current();
      if (done) break;
      size += value.byteLength;
      if (size > LIMIT) throw new Error("文件超过 10 MiB 上限");
      parts.push(value);
    }
  } catch (e) { await reader.cancel().catch(() => {}); throw e; }
  finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size); let offset = 0;
  for (const part of parts) { bytes.set(part, offset); offset += part.byteLength; }
  const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), b => b.toString(16).padStart(2, "0")).join("");
  current(); if (hash !== expected) throw new Error("下载字节校验失败");
  const finalService = await typedIPC.getServiceIdentity();
  current(); if (finalService.service_id !== service_id) throw new Error("服务身份变化");
  return new Blob([bytes.buffer], { type: "application/octet-stream" });
}
