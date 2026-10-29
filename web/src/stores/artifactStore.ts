import { create } from "zustand";
import { ipc, typedIPC } from "../ipc";
import { fetchArtifactContent } from "../ipc/artifactContent";
import type { TextArtifact } from "../types/ipc";

interface ArtifactState {
  projectId: string | null;
  host: string;
  serviceId: string | null;
  nextCursor: string | null; paging: boolean;
  artifacts: TextArtifact[];
  selected: TextArtifact | null;
  preview: string;
  loading: boolean;
  busy: boolean;
  error: string | null;
  activate: (projectId: string | null) => Promise<void>;
  loadMore: () => Promise<void>;
  register: (path: string) => Promise<void>;
  select: (id: string) => Promise<void>;
  download: () => Promise<void>;
  clear: () => void;
}
let epoch = 0;
let selection = 0;
let download: AbortController | null = null;
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export const useArtifactStore = create<ArtifactState>((set, get) => {
  const current = (ticket: number, project: string, host: string) =>
    epoch === ticket && get().projectId === project && get().host === host && ipc.agentBaseUrl === host;
  const validate = (row: TextArtifact, project: string, service: string | null) => {
    if (row.project_id !== project || row.service_id !== service) throw new Error("Artifact association changed; refresh");
  };
  const ready = (project: string | null, host: string, service: string | null) => {
    if (project && service && current(epoch, project, host)) return true;
    get().clear();
    set({ error: "Artifact context changed; refresh the current project" });
    return false;
  };
  return {
    projectId: null, host: "", serviceId: null, nextCursor: null, paging: false, artifacts: [], selected: null, preview: "",
    loading: false, busy: false, error: null,
    clear: () => {
      epoch++; selection++; download?.abort();
      set({ projectId: null, serviceId: null, nextCursor: null, paging: false, artifacts: [], selected: null, preview: "", loading: false, busy: false, error: null });
    },
    activate: async (project) => {
      get().clear();
      if (!project) return;
      const ticket = epoch, host = ipc.agentBaseUrl;
      set({ projectId: project, host, loading: true });
      try {
        const result = await typedIPC.listArtifacts(project);
        if (!current(ticket, project, host)) return;
        result.artifacts.forEach((row) => validate(row, project, result.service_id));
        set({ artifacts: result.artifacts, serviceId: result.service_id, nextCursor: result.next_cursor ?? null });
      } catch (error) { if (current(ticket, project, host)) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ loading: false }); }
    },
    loadMore: async () => {
      const { projectId: project, host, serviceId, nextCursor: cursor } = get();
      if (get().paging || !cursor || !ready(project, host, serviceId) || !project) return;
      const ticket = epoch;
      set({ paging: true, error: null });
      try {
        const result = await typedIPC.listArtifacts(project, undefined, cursor);
        if (!current(ticket, project, host)) return;
        if (result.service_id !== serviceId || result.artifacts.length > 200 ||
            (result.next_cursor != null && (!/^art_[a-f0-9]{32}$/.test(result.next_cursor) ||
              result.next_cursor !== result.artifacts[result.artifacts.length - 1]?.id || result.next_cursor === cursor)))
          throw new Error("Artifact page is inconsistent; refresh");
        result.artifacts.forEach((row) => validate(row, project, serviceId));
        const existing = new Set(get().artifacts.map((row) => row.id));
        set({ artifacts: [...get().artifacts, ...result.artifacts.filter((row) => !existing.has(row.id))], nextCursor: result.next_cursor ?? null });
      } catch (error) { if (current(ticket, project, host)) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ paging: false }); }
    },
    register: async (path) => {
      const { projectId: project, host, serviceId } = get();
      if (get().busy || !ready(project, host, serviceId) || !project) return;
      const ticket = epoch;
      set({ busy: true, error: null });
      try {
        const result = await typedIPC.registerArtifact({ project_id: project, source_path: path });
        if (!current(ticket, project, host)) return;
        validate(result.artifact, project, serviceId);
        set({ artifacts: [result.artifact, ...get().artifacts] });
        await get().select(result.artifact.id);
      } catch (error) { if (current(ticket, project, host)) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
    select: async (id) => {
      const { projectId: project, host, serviceId } = get();
      if (!ready(project, host, serviceId) || !project) return;
      const ticket = epoch, request = ++selection;
      download?.abort();
      set({ selected: null, preview: "", error: null, loading: true });
      try {
        const result = await typedIPC.getArtifact(project, id);
        if (!current(ticket, project, host) || selection !== request) return;
        validate(result.artifact, project, serviceId);
        if (result.artifact.id !== id) throw new Error("Artifact identity changed; refresh");
        set({ selected: result.artifact, preview: result.preview_text });
      } catch (error) { if (current(ticket, project, host) && selection === request) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host) && selection === request) set({ loading: false }); }
    },
    download: async () => {
      const { selected: row, projectId: project, host, serviceId } = get();
      if (get().busy || !ready(project, host, serviceId) || !row || !project) return;
      try { validate(row, project, serviceId); }
      catch (error) { get().clear(); set({ error: errorText(error) }); return; }
      const ticket = epoch, request = selection;
      download?.abort(); download = new AbortController();
      set({ busy: true, error: null });
      try {
        const blob = await fetchArtifactContent(row, host, download.signal);
        if (!current(ticket, project, host) || selection !== request) return;
        const url = URL.createObjectURL(blob), anchor = document.createElement("a");
        anchor.href = url; anchor.download = row.source_path.split("/").pop() ?? "artifact.txt";
        anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (error) { if (current(ticket, project, host) && selection === request) set({ error: errorText(error) }); }
      finally { if (current(ticket, project, host)) set({ busy: false }); }
    },
  };
});
