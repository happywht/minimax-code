/**
 * AgentTeamPanel — the "Agents" inspector tab: a Panel card with a
 * unified header (title + refresh action) wrapping the agent roster.
 */
import { RefreshCw } from "lucide-react";
import { IconButton, Panel } from "../../ui";
import type { AgentInfo } from "../../types/ipc";
import { strings } from "../../ui/strings";
import { AgentTeamList } from "./AgentTeamList";

export interface AgentTeamPanelProps {
  testId: string;
  agents: AgentInfo[] | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

export function AgentTeamPanel({
  testId,
  agents,
  loading,
  error,
  onRefresh,
}: AgentTeamPanelProps): JSX.Element {
  return (
    <section
      id={`${testId}-agents-panel`}
      role="tabpanel"
      data-testid={`${testId}-team-body`}
      className="p-2"
    >
      <Panel
        title={strings.rightPanel.agents.teamTitle}
        flush
        actions={
          <IconButton
            aria-label={strings.rightPanel.agents.refreshAria}
            size="sm"
            data-testid={`${testId}-team-refresh`}
            onClick={onRefresh}
          >
            <RefreshCw className={loading ? "animate-spin" : ""} />
          </IconButton>
        }
      >
        <AgentTeamList
          agents={agents}
          loading={loading}
          error={error}
          testId={`${testId}-team`}
        />
      </Panel>
    </section>
  );
}
