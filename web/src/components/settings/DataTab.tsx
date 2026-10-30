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
import { useEffect, useRef, useState } from "react";
import { Activity, DatabaseBackup, Download, HardDriveDownload, Upload } from "lucide-react";
import { Button, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import { ipc, typedIPC } from "../../ipc";
import type {
  DataBackupResult,
  DataBackupList,
  DataExportEnvelope,
  DataImportSummary,
  DiagnosticBundle,
} from "../../types/ipc";
import { InlineCode, TabHeader } from "./fields";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { TableArchive } from "./TableArchive";

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
  const [backupDirectory, setBackupDirectory] = useState("");
  const [listingBackups, setListingBackups] = useState(false);
  const [backups, setBackups] = useState<DataBackupList | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const connectionEpoch = useRef(0);
  const [directoryPreferenceBusy, setDirectoryPreferenceBusy] = useState(false);

  useEffect(() => {
    const unsubscribe = ipc.onSideCar(() => {
      connectionEpoch.current++;
      setBackupDirectory("");
      setBackups(null);
      setFeedback({ kind: "error", text: "服务连接已变化，请重新核对备份目录。" });
    });
    return () => { connectionEpoch.current++; unsubscribe(); };
  }, []);

  const directoryPreference = async (action: "save" | "load" | "clear") => {
    const epoch = connectionEpoch.current;
    const directory = backupDirectory.trim();
    setDirectoryPreferenceBusy(true);
    try {
      const identity = await typedIPC.getServiceIdentity();
      if (connectionEpoch.current !== epoch) return;
      if (!identity.service_id) throw new Error("无法确认服务身份");
      const key = `minimax.backup-directory.v1.${identity.service_id}`;
      if (action === "save") {
        if (!directory) throw new Error("请先填写自定义备份目录");
        localStorage.setItem(key, directory);
        setFeedback({ kind: "ok", text: "已在此浏览器保存当前服务的目录；备份时仍需确认路径有效。" });
      } else if (action === "load") {
        const saved = localStorage.getItem(key);
        if (!saved) throw new Error("此浏览器没有保存当前服务的目录");
        setBackupDirectory(saved);
        setBackups(null);
        setFeedback({ kind: "ok", text: "已填入保存的目录，请核对服务和路径后再备份。" });
      } else {
        localStorage.removeItem(key);
        setFeedback({ kind: "ok", text: "已清除当前服务在此浏览器保存的目录。" });
      }
    } catch (err) {
      if (connectionEpoch.current === epoch) setFeedback({ kind: "error", text: `目录设置失败：${String(err)}` });
    } finally {
      setDirectoryPreferenceBusy(false);
    }
  };

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

  const onListBackups = async () => {
    setListingBackups(true);
    setFeedback(null);
    setBackups(null);
    try {
      setBackups(await typedIPC.listDataBackups(backupDirectory.trim() || undefined));
    } catch (err) {
      setFeedback({ kind: "error", text: `读取备份清单失败：${String(err)}` });
    } finally {
      setListingBackups(false);
    }
  };

  const onBackup = async () => {
    setBacking(true);
    setBackups(null);
    setFeedback(null);
    try {
      const directory = backupDirectory.trim();
      const result: DataBackupResult = await typedIPC.backupData(directory || undefined, Boolean(directory));
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

      <p data-testid="settings-data-checkpoint-scope" className="text-xs text-status-warning">
        导出和备份只保存数据库记录，不包含检查点的未跟踪文件副本或 Git 对象；恢复数据库不能保证检查点可用。请另行保全这些原件。
      </p>

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
            disabled={exporting || importing || backing || diagnosing || listingBackups}
            loading={exporting}
            onClick={() => void onExport()}
          >
            {strings.settings.data.exportButton}
          </Button>
        </div>
      </Panel>

      <TableArchive disabled={exporting || importing || backing || diagnosing || listingBackups} />

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
            disabled={exporting || importing || backing || diagnosing || listingBackups}
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
        <label htmlFor="backup-directory" className="mt-3 block text-xs text-ink-1">
          {strings.settings.data.backupDirectoryLabel}
        </label>
        <Input
          id="backup-directory"
          data-testid="settings-data-backup-directory"
          className="mt-1"
          value={backupDirectory}
          onChange={(event) => { setBackupDirectory(event.target.value); setBackups(null); }}
          disabled={exporting || importing || backing || diagnosing || listingBackups || directoryPreferenceBusy}
          placeholder={strings.settings.data.backupDirectoryPlaceholder}
          aria-describedby="backup-directory-hint"
        />
        <p id="backup-directory-hint" className="mt-1 text-[11px] text-ink-2">
          {strings.settings.data.backupDirectoryHint}
        </p>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={directoryPreferenceBusy || backing || listingBackups || !backupDirectory.trim()} onClick={() => void directoryPreference("save")}>保存目录</Button>
          <Button size="sm" variant="secondary" disabled={directoryPreferenceBusy || backing || listingBackups} onClick={() => void directoryPreference("load")}>填入保存的目录</Button>
          <Button size="sm" variant="secondary" disabled={directoryPreferenceBusy || backing || listingBackups} onClick={() => void directoryPreference("clear")}>清除保存的目录</Button>
        </div>
        <p className="mt-1 text-[11px] text-ink-2">目录只保存在当前浏览器并按服务身份区分，不随备份迁移；克隆数据库可能复用服务身份，使用前请核对机器和路径。</p>
        <div className="mt-3">
          <Button
            size="sm"
            variant="secondary"
            data-testid="settings-data-backup"
            icon={<HardDriveDownload />}
            disabled={exporting || importing || backing || diagnosing || listingBackups || directoryPreferenceBusy}
            loading={backing}
            onClick={() => void onBackup()}
          >
            {strings.settings.data.backupButton}
          </Button>
        </div>
        <div className="mt-3">
          <Button size="sm" variant="secondary" data-testid="settings-data-backup-list"
            disabled={exporting || importing || backing || diagnosing || listingBackups || directoryPreferenceBusy}
            loading={listingBackups} onClick={() => void onListBackups()}>
            查看已有备份
          </Button>
        </div>
        {backups && (
          <div className="mt-3 text-xs text-ink-2" data-testid="settings-data-backup-inventory">
            <p className="break-all">目录：{backups.directory}</p>
            <p>仅列出 SQLite 备份文件，未检查完整性，也不包含附件等独立文件。</p>
            {backups.items.length === 0 ? <p>未找到备份文件。</p> : (
              <ul className="mt-2 space-y-2">
                {backups.items.map((item) => (
                  <li key={item.path} className="break-all">
                    {item.path} · {formatBytes(item.bytes)} · {new Date(item.modified_at).toLocaleString()}
                  </li>
                ))}
              </ul>
            )}
            {backups.truncated && <p>只显示最近修改的 100 个文件。</p>}
          </div>
        )}
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
            disabled={exporting || importing || backing || diagnosing || listingBackups}
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
