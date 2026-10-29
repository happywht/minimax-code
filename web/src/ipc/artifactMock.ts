/** A virtual demo file only. Mock mode never reads the operator's filesystem. */
import type { TextArtifact } from "../types/ipc";

const rows: TextArtifact[] = [];
export const MOCK_ARTIFACT_TEXT = "Mock text artifact — registered demo, not quality verified.\n";
export const MOCK_ARTIFACT_SERVICE = "svc_mock_artifacts";

export function artifactMock(method: string, params: Record<string, unknown>, projectExists: boolean): unknown {
  const allowed = method === "artifact.register" ? ["project_id", "source_path", "run_id"]
    : method === "artifact.get" ? ["project_id", "artifact_id"] : ["project_id", "run_id", "cursor"];
  if (!params || typeof params !== "object" || Array.isArray(params)
      || Object.keys(params).some((key) => !allowed.includes(key))
      || typeof params.project_id !== "string" || !params.project_id) throw new Error("Invalid artifact parameters");
  if (!projectExists) throw new Error("Select an existing project");
  if (method === "artifact.list" && params.run_id != null &&
      (typeof params.run_id !== "string" || !params.run_id || params.run_id.length > 128)) throw new Error("Invalid artifact parameters");
  const project = String(params.project_id);
  if (method === "artifact.list") {
    const scoped = rows.filter((row) => row.project_id === project &&
      (params.run_id == null || row.run_id === params.run_id)).slice().reverse();
    const index = params.cursor == null ? -1 : scoped.findIndex((row) => row.id === params.cursor);
    if (params.cursor != null && (typeof params.cursor !== "string" || !/^art_[0-9a-f]{32}$/.test(params.cursor) || index < 0)) throw new Error("Invalid artifact cursor");
    const page = scoped.slice(index + 1, index + 201);
    return { service_id: MOCK_ARTIFACT_SERVICE, artifacts: page,
      next_cursor: scoped.length > index + 201 ? page[page.length - 1].id : null };
  }
  if (method === "artifact.get") {
    const row = rows.find((item) => item.id === params.artifact_id && item.project_id === project);
    if (!row) throw new Error("Artifact does not exist in this project");
    return { artifact: row, preview_text: MOCK_ARTIFACT_TEXT };
  }
  if (params.source_path !== "demo.txt" || params.run_id != null) {
    throw new Error("Mock mode provides only the virtual demo.txt file, without run association");
  }
  const artifact: TextArtifact = {
    id: `art_${String(rows.length + 1).padStart(32, "0")}`, project_id: project, run_id: null,
    service_id: MOCK_ARTIFACT_SERVICE, source_path: "demo.txt", snapshot_id: String(rows.length + 1).padStart(32, "0"),
    mime_type: "text/plain", size_bytes: new TextEncoder().encode(MOCK_ARTIFACT_TEXT).length,
    sha256: "d363e21995a3ae5259c0e1aba170b3e6ad48f4cdb7eb09ae7fdb9e7629d48e6c", version: rows.filter((row) => row.project_id === project).length + 1,
    parse_status: "parseable", verification_status: "unverified", created_at: new Date().toISOString(),
  };
  rows.push(artifact);
  return { artifact };
}
