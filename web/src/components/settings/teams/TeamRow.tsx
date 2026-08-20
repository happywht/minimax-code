/**
 * TeamRow — one team card in the Teams tab list.
 */
import { Trash2, Users } from "lucide-react";
import { Badge, Button, IconButton } from "../../../ui";
import { strings } from "../../../ui/strings";
import type { AgentTeam } from "../../../types/ipc";

export interface TeamRowProps {
  team: AgentTeam;
  onToggleEnabled: () => void;
  onDelete: () => void;
}

export function TeamRow({ team, onToggleEnabled, onDelete }: TeamRowProps): JSX.Element {
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
    </li>
  );
}
