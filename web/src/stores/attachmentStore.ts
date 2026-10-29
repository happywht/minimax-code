import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { fetchAttachmentContent, uploadAttachment } from "../ipc/attachmentContent";
import type { AttachmentOriginal, DocxContent } from "../types/ipc";

interface AttachmentState {
  projectId: string | null; host: string; serviceId: string | null;
  attachments: AttachmentOriginal[]; selected: AttachmentOriginal | null; preview: string;
  loading: boolean; busy: boolean; error: string | null;
  activate: (projectId: string | null) => Promise<void>;
  upload: (file: File) => Promise<void>;
  generateDocx: (filename: string, title: string, content: DocxContent, format?: "docx" | "pdf") => Promise<void>;
  select: (id: string) => Promise<void>;
  download: () => Promise<void>;
  clear: () => void;
}
let epoch = 0, selection = 0;
let uploading: AbortController | null = null, downloading: AbortController | null = null;
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const statuses = new Set(["parsed", "empty", "truncated", "parse_failed", "type_mismatch", "parser_unavailable", "ocr_not_attempted"]);
const reasons = new Set(["ok", "no_extracted_body_text_no_ocr", "preview_limit", "invalid_package", "unsafe_package", "package_limit", "unsupported_feature", "missing_dependency", "limits_unavailable", "queue_timeout", "parser_timeout", "parser_crash", "output_limit", "invalid_parser_output", "invalid_text", "type_mismatch", "unsupported_type", "ocr_not_attempted"]);

export const useAttachmentStore = create<AttachmentState>((set, get) => {
  const current = (ticket: number, project: string, host: string) =>
    epoch === ticket && get().projectId === project && get().host === host && ipc.agentBaseUrl === host;
  const validate = (row: AttachmentOriginal, project: string, service: string | null) => {
    if (row.project_id !== project || row.service_id !== service) throw new Error("Attachment association changed; refresh");
    if (!/^att_[a-f0-9]{32}$/.test(row.id) || !/^[a-f0-9]{64}$/.test(row.sha256)
      || typeof row.original_name !== "string" || !row.original_name.trim() || [...row.original_name].length > 240
      || [...row.original_name].some((char) => char.charCodeAt(0) < 32 || (char.charCodeAt(0) >= 127 && char.charCodeAt(0) <= 159)) || !Number.isInteger(row.size_bytes) || row.size_bytes < 0 || row.size_bytes > 10485760
      || !statuses.has(row.parser_status) || row.quality_status !== "unverified"
      || !(row.parser_version === 1 && row.parser_scope === "legacy_v1" && row.parser_reason === "legacy_v1"
        || row.parser_version === 2 && ["inert_text", "docx_body_paragraphs_tables", "original_only"].includes(row.parser_scope) && reasons.has(row.parser_reason))) throw new Error("Attachment metadata is inconsistent");
  };
  const ready = (project: string | null, host: string, service: string | null) => {
    if (project && service && current(epoch, project, host)) return true;
    get().clear(); set({ error: "Attachment context changed; refresh the current project" }); return false;
  };
  return {
    projectId: null, host: "", serviceId: null, attachments: [], selected: null, preview: "", loading: false, busy: false, error: null,
    clear: () => {
      epoch++; selection++; uploading?.abort(); downloading?.abort();
      set({ projectId: null, serviceId: null, attachments: [], selected: null, preview: "", loading: false, busy: false, error: null });
    },
    activate: async (project) => {
      get().clear(); if (!project) return;
      const ticket = epoch, host = ipc.agentBaseUrl;
      set({ projectId: project, host, loading: true });
      try {
        const result = await typedIPC.listAttachments(project);
        if (!current(ticket, project, host)) return;
        if (!result.service_id || result.attachments.length > 200) throw new Error("Attachment list is inconsistent");
        result.attachments.forEach((row) => validate(row, project, result.service_id));
        set({ attachments: result.attachments, serviceId: result.service_id });
      } catch (error) { if (current(ticket, project, host)) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ loading: false }); }
    },
    upload: async (file) => {
      const { projectId: project, host, serviceId } = get();
      if (get().busy || !ready(project, host, serviceId) || !project || !serviceId) return;
      const ticket = epoch;
      uploading?.abort(); uploading = new AbortController();
      set({ busy: true, error: null });
      try {
        const row = await uploadAttachment(file, project, serviceId, host, uploading.signal);
        if (!current(ticket, project, host)) return;
        validate(row, project, serviceId);
        set({ attachments: [row, ...get().attachments].slice(0, 200) });
        await get().select(row.id);
      } catch (error) { if (current(ticket, project, host)) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    generateDocx: async (filename, title, content, format = "docx") => {
      const { projectId: project, host, serviceId } = get();
      if (get().busy || !ready(project, host, serviceId) || !project || !serviceId) return;
      const ticket = epoch;
      set({ busy: true, error: null });
      try {
        const params = { project_id: project, expected_service_id: serviceId, filename, title, ...content };
        const result = await (format === "pdf" ? typedIPC.generatePdf(params) : typedIPC.generateDocx(params));
        if (!current(ticket, project, host)) return;
        validate(result.attachment, project, serviceId);
        set({ attachments: [result.attachment, ...get().attachments].slice(0, 200) });
        await get().select(result.attachment.id);
      } catch (error) { if (current(ticket, project, host)) set({ error: `${errorText(error)}；如响应丢失，请先刷新检查是否已生成。` }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    select: async (id) => {
      const { projectId: project, host, serviceId } = get();
      if (!ready(project, host, serviceId) || !project) return;
      const ticket = epoch, request = ++selection;
      downloading?.abort(); set({ selected: null, preview: "", error: null, loading: true });
      try {
        const result = await typedIPC.getAttachment(project, id);
        if (!current(ticket, project, host) || selection !== request) return;
        validate(result.attachment, project, serviceId);
        if (result.attachment.id !== id) throw new Error("Attachment identity changed; refresh");
        // Deliberate Python splitlines separators, not executable document markup.
        if (typeof result.preview_text !== "string" || new TextEncoder().encode(result.preview_text).length > 65536
          // eslint-disable-next-line no-control-regex
          || result.preview_text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/).length > 2001) throw new Error("Attachment preview exceeds bounds");
        set({ selected: result.attachment, preview: result.preview_text });
      } catch (error) { if (current(ticket, project, host) && selection === request) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host) && selection === request) set({ loading: false }); }
    },
    download: async () => {
      const { selected: row, projectId: project, host, serviceId } = get();
      if (get().busy || !ready(project, host, serviceId) || !row || !project) return;
      try { validate(row, project, serviceId); }
      catch (error) { get().clear(); set({ error: errorText(error) }); return; }
      const ticket = epoch, request = selection;
      downloading?.abort(); downloading = new AbortController(); set({ busy: true, error: null });
      try {
        const blob = await fetchAttachmentContent(row, host, downloading.signal);
        if (!current(ticket, project, host) || selection !== request) return;
        const url = URL.createObjectURL(blob), anchor = document.createElement("a");
        anchor.href = url; anchor.download = row.original_name.replace(/[\\/:*?"<>|]/g, "_");
        const revoke = URL.revokeObjectURL.bind(URL);
        anchor.click(); setTimeout(() => revoke(url), 1000);
      } catch (error) { if (current(ticket, project, host) && selection === request) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
  };
});
