/**
 * TeamRow — one team card in the Teams tab list.
 *
 * v1.1.0: adds an inline "run this team" form (Play button expands a
 * request textarea) so a team run can be started without typing an
 * @team: mention in the chat.
 */
import { useState } from "react";
import { Play, Trash2, Users, X } from "lucide-react";
import { Badge, Button, IconButton } from "../../../ui";
import { strings } from "../../../ui/strings";
import type { AgentTeam } from "../../../types/ipc";

export interface TeamRowProps {
  team: AgentTeam;
  onToggleEnabled: () => void;
  onDelete: () => void;
  /** Start a team run with the given request text. */
  onRun: (request: string) => void;
}

export function TeamRow({ team, onToggleEnabled, onDelete, onRun }: TeamRowProps): JSX.Element {
  const [showRunForm, setShowRunForm] = useState(false);
  const [request, setRequest] = useState("");

  const submitRun = () => {
    const trimmed = request.trim();
    if (!trimmed) return;
    onRun(trimmed);
    setShowRunForm(false);
    setRequest("");
  };

  return (
    <li
      data-testid={`settings-team-row-${team.name}`}
      className="rounded-lg border border-line bg-surface-2 p-3 transition-colors duration-150 hover:border-line-strong"
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <span
            className="h-3 w-3 shrink-0 rounded-full"
            style={{ backgroundColor: team.color || "#6366f1" }}
          />
          <Users size={14} className="shrink-0 text-accent" />
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="truncate text-xs font-medium text-ink-0">{team.name}</span>
              <Badge tone="accent">{team.orchestration_mode}</Badge>
              <Badge tone={team.enabled ? "success" : "neutral"} dot>
                {team.enabled ? strings.settings.teams.enabled : strings.settings.teams.disabled}
              </Badge>
            </div>
            {team.description && (
              <p className="mt-0.5 truncate text-[11px] text-ink-2">{team.description}</p>
            )}
            {team.agents.length > 0 && (
              <div className="mt-1 flex flex-wrap gap-1">
                {team.agents.map((a) => (
                  <span
                    key={a}
                    className="rounded-md border border-line bg-surface-1 px-1.5 py-0.5 text-[11px] text-ink-0"
                  >
                    {a}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="ml-2 flex shrink-0 items-center gap-1">
          <IconButton
            data-testid={`settings-team-run-${team.name}`}
            onClick={() => setShowRunForm((v) => !v)}
            aria-label={strings.settings.teams.runAria(team.name)}
            title={strings.settings.teams.runAria(team.name)}
          >
            {showRunForm ? <X /> : <Play />}
          </IconButton>
          <Button
            size="sm"
            variant="ghost"
            data-testid={`settings-team-toggle-${team.name}`}
            onClick={onToggleEnabled}
          >
            {team.enabled ? strings.settings.teams.disable : strings.settings.teams.enable}
          </Button>
          <IconButton
            data-testid={`settings-team-delete-${team.name}`}
            onClick={onDelete}
            aria-label={strings.settings.teams.deleteAria(team.name)}
          >
            <Trash2 />
          </IconButton>
        </div>
      </div>

      {showRunForm && (
        <div data-testid={`settings-team-runform-${team.name}`} className="mt-2 space-y-2 border-t border-line pt-2">
          <label className="text-[11px] font-medium text-ink-1" htmlFor={`team-run-request-${team.name}`}>
            {strings.settings.teams.runFormTitle} — {strings.settings.teams.runRequestLabel}
          </label>
          <textarea
            id={`team-run-request-${team.name}`}
            data-testid={`settings-team-run-input-${team.name}`}
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            placeholder={strings.settings.teams.runRequestPlaceholder}
            rows={2}
            className="w-full resize-none rounded-md border border-line bg-surface-1 px-2 py-1.5 text-xs text-ink-0 placeholder:text-ink-2 focus:border-accent/50 focus:outline-none"
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setShowRunForm(false)}>
              {strings.settings.teams.runCancel}
            </Button>
            <Button
              size="sm"
              variant="subtle"
              data-testid={`settings-team-run-submit-${team.name}`}
              disabled={!request.trim()}
              onClick={submitRun}
            >
              {strings.settings.teams.runSubmit}
            </Button>
          </div>
        </div>
      )}
    </li>
  );
}
