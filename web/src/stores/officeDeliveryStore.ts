import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { fetchAttachmentContent } from "../ipc/attachmentContent";
import type { OfficeAnalysisParams, OfficeAnalysisResult, OfficeEntry, OfficeGenerateParams, OfficeGetResult } from "../types/ipc";

interface OfficeState {
  projectId: string | null;
  host: string;
  serviceId: string | null;
  entries: OfficeEntry[];
  query: string;
  nextCursor: string | null;
  paging: boolean;
  selected: OfficeGetResult | null;
  busy: boolean;
  error: string | null;
  clear: () => void;
  activate: (project: string | null, query?: string) => Promise<void>;
  loadMore: () => Promise<void>;
  generate: (input: Omit<OfficeGenerateParams, "project_id" | "expected_service_id">) => Promise<OfficeGetResult | null>;
  generateAnalysis: (input: Omit<OfficeAnalysisParams, "project_id" | "expected_service_id">) => Promise<Extract<OfficeAnalysisResult, { kind: "delivery" }> | null>;
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
      report.input_sha256 !== delivery.input_sha256 ||
      (report.scope === "xlsx_plain_cells_v1" ? report.structure_status !== "passed" :
        report.scope !== "xlsx_chart_values_v1" || report.cell_check !== "passed" || report.chart_reference_check !== "passed") ||
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
    projectId: null, host: "", serviceId: null, entries: [], query: "", nextCursor: null, paging: false, selected: null, busy: false, error: null,
    clear: () => {
      epoch++; selection++; download?.abort();
      set({ projectId: null, host: "", serviceId: null, entries: [], query: "", nextCursor: null, paging: false, selected: null, busy: false, error: null });
    },
    activate: async (project, query = "") => {
      get().clear();
      if (!project) return;
      const host = ipc.agentBaseUrl, ticket = epoch;
      set({ projectId: project, host, query, busy: true });
      try {
        const result = await typedIPC.listOfficeDeliveries(project, undefined, query || undefined);
        if (!current(ticket, project, host)) return;
        result.deliveries.forEach((row) => validate(row, project, result.service_id));
        set({ entries: result.deliveries, serviceId: result.service_id, nextCursor: result.next_cursor ?? null });
      } catch (error) { if (current(ticket, project, host)) set({ error: message(error) }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    loadMore: async () => {
      const scope = context(), { nextCursor: cursor, query, paging, busy } = get();
      if (!scope || !cursor || paging || busy) return;
      const { project, service, host, ticket } = scope;
      set({ paging: true, error: null });
      try {
        const result = await typedIPC.listOfficeDeliveries(project, cursor, query || undefined);
        if (!current(ticket, project, host)) return;
        if (result.service_id !== service || result.deliveries.length > 200 ||
            (result.next_cursor != null && (!result.next_cursor.startsWith("q_") || result.next_cursor === cursor)))
          throw new Error("Office page is inconsistent; refresh");
        result.deliveries.forEach((row) => validate(row, project, service));
        const existing = new Set(get().entries.map((row) => row.delivery.id));
        set({ entries: [...get().entries, ...result.deliveries.filter((row) => !existing.has(row.delivery.id))],
          nextCursor: result.next_cursor ?? null });
      } catch (error) { if (current(ticket, project, host)) set({ error: message(error) }); }
      finally { if (current(ticket, project, host)) set({ paging: false }); }
    },
    generate: async (input) => {
      const scope = context();
      if (!scope || get().busy) return null;
      const { project, service, host, ticket } = scope;
      set({ busy: true, error: null });
      try {
        const result = await typedIPC.generateOfficeXlsx({ ...input, project_id: project, expected_service_id: service });
        if (!current(ticket, project, host)) return null;
        validate(result, project, service);
        selection++; download?.abort();
        set({ selected: result, entries: (!get().query || [result.attachment.original_name, result.delivery.id, result.delivery.run_id ?? ""].some((value) => value.includes(get().query))) ? [result, ...get().entries.filter((entry) => entry.delivery.id !== result.delivery.id)] : get().entries });
        return result;
      } catch (error) { if (current(ticket, project, host)) set({ error: `${message(error)}；刷新可找回已提交结果，同一请求重试不会新建版本。` }); return null; }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    generateAnalysis: async (input) => {
      const scope = context();
      if (!scope || get().busy) return null;
      const { project, service, host, ticket } = scope;
      set({ busy: true, error: null, selected: null });
      try {
        const result = await typedIPC.generateOfficeAnalysis({ ...input, project_id: project, expected_service_id: service, register_chart_delivery: true });
        if (!current(ticket, project, host)) return null;
        selection++; download?.abort();
        if (result.kind !== "delivery") throw new Error("未知的办公成果类型");
        validate(result, project, service);
        set({ selected: result, entries: (!get().query || [result.attachment.original_name, result.delivery.id, result.delivery.run_id ?? ""].some((value) => value.includes(get().query))) ? [result, ...get().entries.filter((entry) => entry.delivery.id !== result.delivery.id)] : get().entries });
        return result;
      } catch (error) {
        if (current(ticket, project, host)) set({ error: `${message(error)}；刷新可找回已提交结果，同一请求重试不会新建版本。` });
        return null;
      } finally { if (current(ticket, project, host)) set({ busy: false }); }
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
