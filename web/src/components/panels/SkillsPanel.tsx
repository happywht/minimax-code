/**
 * Skills panel — full-page view that lists every installed skill
 * with a per-row enable / disable toggle. The panel is mounted by
 * `App.tsx` when the sidebar nav switches to the "skills" view.
 *
 * Users can import an instruction-only or tool-referencing SKILL.md
 * directly from disk; the Agent validates and stores it in user data.
 */
import { useEffect, useRef, useState } from "react";
import { FileUp, Trash2, Wrench, X } from "lucide-react";
import { useSkillStore } from "../../stores";
import { Badge, Button, EmptyState, IconButton, Modal, Spinner } from "../../ui";
import { toast } from "../layout/ErrorBoundary";

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
  const [removeId, setRemoveId] = useState<string | null>(null);
  const skillToRemove = removeId ? skills.find((s) => s.id === removeId) : undefined;

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section
      data-testid={testId}
      role={onClose ? "dialog" : undefined}
      aria-modal={onClose ? true : undefined}
      aria-labelledby="skills-title"
      className="flex h-full w-full flex-col overflow-hidden bg-surface-0 text-ink-0"
    >
      <header className="flex items-center justify-between border-b border-line px-6 py-4">
        <div>
          <h1
            data-testid="skills-title"
            className="flex items-center gap-2 text-base font-semibold"
          >
            <Wrench size={16} className="text-accent" />
            Skills
          </h1>
          <p className="mt-1 text-[11px] text-ink-2">
            Enable or disable installed skills. Built-in skills ship
            with the agent; import custom skills from a local SKILL.md.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            data-testid="skills-add"
            loading={installing}
            icon={<FileUp />}
            title="Import a SKILL.md file"
            onClick={() => fileInputRef.current?.click()}
          >
            {installing ? "Importing..." : "Import"}
          </Button>
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
            <IconButton
              data-testid="skills-close"
              aria-label="Close skills"
              onClick={onClose}
            >
              <X />
            </IconButton>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {loading && skills.length === 0 ? (
          <p data-testid="skills-loading" className="flex items-center gap-2 text-sm text-ink-1">
            <Spinner size={14} />
            Loading skills…
          </p>
        ) : skills.length === 0 ? (
          <EmptyState
            testId="skills-empty"
            title="No skills installed"
            hint="Import a local SKILL.md to add a custom skill."
          />
        ) : (
          <ul data-testid="skills-list" className="space-y-2">
            {skills.map((skill) => (
              <li
                key={skill.id}
                data-testid={`skills-row-${skill.id}`}
                data-enabled={skill.enabled ? "true" : "false"}
                className="flex items-start justify-between gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2.5 transition-colors duration-150 hover:border-line-strong"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-ink-0">
                      {skill.name}
                    </span>
                    {skill.builtin && (
                      <Badge tone="neutral" className="uppercase tracking-wider">
                        built-in
                      </Badge>
                    )}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-ink-1">
                    {skill.description}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <label
                    data-testid={`skills-toggle-${skill.id}`}
                    className="flex cursor-pointer items-center gap-1.5 text-[11px] text-ink-2"
                  >
                    <input
                      type="checkbox"
                      checked={skill.enabled}
                      onChange={(e) =>
                        void setEnabled(skill.id, e.target.checked)
                      }
                      className="h-3.5 w-3.5 cursor-pointer accent-accent"
                    />
                    <span>{skill.enabled ? "On" : "Off"}</span>
                  </label>
                  {!skill.builtin && (
                    <IconButton
                      data-testid={`skills-remove-${skill.id}`}
                      aria-label={`Remove skill ${skill.name}`}
                      title="Remove custom skill"
                      onClick={() => setRemoveId(skill.id)}
                      className="hover:bg-[var(--status-error-subtle)] hover:text-status-error"
                    >
                      <Trash2 />
                    </IconButton>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>

      {skillToRemove && (
        <Modal
          title="移除技能"
          testId="skills-remove-modal"
          onClose={() => setRemoveId(null)}
          footer={
            <>
              <Button variant="ghost" size="sm" onClick={() => setRemoveId(null)}>
                取消
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={() => {
                  void remove(removeId!);
                  setRemoveId(null);
                }}
              >
                移除
              </Button>
            </>
          }
        >
          <p className="text-sm text-ink-0">
            移除自定义技能「<span className="font-medium">{skillToRemove.name}</span>」？
          </p>
          <p className="mt-1 text-xs text-ink-2">移除后可在需要时重新导入。</p>
        </Modal>
      )}
    </section>
  );
}
