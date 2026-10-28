import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { fetchAttachmentContent } from "../ipc/attachmentContent";
import type { OfficeEntry, OfficeGenerateParams, OfficeGetResult } from "../types/ipc";

interface OfficeState {
  projectId: string | null;
  host: string;
  serviceId: string | null;
  entries: OfficeEntry[];
  selected: OfficeGetResult | null;
  busy: boolean;
  error: string | null;
  clear: () => void;
  activate: (project: string | null) => Promise<void>;
  generate: (input: Omit<OfficeGenerateParams, "project_id" | "expected_service_id">) => Promise<void>;
  select: (id: string) => Promise<void>;
  download: () => Promise<void>;
}
let epoch = 0;
let selection = 0;
let download: AbortController | null = null;
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

function validate(row: OfficeEntry, project: string, service: string): void {
  const { delivery, attachment } = row;
  const report = delivery.validation_report;
  if (delivery.project_id !== project || delivery.service_id !== service || attachment.project_id !== project ||
      attachment.service_id !== service || attachment.id !== delivery.attachment_id ||
      report.file_sha256 !== attachment.sha256 || report.size_bytes !== attachment.size_bytes ||
      report.input_sha256 !== delivery.input_sha256 || report.scope !== "xlsx_plain_cells_v1" ||
      report.business_quality !== "unverified" || report.render_status !== "not_attempted") {
    throw new Error("Office association changed; refresh the current project");
  }
}

export const useOfficeDeliveryStore = create<OfficeState>((set, get) => {
  const current = (ticket: number, project: string, host: string) =>
    epoch === ticket && get().projectId === project && get().host === host && ipc.agentBaseUrl === host;
  const context = () => {
    const state = get();
    if (!state.projectId || !state.serviceId || !current(epoch, state.projectId, state.host)) {
      get().clear(); set({ error: "Office context changed; refresh the current project" });
      return null;
    }
    return { project: state.projectId, service: state.serviceId, host: state.host, ticket: epoch };
  };
  return {
    projectId: null, host: "", serviceId: null, entries: [], selected: null, busy: false, error: null,
    clear: () => {
      epoch++; selection++; download?.abort();
      set({ projectId: null, host: "", serviceId: null, entries: [], selected: null, busy: false, error: null });
    },
    activate: async (project) => {
      get().clear();
      if (!project) return;
      const host = ipc.agentBaseUrl, ticket = epoch;
      set({ projectId: project, host, busy: true });
      try {
        const result = await typedIPC.listOfficeDeliveries(project);
        if (!current(ticket, project, host)) return;
        result.deliveries.forEach((row) => validate(row, project, result.service_id));
        set({ entries: result.deliveries, serviceId: result.service_id });
      } catch (error) { if (current(ticket, project, host)) set({ error: message(error) }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    generate: async (input) => {
      const scope = context();
      if (!scope || get().busy) return;
      const { project, service, host, ticket } = scope;
      set({ busy: true, error: null });
      try {
        const result = await typedIPC.generateOfficeXlsx({ ...input, project_id: project, expected_service_id: service });
        if (!current(ticket, project, host)) return;
        validate(result, project, service);
        selection++; download?.abort();
        set({ selected: result, entries: [result, ...get().entries.filter((entry) => entry.delivery.id !== result.delivery.id)] });
      } catch (error) { if (current(ticket, project, host)) set({ error: `${message(error)}；刷新可找回已提交结果，同一请求重试不会新建版本。` }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    select: async (id) => {
      const scope = context();
      if (!scope) return;
      const { project, service, host, ticket } = scope, request = ++selection;
      download?.abort(); set({ busy: true, selected: null, error: null });
      try {
        const result = await typedIPC.getOfficeDelivery(project, id);
        if (!current(ticket, project, host) || request !== selection) return;
        validate(result, project, service);
        if (result.delivery.id !== id) throw new Error("Office identity changed; refresh");
        set({ selected: result });
      } catch (error) { if (current(ticket, project, host) && request === selection) set({ error: message(error) }); }
      finally { if (current(ticket, project, host) && request === selection) set({ busy: false }); }
    },
    download: async () => {
      const scope = context(), selected = get().selected;
      if (!scope || !selected || get().busy) return;
      const { project, service, host, ticket } = scope, request = selection;
      try { validate(selected, project, service); }
      catch (error) { get().clear(); set({ error: message(error) }); return; }
      download?.abort(); download = new AbortController(); set({ busy: true, error: null });
      try {
        const blob = await fetchAttachmentContent(selected.attachment, host, download.signal);
        if (!current(ticket, project, host) || request !== selection) return;
        const url = URL.createObjectURL(blob), anchor = document.createElement("a");
        anchor.href = url; anchor.download = selected.attachment.original_name; anchor.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { if (current(ticket, project, host) && request === selection) set({ error: message(error) }); }
      finally { if (current(ticket, project, host) && request === selection) set({ busy: false }); }
    },
  };
});
