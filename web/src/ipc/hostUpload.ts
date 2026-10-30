import { ipc, typedIPC } from "./index";
import { authHeaders, ensureSession } from "./httpAuth";
import type { HostTransferResult, RemoteHost } from "../types/ipc";

export async function uploadHostFile(host: RemoteHost, path: string, transferId: string, file: File, base: string, signal: AbortSignal, expectedServiceId?: string): Promise<HostTransferResult> {
  const current = () => { if (signal.aborted || ipc.agentBaseUrl !== base) throw new Error("连接变化或上传已取消"); };
  current();
  if (ipc.isMock) throw new Error("模拟模式不支持远端文件上传");
  if (file.size > 10 * 1024 * 1024) throw new Error("文件超过 10 MiB 上限");
  const { service_id } = await typedIPC.getServiceIdentity();
  if (expectedServiceId && service_id !== expectedServiceId) throw new Error("服务身份变化，未开始上传");
  current(); await ensureSession(base); current();
  const metadata = JSON.stringify({ host_id: host.id, hostname: host.hostname, username: host.username,
    port: host.port, identity_file: host.identity_file || "", remote_path: path, service_id, transfer_id: transferId });
  const encoded = btoa(Array.from(new TextEncoder().encode(metadata), byte => String.fromCharCode(byte)).join(""));
  // A write is dispatched once only, including on 401 or a lost response.
  const response = await fetch(`${base}/hosts/upload`, { method: "POST", signal, credentials: "include",
    headers: { ...authHeaders(base), "Content-Type": "application/octet-stream", "X-Host-Upload-Metadata": encoded }, body: file });
  current();
  if (!response.ok) throw new Error(`上传未确认（HTTP ${response.status}）`);
  const result = await response.json() as HostTransferResult;
  current();
  if (result.transfer_id !== transferId || result.remote_path !== path) throw new Error("上传回执不匹配");
  const finalService = await typedIPC.getServiceIdentity();
  current(); if (finalService.service_id !== service_id) throw new Error("服务身份变化");
  return result;
}
