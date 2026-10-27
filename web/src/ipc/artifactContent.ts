import { ipc, typedIPC } from "./index";
import { authHeaders, ensureSession } from "./httpAuth";
import type { TextArtifact } from "../types/ipc";

export async function fetchArtifactContent(artifact: TextArtifact, base: string, signal: AbortSignal): Promise<Blob> {
  const requireCurrent = () => {
    if (ipc.agentBaseUrl !== base || signal.aborted) throw new Error("Artifact endpoint changed or download cancelled");
  };
  requireCurrent();
  if (ipc.isMock) {
    const result = await typedIPC.getArtifact(artifact.project_id, artifact.id);
    if (result.artifact.service_id !== artifact.service_id) throw new Error("Artifact service changed; refresh");
    return new Blob([result.preview_text], { type: "application/octet-stream" });
  }
  await ensureSession(base);
  requireCurrent();
  const url = `${base}/artifacts/${encodeURIComponent(artifact.id)}/content?project_id=${encodeURIComponent(artifact.project_id)}`;
  let response = await fetch(url, { signal, credentials: "include", headers: authHeaders(base) });
  if (response.status === 401) {
    await ensureSession(base, true);
    requireCurrent();
    response = await fetch(url, { signal, credentials: "include", headers: authHeaders(base) });
  }
  if (!response.ok) throw new Error(`Artifact download failed (HTTP ${response.status})`);
  if (response.headers.get("X-Artifact-Service-Id") !== artifact.service_id) throw new Error("Artifact service changed; refresh");
  if (response.headers.get("X-Artifact-SHA256") !== artifact.sha256) throw new Error("Artifact byte identity changed; refresh");
  return response.blob();
}
