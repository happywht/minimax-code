/**
 * DataTab tests (R24) — the Settings Data tab over the `data.*` IPC.
 *
 * Covers the three operation flows end-to-end at the component level:
 * export (blob download path), import (file → JSON → confirm → summary),
 * and backup (path/bytes feedback), plus the two failure paths that
 * matter most: a non-export JSON file and a declined confirmation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const mockExportData = vi.fn();
const mockImportData = vi.fn();
const mockBackupData = vi.fn();
const mockExportDiagnostic = vi.fn();
let confirmNext = true;

vi.mock("../../../ipc", () => ({
  typedIPC: {
    exportData: (...a: unknown[]) => mockExportData(...a),
    importData: (...a: unknown[]) => mockImportData(...a),
    backupData: (...a: unknown[]) => mockBackupData(...a),
    exportDiagnostic: (...a: unknown[]) => mockExportDiagnostic(...a),
  },
}));

vi.mock("../../modals/ConfirmationDialog", () => ({
  requestConfirmation: vi.fn(() => Promise.resolve(confirmNext)),
}));

import { DataTab } from "../DataTab";

const ENVELOPE = {
  format: "minimax-code-export" as const,
  schema_version: 1,
  app_version: "0.14.0",
  exported_at: "2026-08-20T00:00:00Z",
  counts: { sessions: 2, messages: 3 },
  tables: {
    sessions: [{ id: "s1" }, { id: "s2" }],
    messages: [{ id: "m1" }, { id: "m2" }, { id: "m3" }],
  },
};

// jsdom has no blob-URL implementation — stub the two entry points the
// export flow touches, and spy on the synthetic anchor click.
const createObjectURL = vi.fn(() => "blob:mock");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  confirmNext = true;
  URL.createObjectURL = createObjectURL;
  URL.revokeObjectURL = revokeObjectURL;
});

afterEach(() => {
  delete (URL as { createObjectURL?: unknown }).createObjectURL;
  delete (URL as { revokeObjectURL?: unknown }).revokeObjectURL;
});

function pickFile(json: unknown): void {
  const file = new File([JSON.stringify(json)], "export.json", {
    type: "application/json",
  });
  fireEvent.change(screen.getByTestId("settings-data-import-input"), {
    target: { files: [file] },
  });
}

const DIAG_BUNDLE = {
  format: "minimax-code-diagnostic" as const,
  generated_at: "2026-08-21T00:00:00Z",
  version: "0.18.0",
  platform: {
    system: "Windows",
    release: "11",
    machine: "AMD64",
    python: "3.12.9",
    pid: 1234,
  },
  runtime: { uptime_s: 60 },
  config: { log_level: "INFO", http_port: 8765 },
  storage: {
    db_available: true,
    table_count: 2,
    tables: { sessions: 2, messages: 3 },
    migrations_applied: 25,
  },
  log_tail: ["agent started"],
};

describe("DataTab", () => {
  it("renders the four operation panels", () => {
    render(<DataTab />);
    expect(screen.getByTestId("settings-data")).toBeTruthy();
    expect(screen.getByText("导出为 JSON")).toBeTruthy();
    expect(screen.getByText("从 JSON 导入")).toBeTruthy();
    expect(screen.getByText("备份快照")).toBeTruthy();
    expect(screen.getByText("诊断包")).toBeTruthy();
  });

  it("exports: downloads a blob and reports the row total", async () => {
    mockExportData.mockResolvedValue(ENVELOPE);
    render(<DataTab />);
    fireEvent.click(screen.getByTestId("settings-data-export"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-feedback").textContent).toContain(
        "已导出 2 张表共 5 行",
      );
    });
    expect(mockExportData).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
  });

  it("imports: valid envelope → confirm → summary feedback", async () => {
    mockImportData.mockResolvedValue({
      imported: { sessions: 2, messages: 3 },
      skipped_tables: [],
    });
    render(<DataTab />);
    pickFile(ENVELOPE);
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-feedback").textContent).toContain(
        "已导入 2 张表共 5 行",
      );
    });
    expect(mockImportData).toHaveBeenCalledWith(ENVELOPE);
  });

  it("import rejects a file whose format is not an export envelope", async () => {
    render(<DataTab />);
    pickFile({ format: "something-else", tables: {} });
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-error").textContent).toContain(
        "导入失败",
      );
    });
    expect(mockImportData).not.toHaveBeenCalled();
  });

  it("declined confirmation skips the import call", async () => {
    confirmNext = false;
    render(<DataTab />);
    pickFile(ENVELOPE);
    await waitFor(() => {
      expect(mockImportData).not.toHaveBeenCalled();
    });
    // No success/error feedback — the user bailed, nothing happened.
    expect(screen.queryByTestId("settings-data-feedback")).toBeNull();
    expect(screen.queryByTestId("settings-data-error")).toBeNull();
  });

  it("backup: reports the written path and human-readable size", async () => {
    mockBackupData.mockResolvedValue({
      path: "/home/user/.local/share/MiniMaxCode/backups/minimax-code-backup-x.db",
      bytes: 45_056,
    });
    render(<DataTab />);
    fireEvent.click(screen.getByTestId("settings-data-backup"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-feedback").textContent).toContain(
        "minimax-code-backup-x.db",
      );
    });
    expect(screen.getByTestId("settings-data-feedback").textContent).toContain("44.0 KB");
  });

  it("diagnostic: downloads a blob and reports version + table count", async () => {
    mockExportDiagnostic.mockResolvedValue(DIAG_BUNDLE);
    render(<DataTab />);
    fireEvent.click(screen.getByTestId("settings-data-diagnostic"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-feedback").textContent).toContain(
        "v0.18.0，2 张表",
      );
    });
    expect(mockExportDiagnostic).toHaveBeenCalledTimes(1);
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("settings-data-feedback").textContent).toContain(
      "minimax-code-diagnostic-",
    );
  });

  it("diagnostic: NO_DB degradation still downloads (0 tables)", async () => {
    mockExportDiagnostic.mockResolvedValue({
      ...DIAG_BUNDLE,
      storage: { db_available: false },
    });
    render(<DataTab />);
    fireEvent.click(screen.getByTestId("settings-data-diagnostic"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-feedback").textContent).toContain("0 张表");
    });
    expect(createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("diagnostic: failure surfaces the error feedback", async () => {
    mockExportDiagnostic.mockRejectedValue(new Error("agent down"));
    render(<DataTab />);
    fireEvent.click(screen.getByTestId("settings-data-diagnostic"));
    await waitFor(() => {
      expect(screen.getByTestId("settings-data-error").textContent).toContain(
        "诊断包导出失败",
      );
    });
    expect(createObjectURL).not.toHaveBeenCalled();
  });
});
