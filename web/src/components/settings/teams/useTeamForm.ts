/**
 * useTeamForm — create-form state for the Teams tab.
 *
 * Owns the new-team draft fields, the agent-selection toggle list, and
 * the create mutation. The tab wires the result into `TeamForm`.
 */
import { useState } from "react";
import { useTeamStore } from "../../../stores";
import { toast } from "../../layout/ErrorBoundary";
import type { OrchestrationMode } from "../../../types/ipc";
import { TEAM_COLORS } from "./constants";

export function useTeamForm() {
  const create = useTeamStore((s) => s.create);

  const [showForm, setShowForm] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formMode, setFormMode] = useState<OrchestrationMode>("parallel");
  const [formColor, setFormColor] = useState(TEAM_COLORS[0]);
  const [formAgents, setFormAgents] = useState<string[]>([]);

  const resetForm = () => {
    setFormName(""); setFormDescription(""); setFormMode("parallel");
    setFormColor(TEAM_COLORS[0]); setFormAgents([]);
    setShowForm(false);
  };

  const toggleForm = () => setShowForm((v) => !v);

  const toggleAgent = (name: string) => {
    setFormAgents((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name],
    );
  };

  const canSubmit = Boolean(formName.trim());

  const handleCreate = async () => {
    if (!canSubmit) return;
    const t = await create({
      name: formName.trim(),
      description: formDescription.trim(),
      color: formColor,
      agents: formAgents,
      orchestration_mode: formMode,
    });
    if (t) {
      toast.success("Team created", t.name);
      resetForm();
    }
  };

  return {
    showForm,
    formName, setFormName,
    formDescription, setFormDescription,
    formMode, setFormMode,
    formColor, setFormColor,
    formAgents,
    canSubmit,
    resetForm,
    toggleForm,
    toggleAgent,
    handleCreate,
  };
}

export type TeamFormState = ReturnType<typeof useTeamForm>;
