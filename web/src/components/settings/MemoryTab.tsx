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
import { Button, IconButton, Input, Panel, Textarea } from "../../ui";
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
  const searchQuery = useMemoryStore((s) => s.searchQuery);
  const refresh = useMemoryStore((s) => s.refresh);
  const search = useMemoryStore((s) => s.search);
  const add = useMemoryStore((s) => s.add);
  const remove = useMemoryStore((s) => s.remove);
  const setSearchQuery = useMemoryStore((s) => s.setSearchQuery);

  const [draftContent, setDraftContent] = useState("");
  const [draftCategory, setDraftCategory] = useState<MemoryCategory>("fact");

  useEffect(() => {
    if (memories.length === 0 && !searchQuery) void refresh();
  }, [memories.length, searchQuery, refresh]);

  const handleSearch = (query: string) => {
    setSearchQuery(query);
    void search(query);
  };

  const handleAdd = async () => {
    const content = draftContent.trim();
    if (!content) {
      toast.error("Content required", "Please enter a memory before adding.");
      return;
    }
    const memory = await add({ content, category: draftCategory });
    if (memory) {
      toast.success("Memory added");
      setDraftContent("");
      setDraftCategory("fact");
    }
  };

  const handleDelete = async (memory: MemoryEntry) => {
    const accepted = await requestConfirmation({
      title: "Delete this memory?",
      description: memory.content.slice(0, 120) + (memory.content.length > 120 ? "…" : ""),
      confirmLabel: "Delete",
    });
    if (accepted) await remove(memory.id);
  };

  return (
    <section data-testid="settings-memory" className="space-y-4">
      <TabHeader
        title="Long-term memory"
        hint={
          <>
            Facts, preferences, decisions and lessons the agent recalls for matching projects or
            sessions. Inject via the <InlineCode>## Relevant memories</InlineCode> system prompt
            block.
          </>
        }
      />

      <Panel title="Add memory">
        <div className="space-y-3">
          <Textarea
            data-testid="settings-memory-content"
            value={draftContent}
            onChange={(e) => setDraftContent(e.target.value)}
            placeholder="e.g. Prefer TypeScript strict mode; always add tests for new IPC handlers…"
            className="min-h-[80px]"
          />
          <div className="flex items-end gap-2">
            <div className="w-40">
              <label htmlFor="memory-category" className="mb-1 block text-[11px] text-ink-2">
                Category
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
            <Button
              size="sm"
              variant="subtle"
              data-testid="settings-memory-add"
              disabled={!draftContent.trim()}
              onClick={() => void handleAdd()}
              icon={<Plus />}
            >
              Add
            </Button>
          </div>
        </div>
      </Panel>

      <div className="relative">
        <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-2" />
        <Input
          data-testid="settings-memory-search"
          value={searchQuery}
          onChange={(e) => handleSearch(e.target.value)}
          placeholder="Search memories…"
          className="pl-8 pr-8"
        />
        {searchQuery && (
          <IconButton
            className="absolute right-1 top-1/2 -translate-y-1/2"
            aria-label="Clear search"
            onClick={() => handleSearch("")}
          >
            <X />
          </IconButton>
        )}
      </div>

      <div className="flex items-center gap-2 text-[11px] text-ink-2">
        <Brain size={12} />
        <span data-testid="settings-memory-count">
          {total} memory{total === 1 ? "" : "ies"}
        </span>
      </div>

      <ul className="space-y-1.5" data-testid="settings-memory-list">
        {memories.length === 0 && !loading && (
          <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">
            {searchQuery ? "No memories match your search" : "No memories yet"}
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
              <span className="mr-2">project: {memory.project_id}</span>
            )}
            {memory.session_id && (
              <span className="mr-2">session: {memory.session_id}</span>
            )}
            <span>updated {formatRelative(memory.updated_at)}</span>
          </p>
        </div>
        <IconButton
          data-testid={`settings-memory-delete-${memory.id}`}
          onClick={onDelete}
          aria-label="Delete memory"
        >
          <Trash2 />
        </IconButton>
      </div>
    </li>
  );
}
