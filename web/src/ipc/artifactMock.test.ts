import { describe, expect, it } from "vitest";
import { artifactMock, MOCK_ARTIFACT_TEXT } from "./artifactMock";
import type { TextArtifact } from "../types/ipc";

describe("artifact demo mode", () => {
  it("implements three methods with explicit virtual-file versions", () => {
    const params = { project_id: "demo-test", source_path: "demo.txt" };
    const first = artifactMock("artifact.register", params, true) as { artifact: TextArtifact };
    const second = artifactMock("artifact.register", params, true) as { artifact: TextArtifact };
    expect(first.artifact.verification_status).toBe("unverified");
    expect(second.artifact.version).toBe(first.artifact.version + 1);
    const list = artifactMock("artifact.list", { project_id: params.project_id }, true) as { artifacts: TextArtifact[] };
    expect(list.artifacts).toHaveLength(2);
    expect(artifactMock("artifact.get", { project_id: params.project_id, artifact_id: first.artifact.id }, true)).toEqual({ artifact: first.artifact, preview_text: MOCK_ARTIFACT_TEXT });
    expect(() => artifactMock("artifact.register", { ...params, source_path: "real.txt" }, true)).toThrow("virtual");
    expect(() => artifactMock("artifact.list", { project_id: params.project_id }, false)).toThrow("existing project");
    expect(() => artifactMock("artifact.list", { project_id: "demo-test", host: "other" }, true)).toThrow("Invalid artifact parameters");
  });
});
