/**
 * useAgentTeam — loads the sub-agent roster for the Agent Team tab.
 *
 * Tests can bypass IPC entirely via `initialAgents` (static data) or
 * `loadAgents` (custom loader). Otherwise the roster is pulled from
 * `agent.list` on mount and whenever the refresh action fires.
 */
import { useCallback, useEffect, useState } from "react";
import { typedIPC } from "../../ipc";
import type { AgentInfo } from "../../types/ipc";

export interface AgentTeamState {
  agents: AgentInfo[] | null;
  agentsLoading: boolean;
  agentsError: string | null;
  fetchAgents: () => Promise<void>;
}

export function useAgentTeam(
  initialAgents: AgentInfo[] | undefined,
  loadAgents: (() => Promise<AgentInfo[]>) | undefined,
): AgentTeamState {
  const [agents, setAgents] = useState<AgentInfo[] | null>(initialAgents ?? null);
  const [agentsLoading, setAgentsLoading] = useState<boolean>(!initialAgents);
  const [agentsError, setAgentsError] = useState<string | null>(null);

  const fetchAgents = useCallback(async () => {
    setAgentsLoading(true);
    setAgentsError(null);
    try {
      const loader = loadAgents ?? (() => typedIPC.listAgents().then((r) => r.agents));
      const list = await loader();
      setAgents(list);
    } catch (err) {
      setAgentsError(err instanceof Error ? err.message : String(err));
      setAgents([]);
    } finally {
      setAgentsLoading(false);
    }
  }, [loadAgents]);

  useEffect(() => {
    if (initialAgents) return; // tests supply static data
    void fetchAgents();
  }, [initialAgents, fetchAgents]);

  return { agents, agentsLoading, agentsError, fetchAgents };
}
