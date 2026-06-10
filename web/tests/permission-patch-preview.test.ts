import { describe, expect, it } from "vitest";
import { buildPermissionPatchFiles } from "../src/lib/permissionPatchPreview";

describe("buildPermissionPatchFiles", () => {
  it("builds a compact edit_file preview", () => {
    const files = buildPermissionPatchFiles("edit_file", {
      path: "src/app.ts",
      old_string: "const answer = 1;\n",
      new_string: "const answer = 2;\n",
    });
    expect(files).toHaveLength(1);
    expect(files[0].path).toBe("src/app.ts");
    expect(files[0].status).toBe("modified");
    expect(files[0].additions).toBe(1);
    expect(files[0].deletions).toBe(1);
    expect(files[0].hunks[0].lines.map((line) => line.kind)).toEqual(["delete", "add"]);
  });

  it("builds a write_file preview from content", () => {
    const files = buildPermissionPatchFiles("write_file", {
      path: "README.md",
      content: "# Hello\ncontent\n",
    });
    expect(files).toHaveLength(1);
    expect(files[0].status).toBe("added");
    expect(files[0].additions).toBe(2);
    expect(files[0].deletions).toBe(0);
  });

  it("returns no files for non-mutating tools", () => {
    expect(buildPermissionPatchFiles("read_file", { path: "x" })).toEqual([]);
  });
});
