/**
 * Data tab — data portability UI over the `data.*` IPC namespace.
 *
 * Three operations, mirroring the backend capabilities (R21–R23):
 *  - Export: dump every business table into a JSON envelope, then
 *    download it as a file via a Blob URL.
 *  - Import: pick an exported JSON file, confirm the replace semantics,
 *    then restore it in a single backend transaction.
 *  - Backup: take a file-level SQLite snapshot (schema + WAL + derived
 *    indexes included) into the agent's backups directory.
 */
import { useRef, useState } from "react";
import { Activity, DatabaseBackup, Download, HardDriveDownload, Upload } from "lucide-react";
import { Button, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import { typedIPC } from "../../ipc";
import type {
  DataBackupResult,
  DataExportEnvelope,
  DataImportSummary,
  DiagnosticBundle,
} from "../../types/ipc";
import { InlineCode, TabHeader } from "./fields";
import { requestConfirmation } from "../modals/ConfirmationDialog";

export { DataTab };

type Feedback =
  | { kind: "ok"; text: string }
  | { kind: "error"; text: string };

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** `20260820-143005`-style stamp for the default export filename. */
function exportFilename(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `minimax-code-export-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(
    now.getDate(),
  )}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}.json`;
}

/** Same stamp style, but for the diagnostic bundle (R46). */
function diagFilename(): string {
  return exportFilename().replace("minimax-code-export-", "minimax-code-diagnostic-");
}

/** Shared Blob-URL download for JSON payloads (export + diagnostics). */
function downloadJson(payload: unknown, filename: string): void {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Read a picked file as text — FileReader for the widest compat. */
function readFileText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("failed to read file"));
    reader.readAsText(file);
  });
}

function DataTab(): JSX.Element {
  const [exporting, setExporting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [backing, setBacking] = useState(false);
  const [diagnosing, setDiagnosing] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const onExport = async () => {
    setExporting(true);
    setFeedback(null);
    try {
      const envelope = await typedIPC.exportData();
      downloadJson(envelope, exportFilename());
      const totalRows = Object.values(envelope.counts ?? {}).reduce<number>((a, b) => a + b, 0);
      setFeedback({
        kind: "ok",
        text: strings.settings.data.exportOk(
          totalRows,
          Object.keys(envelope.tables ?? {}).length,
          exportFilename(),
        ),
      });
    } catch (err) {
      setFeedback({ kind: "error", text: strings.settings.data.exportFail(String(err)) });
    } finally {
      setExporting(false);
    }
  };

  const onDiagnostic = async () => {
    setDiagnosing(true);
    setFeedback(null);
    try {
      const bundle: DiagnosticBundle = await typedIPC.exportDiagnostic();
      downloadJson(bundle, diagFilename());
      const tableCount = bundle.storage.db_available ? bundle.storage.table_count : 0;
      setFeedback({
        kind: "ok",
        text: strings.settings.data.diagOk(bundle.version, tableCount, diagFilename()),
      });
    } catch (err) {
      setFeedback({ kind: "error", text: strings.settings.data.diagFail(String(err)) });
    } finally {
      setDiagnosing(false);
    }
  };

  const onImportFile = async (file: File) => {
    setImporting(true);
    setFeedback(null);
    try {
      const text = await readFileText(file);
      let envelope: DataExportEnvelope;
      try {
        envelope = JSON.parse(text) as DataExportEnvelope;
      } catch {
        throw new Error("文件不是有效的 JSON");
      }
      if (envelope?.format !== "minimax-code-export") {
        throw new Error("不是 MiniMax Code 导出文件（格式不匹配）");
      }
      const totalRows = Object.values(envelope.counts ?? {}).reduce<number>((a, b) => a + b, 0);
      const accepted = await requestConfirmation({
        title: strings.settings.data.importConfirmTitle,
        description: strings.settings.data.importConfirmDesc(totalRows, envelope.schema_version),
        confirmLabel: strings.settings.data.importConfirmLabel,
      });
      if (!accepted) {
        setFeedback(null);
        return;
      }
      const summary: DataImportSummary = await typedIPC.importData(envelope);
      const importedRows = Object.values(summary.imported ?? {}).reduce(
        (a, b) => a + b,
        0,
      );
      const skipped = summary.skipped_tables?.length
        ? strings.settings.data.importSkipped(summary.skipped_tables.length)
        : "";
      setFeedback({
        kind: "ok",
        text: strings.settings.data.importOk(
          importedRows,
          Object.keys(summary.imported ?? {}).length,
          skipped,
        ),
      });
    } catch (err) {
      setFeedback({ kind: "error", text: strings.settings.data.importFail(String(err)) });
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const onBackup = async () => {
    setBacking(true);
    setFeedback(null);
    try {
      const result: DataBackupResult = await typedIPC.backupData();
      setFeedback({
        kind: "ok",
        text: strings.settings.data.backupOk(result.path, formatBytes(result.bytes)),
      });
    } catch (err) {
      setFeedback({ kind: "error", text: strings.settings.data.backupFail(String(err)) });
    } finally {
      setBacking(false);
    }
  };

  return (
    <section data-testid="settings-data" className="space-y-4">
      <TabHeader
        title={strings.settings.data.title}
        hint={strings.settings.data.hint}
      />

      {feedback && (
        <p
          data-testid={
            feedback.kind === "ok" ? "settings-data-feedback" : "settings-data-error"
          }
          role="status"
          className={
            "rounded-md border px-3 py-2 text-xs break-all " +
            (feedback.kind === "ok"
              ? "border-accent/30 bg-accent-subtle text-accent"
              : "border-red-500/30 bg-red-500/10 text-red-400")
          }
        >
          {feedback.text}
        </p>
      )}

      <Panel title={strings.settings.data.exportTitle}>
        <p className="text-[11px] text-ink-2">
          {strings.settings.data.exportDesc}
        </p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="primary"
            data-testid="settings-data-export"
            icon={<Download />}
            disabled={exporting || importing || backing || diagnosing}
            loading={exporting}
            onClick={() => void onExport()}
          >
            {strings.settings.data.exportButton}
          </Button>
        </div>
      </Panel>

      <Panel title={strings.settings.data.importTitle}>
        <p className="text-[11px] text-ink-2">
          {strings.settings.data.importDesc}
        </p>
        <input
          ref={fileInputRef}
          data-testid="settings-data-import-input"
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void onImportFile(file);
          }}
        />
        <div className="mt-3">
          <Button
            size="sm"
            variant="secondary"
            data-testid="settings-data-import"
            icon={<Upload />}
            disabled={exporting || importing || backing || diagnosing}
            loading={importing}
            onClick={() => fileInputRef.current?.click()}
          >
            {strings.settings.data.importButton}
          </Button>
        </div>
      </Panel>

      <Panel title={strings.settings.data.backupTitle}>
        <p className="text-[11px] text-ink-2">
          {strings.settings.data.backupDescLead}{" "}
          <InlineCode>&lt;data dir&gt;/backups/</InlineCode>
          {strings.settings.data.backupDescTail}
        </p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="secondary"
            data-testid="settings-data-backup"
            icon={<HardDriveDownload />}
            disabled={exporting || importing || backing || diagnosing}
            loading={backing}
            onClick={() => void onBackup()}
          >
            {strings.settings.data.backupButton}
          </Button>
        </div>
      </Panel>

      <Panel title={strings.settings.data.diagTitle}>
        <p className="text-[11px] text-ink-2">
          {strings.settings.data.diagDesc}
        </p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="secondary"
            data-testid="settings-data-diagnostic"
            icon={<Activity />}
            disabled={exporting || importing || backing || diagnosing}
            loading={diagnosing}
            onClick={() => void onDiagnostic()}
          >
            {strings.settings.data.diagButton}
          </Button>
        </div>
      </Panel>

      <p className="flex items-start gap-1.5 text-[11px] text-ink-2">
        <DatabaseBackup size={14} className="mt-px shrink-0" aria-hidden="true" />
        <span>{strings.settings.data.footerNote}</span>
      </p>
    </section>
  );
}
