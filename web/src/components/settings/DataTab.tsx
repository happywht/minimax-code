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
import { DatabaseBackup, Download, HardDriveDownload, Upload } from "lucide-react";
import { Button, Panel } from "../../ui";
import { typedIPC } from "../../ipc";
import type {
  DataBackupResult,
  DataExportEnvelope,
  DataImportSummary,
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
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const onExport = async () => {
    setExporting(true);
    setFeedback(null);
    try {
      const envelope = await typedIPC.exportData();
      const blob = new Blob([JSON.stringify(envelope, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      try {
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = exportFilename();
        anchor.click();
      } finally {
        URL.revokeObjectURL(url);
      }
      const totalRows = Object.values(envelope.counts ?? {}).reduce<number>((a, b) => a + b, 0);
      setFeedback({
        kind: "ok",
        text: `Exported ${totalRows} rows across ${
          Object.keys(envelope.tables ?? {}).length
        } tables → ${exportFilename()}`,
      });
    } catch (err) {
      setFeedback({ kind: "error", text: `Export failed: ${String(err)}` });
    } finally {
      setExporting(false);
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
        throw new Error("the file is not valid JSON");
      }
      if (envelope?.format !== "minimax-code-export") {
        throw new Error("not a MiniMax Code export file (format mismatch)");
      }
      const totalRows = Object.values(envelope.counts ?? {}).reduce<number>((a, b) => a + b, 0);
      const accepted = await requestConfirmation({
        title: "Import this export file?",
        description:
          `Replace-import: every table present in the file overwrites its current data ` +
          `(${totalRows} rows, schema v${envelope.schema_version}). This cannot be undone — ` +
          `take a backup first if unsure.`,
        confirmLabel: "Import",
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
        ? ` (${summary.skipped_tables.length} unknown table(s) skipped)`
        : "";
      setFeedback({
        kind: "ok",
        text: `Imported ${importedRows} rows across ${
          Object.keys(summary.imported ?? {}).length
        } tables${skipped}.`,
      });
    } catch (err) {
      setFeedback({ kind: "error", text: `Import failed: ${String(err)}` });
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
        text: `Backup written: ${result.path} (${formatBytes(result.bytes)})`,
      });
    } catch (err) {
      setFeedback({ kind: "error", text: `Backup failed: ${String(err)}` });
    } finally {
      setBacking(false);
    }
  };

  return (
    <section data-testid="settings-data" className="space-y-4">
      <TabHeader
        title="Data portability"
        hint={
          <>
            Move your data in and out of the local agent database. Export produces a portable
            JSON envelope; backup produces a file-level SQLite snapshot kept on the same machine.
          </>
        }
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

      <Panel title="Export to JSON">
        <p className="text-[11px] text-ink-2">
          Dumps every business table (sessions, messages, memories, permissions, …) into a
          single self-describing JSON document. Schema-versioned, so an older install
          refuses to import files from a newer one instead of corrupting itself.
        </p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="primary"
            data-testid="settings-data-export"
            icon={<Download />}
            disabled={exporting || importing || backing}
            loading={exporting}
            onClick={() => void onExport()}
          >
            Export data
          </Button>
        </div>
      </Panel>

      <Panel title="Import from JSON">
        <p className="text-[11px] text-ink-2">
          Restore an export file. Replace semantics: each table in the file fully overwrites
          its current contents inside one transaction — all rows land or none do. Unknown
          tables and drifted columns are skipped, never fatal.
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
            disabled={exporting || importing || backing}
            loading={importing}
            onClick={() => fileInputRef.current?.click()}
          >
            Choose file…
          </Button>
        </div>
      </Panel>

      <Panel title="Backup snapshot">
        <p className="text-[11px] text-ink-2">
          Hot-copies the SQLite database via the online backup API — schema, WAL contents,
          and derived indexes (full-text, vector) included — into{" "}
          <InlineCode>&lt;data dir&gt;/backups/</InlineCode> with a UTC timestamp in the
          filename. The agent keeps running; the source is only read.
        </p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="secondary"
            data-testid="settings-data-backup"
            icon={<HardDriveDownload />}
            disabled={exporting || importing || backing}
            loading={backing}
            onClick={() => void onBackup()}
          >
            Back up now
          </Button>
        </div>
      </Panel>

      <p className="flex items-start gap-1.5 text-[11px] text-ink-2">
        <DatabaseBackup size={14} className="mt-px shrink-0" aria-hidden="true" />
        <span>
          Exports are the cross-version format (e.g. machine-to-machine migration); backups
          are the same-version disaster-recovery format. Keep at least one backup before a
          big import.
        </span>
      </p>
    </section>
  );
}
