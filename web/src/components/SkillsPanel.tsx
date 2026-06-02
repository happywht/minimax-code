/**
 * Skills panel — full-page view that lists every installed skill
 * with a per-row enable / disable toggle. The panel is mounted by
 * `App.tsx` when the sidebar nav switches to the "skills" view.
 *
 * The "+" button at the top is intentionally disabled in this
 * iteration — the real "install skill" workflow (drag-drop a folder,
 * pick from registry, etc.) lives in a follow-up.
 */
import { useEffect } from "react";
import { Plus, Wrench } from "lucide-react";
import { useSkillStore } from "../stores";

export interface SkillsPanelProps {
  testId?: string;
}

export function SkillsPanel({ testId = "skills-panel" }: SkillsPanelProps): JSX.Element {
  const skills = useSkillStore((s) => s.skills);
  const loading = useSkillStore((s) => s.loading);
  const refresh = useSkillStore((s) => s.refresh);
  const setEnabled = useSkillStore((s) => s.setEnabled);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section
      data-testid={testId}
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
            with the agent; custom skills can be added later.
          </p>
        </div>
        <button
          type="button"
          data-testid="skills-add"
          disabled
          title="Install skill (coming soon)"
          className="flex items-center gap-1.5 rounded-md border border-minimax-border bg-minimax-panel/60 px-2.5 py-1.5 text-xs text-minimax-muted opacity-60"
        >
          <Plus size={12} />
          <span>Add skill</span>
        </button>
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
                      <span className="rounded bg-minimax-border px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-minimax-muted">
                        built-in
                      </span>
                    )}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[11px] text-minimax-muted">
                    {skill.description}
                  </p>
                </div>
                <label
                  data-testid={`skills-toggle-${skill.id}`}
                  className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[11px] text-minimax-muted"
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
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
