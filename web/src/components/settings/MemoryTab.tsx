/**
 * Memory tab — manage long-term memories via `memory.*` IPC.
 *
 * Memories are facts/preferences/decisions/lessons that the agent injects
 * into the system prompt when a session has a matching project_id or
 * session_id.
 */
import { useEffect, useState } from "react";
import { Brain, Plus, Search, Trash2, X } from "lucide-react";
import { Badge } from "../../ui/Badge";
import { Button, ErrorBanner, IconButton, Input, Panel, Textarea } from "../../ui";
import { strings } from "../../ui/strings";
import { useMemoryStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import type { MemoryCategory, MemoryEntry } from "../../types/ipc";
import { formatRelative } from "../../lib/time";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { InlineCode, Select, TabHeader } from "./fields";

const CATEGORIES: MemoryCategory[] = ["preference", "decision", "lesson", "fact"];

const CATEGORY_TONE: Record<MemoryCategory, "accent" | "success" | "warning" | "info"> = {
  preference: "accent",
  decision: "success",
  lesson: "warning",
  fact: "info",
};

export function MemoryTab(): JSX.Element {
  const memories = useMemoryStore((s) => s.memories);
  const total = useMemoryStore((s) => s.total);
  const loading = useMemoryStore((s) => s.loading);
  const error = useMemoryStore((s) => s.error);
  const searchQuery = useMemoryStore((s) => s.searchQuery);
  const refresh = useMemoryStore((s) => s.refresh);
  const search = useMemoryStore((s) => s.search);
  const add = useMemoryStore((s) => s.add);
  const remove = useMemoryStore((s) => s.remove);
  const setSearchQuery = useMemoryStore((s) => s.setSearchQuery);

  const [draftContent, setDraftContent] = useState("");
  const [draftCategory, setDraftCategory] = useState<MemoryCategory>("fact");
  const [draftConfidence, setDraftConfidence] = useState(1.0);
  const [filterProjectId, setFilterProjectId] = useState("");
  const [filterSessionId, setFilterSessionId] = useState("");

  const filterOpts = {
    project_id: filterProjectId.trim() || undefined,
    session_id: filterSessionId.trim() || undefined,
  };

  useEffect(() => {
    if (memories.length === 0 && !searchQuery) void refresh(filterOpts);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [memories.length, searchQuery, refresh]);

  const handleSearch = (query: string) => {
    setSearchQuery(query);
    void search(query, filterOpts);
  };

  const handleAdd = async () => {
    const content = draftContent.trim();
    if (!content) {
      toast.error(strings.settings.memory.contentRequiredTitle, strings.settings.memory.contentRequiredDesc);
      return;
    }
    const memory = await add({
      content,
      category: draftCategory,
      confidence: draftConfidence,
    });
    if (memory) {
      toast.success(strings.settings.memory.addedToast);
      setDraftContent("");
      setDraftCategory("fact");
      setDraftConfidence(1.0);
    }
  };

  const handleDelete = async (memory: MemoryEntry) => {
    const accepted = await requestConfirmation({
      title: strings.settings.memory.deleteTitle,
      description: memory.content.slice(0, 120) + (memory.content.length > 120 ? "…" : ""),
      confirmLabel: strings.settings.memory.deleteLabel,
    });
    if (accepted) await remove(memory.id);
  };

  return (
    <section data-testid="settings-memory" className="space-y-4">
      <TabHeader
        title={strings.settings.memory.title}
        hint={
          <>
            {strings.settings.memory.hintLead}{" "}
            <InlineCode>## Relevant memories</InlineCode>
            {strings.settings.memory.hintTail}
          </>
        }
      />

      <Panel title={strings.settings.memory.addTitle}>
        <div className="space-y-3">
          <Textarea
            data-testid="settings-memory-content"
            value={draftContent}
            onChange={(e) => setDraftContent(e.target.value)}
            placeholder={strings.settings.memory.placeholder}
            className="min-h-[80px]"
          />
          <div className="flex flex-wrap items-end gap-2">
            <div className="w-40">
              <label htmlFor="memory-category" className="mb-1 block text-[11px] text-ink-2">
                {strings.settings.memory.category}
              </label>
              <Select
                id="memory-category"
                data-testid="settings-memory-category"
                value={draftCategory}
                onChange={(e) => setDraftCategory(e.target.value as MemoryCategory)}
              >
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </div>
            <div className="min-w-[140px] flex-1">
              <label htmlFor="memory-confidence" className="mb-1 block text-[11px] text-ink-2">
                {strings.settings.memory.confidence((draftConfidence * 100).toFixed(0))}
              </label>
              <input
                id="memory-confidence"
                data-testid="settings-memory-confidence"
                type="range"
                min={0}
                max={1}
                step={0.1}
                value={draftConfidence}
                onChange={(e) => setDraftConfidence(parseFloat(e.target.value))}
                className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-surface-3 accent-accent"
              />
            </div>
            <Button
              size="sm"
              variant="subtle"
              data-testid="settings-memory-add"
              disabled={!draftContent.trim()}
              onClick={() => void handleAdd()}
              icon={<Plus />}
            >
              {strings.settings.memory.add}
            </Button>
          </div>
        </div>
      </Panel>

      <div className="space-y-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-2" />
          <Input
            data-testid="settings-memory-search"
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder={strings.settings.memory.searchPlaceholder}
            className="pl-8 pr-8"
          />
          {searchQuery && (
            <IconButton
              className="absolute right-1 top-1/2 -translate-y-1/2"
              aria-label={strings.settings.memory.clearSearch}
              onClick={() => handleSearch("")}
            >
              <X />
            </IconButton>
          )}
        </div>
        <div className="flex gap-2">
          <Input
            data-testid="settings-memory-filter-project"
            value={filterProjectId}
            onChange={(e) => {
              setFilterProjectId(e.target.value);
              void search(searchQuery, { ...filterOpts, project_id: e.target.value.trim() || undefined });
            }}
            placeholder={strings.settings.memory.filterProject}
            className="text-xs"
          />
          <Input
            data-testid="settings-memory-filter-session"
            value={filterSessionId}
            onChange={(e) => {
              setFilterSessionId(e.target.value);
              void search(searchQuery, { ...filterOpts, session_id: e.target.value.trim() || undefined });
            }}
            placeholder={strings.settings.memory.filterSession}
            className="text-xs"
          />
        </div>
      </div>

      <div className="flex items-center gap-2 text-[11px] text-ink-2">
        <Brain size={12} />
        <span data-testid="settings-memory-count">
          {strings.settings.memory.count(total)}
        </span>
      </div>

      <ul className="space-y-1.5" data-testid="settings-memory-list">
        {error && (
          <li>
            <ErrorBanner
              message={error}
              onRetry={() => void refresh(filterOpts)}
              testId="settings-memory-error"
            />
          </li>
        )}
        {memories.length === 0 && !loading && !error && (
          <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">
            {searchQuery ? strings.settings.memory.emptySearch : strings.settings.memory.empty}
          </li>
        )}
        {memories.map((m) => (
          <MemoryRow key={m.id} memory={m} onDelete={() => void handleDelete(m)} />
        ))}
      </ul>
    </section>
  );
}

function MemoryRow({ memory, onDelete }: { memory: MemoryEntry; onDelete: () => void }) {
  return (
    <li
      data-testid={`settings-memory-row-${memory.id}`}
      className="rounded-lg border border-line bg-surface-2 transition-colors duration-150 hover:border-line-strong"
    >
      <div className="flex items-start gap-2 px-3 py-2">
        <div className="mt-0.5 shrink-0">
          <Badge tone={CATEGORY_TONE[memory.category]}>{memory.category}</Badge>
        </div>
        <div className="min-w-0 flex-1">
          <p className="whitespace-pre-wrap text-xs text-ink-0">{memory.content}</p>
          <p className="mt-1 text-[11px] text-ink-2">
            {memory.project_id && (
              <span className="mr-2">{strings.settings.memory.rowProject(memory.project_id)}</span>
            )}
            {memory.session_id && (
              <span className="mr-2">{strings.settings.memory.rowSession(memory.session_id)}</span>
            )}
            <span>{strings.settings.memory.rowUpdated(formatRelative(memory.updated_at))}</span>
          </p>
        </div>
        <IconButton
          data-testid={`settings-memory-delete-${memory.id}`}
          onClick={onDelete}
          aria-label={strings.settings.memory.deleteAria}
        >
          <Trash2 />
        </IconButton>
      </div>
    </li>
  );
}
