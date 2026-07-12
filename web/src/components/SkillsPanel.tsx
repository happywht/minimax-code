/**
 * Skills panel — full-page view that lists every installed skill
 * with a per-row enable / disable toggle. The panel is mounted by
 * `App.tsx` when the sidebar nav switches to the "skills" view.
 *
 * Users can import an instruction-only or tool-referencing SKILL.md
 * directly from disk; the Agent validates and stores it in user data.
 */
import { useEffect, useRef } from "react";
import { FileUp, Loader2, Trash2, Wrench, X } from "lucide-react";
import { useSkillStore } from "../stores";
import { toast } from "./ErrorBoundary";

function readTextFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(reader.error ?? new Error("Unable to read file"));
    reader.readAsText(file, "utf-8");
  });
}

export interface SkillsPanelProps {
  testId?: string;
  onClose?: () => void;
}

export function SkillsPanel({ testId = "skills-panel", onClose }: SkillsPanelProps): JSX.Element {
  const skills = useSkillStore((s) => s.skills);
  const loading = useSkillStore((s) => s.loading);
  const installing = useSkillStore((s) => s.installing);
  const refresh = useSkillStore((s) => s.refresh);
  const install = useSkillStore((s) => s.install);
  const remove = useSkillStore((s) => s.remove);
  const setEnabled = useSkillStore((s) => s.setEnabled);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section
      data-testid={testId}
      role={onClose ? "dialog" : undefined}
      aria-modal={onClose ? true : undefined}
      aria-labelledby="skills-title"
      className="flex h-full w-full flex-col overflow-hidden bg-minimax-bg text-minimax-fg"
    >
      <header className="flex items-center justify-between border-b border-minimax-border px-6 py-4">
        <div>
          <h1
            data-testid="skills-title"
            className="flex items-center gap-2 text-base font-semibold"
          >
            <Wrench size={16} className="text-minimax-accent" />
            Skills
          </h1>
          <p className="mt-1 text-[11px] text-minimax-muted">
            Enable or disable installed skills. Built-in skills ship
            with the agent; import custom skills from a local SKILL.md.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            data-testid="skills-add"
            disabled={installing}
            title="Import a SKILL.md file"
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-1.5 rounded-md border border-minimax-border bg-minimax-panel/60 px-2.5 py-1.5 text-xs text-minimax-fg hover:border-minimax-accent/50 disabled:cursor-wait disabled:opacity-60"
          >
            {installing ? <Loader2 size={12} className="animate-spin" /> : <FileUp size={12} />}
            <span>{installing ? "Importing..." : "Import"}</span>
          </button>
          <input
            ref={fileInputRef}
            data-testid="skills-file-input"
            type="file"
            accept=".md,text/markdown,text/plain"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              void readTextFile(file)
                .then((content) => install(content))
                .catch((error) => {
                  const message = error instanceof Error ? error.message : String(error);
                  toast.error("Failed to read skill file", message);
                });
            }}
          />
          {onClose && (
            <button
              type="button"
              data-testid="skills-close"
              aria-label="Close skills"
              onClick={onClose}
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-minimax-muted transition-colors duration-200 hover:bg-minimax-border hover:text-minimax-fg"
            >
              <X size={14} />
            </button>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {loading && skills.length === 0 ? (
          <p data-testid="skills-loading" className="text-sm text-minimax-muted">
            Loading skills…
          </p>
        ) : skills.length === 0 ? (
          <p
            data-testid="skills-empty"
            className="rounded-md border border-dashed border-minimax-border bg-minimax-panel/40 px-4 py-6 text-center text-sm italic text-minimax-muted"
          >
            No skills installed
          </p>
        ) : (
          <ul data-testid="skills-list" className="space-y-2">
            {skills.map((skill) => (
              <li
                key={skill.id}
                data-testid={`skills-row-${skill.id}`}
                data-enabled={skill.enabled ? "true" : "false"}
                className="flex items-start justify-between gap-3 rounded-md border border-minimax-border bg-minimax-panel/40 px-3 py-2.5"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-minimax-fg">
                      {skill.name}
                    </span>
                    {skill.builtin && (
                      <span className="rounded bg-minimax-border px-1.5 py-0.5 text-[11px] uppercase tracking-wider text-minimax-muted">
                        built-in
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-minimax-muted">
                    {skill.description}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <label
                    data-testid={`skills-toggle-${skill.id}`}
                    className="flex cursor-pointer items-center gap-1.5 text-[11px] text-minimax-muted"
                  >
                    <input
                      type="checkbox"
                      checked={skill.enabled}
                      onChange={(e) =>
                        void setEnabled(skill.id, e.target.checked)
                      }
                      className="h-3.5 w-3.5 cursor-pointer accent-minimax-accent"
                    />
                    <span>{skill.enabled ? "On" : "Off"}</span>
                  </label>
                  {!skill.builtin && (
                    <button
                      type="button"
                      data-testid={`skills-remove-${skill.id}`}
                      aria-label={`Remove skill ${skill.name}`}
                      title="Remove custom skill"
                      onClick={() => {
                        if (window.confirm(`Remove custom skill ${skill.name}?`)) {
                          void remove(skill.id);
                        }
                      }}
                      className="flex h-7 w-7 items-center justify-center rounded-md text-minimax-muted hover:bg-red-500/10 hover:text-status-error"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
