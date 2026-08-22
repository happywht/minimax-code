/**
 * StepEditor — inline editor for one workflow's step list.
 *
 * Lets the user add/remove/reorder steps and edit each step's config.
 * Action steps cover all four backend action types (notify /
 * send-message / code-review / run-skill); condition steps edit the
 * field/op/value clause plus then/else jump targets. The whole list is
 * saved back through ``workflow.update``.
 */
import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, GitBranch, Plus, Trash2, Zap } from "lucide-react";
import { Badge, Button, IconButton, Input } from "../../../ui";
import { strings } from "../../../ui/strings";
import { Field, Select } from "../fields";
import { toast } from "../../layout/ErrorBoundary";
import type { WorkflowEntry, WorkflowStep } from "../../../types/ipc";

export interface StepEditorProps {
  workflow: WorkflowEntry;
  onSave: (steps: WorkflowStep[]) => Promise<void>;
}

type ActionType = NonNullable<WorkflowStep["action_type"]>;
type CondOp = NonNullable<WorkflowStep["if"]>["op"];

const ACTION_TYPES: ActionType[] = ["notify", "send-message", "code-review", "run-skill"];
const COND_OPS: CondOp[] = ["eq", "neq", "contains", "startswith"];

const ACTION_LABELS: Record<ActionType, string> = {
  notify: "发送通知",
  "send-message": "发送消息",
  "code-review": "代码审查",
  "run-skill": "运行技能",
};

const OP_LABELS: Record<CondOp, string> = {
  eq: strings.settings.workflows.condOpEq,
  neq: strings.settings.workflows.condOpNeq,
  contains: strings.settings.workflows.condOpContains,
  startswith: strings.settings.workflows.condOpStartswith,
};

/** Sensible initial config when a step's action type is created/switched. */
function defaultConfigFor(actionType: ActionType): Record<string, unknown> {
  switch (actionType) {
    case "notify":
      return { title: "", body: "" };
    case "send-message":
      return { message_template: "" };
    case "run-skill":
      return { skill_id: "" };
    default:
      return {};
  }
}

function cloneSteps(steps: WorkflowStep[]): WorkflowStep[] {
  return steps.map((s) => ({
    ...s,
    if: s.if ? { ...s.if } : undefined,
    config: s.config ? { ...s.config } : undefined,
  }));
}

// ── Action step fields ─────────────────────────────────────────────────────

function ActionFields({
  wfId,
  index,
  step,
  onType,
  onConfig,
}: {
  wfId: string;
  index: number;
  step: WorkflowStep;
  onType: (t: ActionType) => void;
  onConfig: (key: string, value: unknown) => void;
}): JSX.Element {
  const p = `${wfId}-${index}`;
  const actionType = (step.action_type ?? "notify") as ActionType;
  const cfg = step.config ?? {};
  const strNum = (key: string): string => String(cfg[key] ?? "");

  return (
    <div className="space-y-2">
      <Field label={strings.settings.workflows.fieldActionType} htmlFor={`step-${p}-type`}>
        <Select
          id={`step-${p}-type`}
          name={`step-${p}-type`}
          data-testid={`workflow-step-type-${p}`}
          value={actionType}
          onChange={(e) => onType(e.target.value as ActionType)}
        >
          {ACTION_TYPES.map((t) => (
            <option key={t} value={t}>
              {ACTION_LABELS[t]}
            </option>
          ))}
        </Select>
      </Field>

      {actionType === "notify" && (
        <>
          <Field label={strings.settings.workflows.configTitle} htmlFor={`step-${p}-title`}>
            <Input
              id={`step-${p}-title`}
              name={`step-${p}-title`}
              data-testid={`workflow-step-cfg-${p}-title`}
              placeholder={strings.settings.workflows.configTitlePlaceholder}
              value={strNum("title")}
              onChange={(e) => onConfig("title", e.target.value)}
            />
          </Field>
          <Field label={strings.settings.workflows.configBody} htmlFor={`step-${p}-body`}>
            <Input
              id={`step-${p}-body`}
              name={`step-${p}-body`}
              data-testid={`workflow-step-cfg-${p}-body`}
              value={strNum("body")}
              onChange={(e) => onConfig("body", e.target.value)}
            />
          </Field>
          <Field label={strings.settings.workflows.configPriority} htmlFor={`step-${p}-priority`}>
            <Input
              id={`step-${p}-priority`}
              name={`step-${p}-priority`}
              data-testid={`workflow-step-cfg-${p}-priority`}
              type="number"
              value={strNum("priority")}
              onChange={(e) => onConfig("priority", Number(e.target.value || 0))}
            />
          </Field>
        </>
      )}

      {actionType === "send-message" && (
        <>
          <Field label={strings.settings.workflows.configTemplate} htmlFor={`step-${p}-template`}>
            <Input
              id={`step-${p}-template`}
              name={`step-${p}-template`}
              data-testid={`workflow-step-cfg-${p}-template`}
              placeholder={strings.settings.workflows.configTemplatePlaceholder}
              value={strNum("message_template")}
              onChange={(e) => onConfig("message_template", e.target.value)}
            />
          </Field>
          <Field label={strings.settings.workflows.configPriority} htmlFor={`step-${p}-priority`}>
            <Input
              id={`step-${p}-priority`}
              name={`step-${p}-priority`}
              data-testid={`workflow-step-cfg-${p}-priority`}
              type="number"
              value={strNum("priority")}
              onChange={(e) => onConfig("priority", Number(e.target.value || 0))}
            />
          </Field>
        </>
      )}

      {actionType === "run-skill" && (
        <Field label={strings.settings.workflows.configSkillId} htmlFor={`step-${p}-skill`}>
          <Input
            id={`step-${p}-skill`}
            name={`step-${p}-skill`}
            data-testid={`workflow-step-cfg-${p}-skill`}
            placeholder={strings.settings.workflows.configSkillIdPlaceholder}
            value={strNum("skill_id")}
            onChange={(e) => onConfig("skill_id", e.target.value)}
          />
        </Field>
      )}

      {actionType === "code-review" && (
        <p className="text-[11px] text-ink-2">{strings.settings.workflows.codeReviewHint}</p>
      )}
    </div>
  );
}

// ── Condition step fields ──────────────────────────────────────────────────

function ConditionFields({
  wfId,
  index,
  step,
  onCond,
  onJump,
}: {
  wfId: string;
  index: number;
  step: WorkflowStep;
  onCond: (next: Partial<NonNullable<WorkflowStep["if"]>>) => void;
  onJump: (key: "then_step" | "else_step", value: number) => void;
}): JSX.Element {
  const p = `${wfId}-${index}`;
  const cond = step.if ?? { field: "", op: "eq" as CondOp, value: "" };

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-6">
        <Field
          label={strings.settings.workflows.condField}
          htmlFor={`step-${p}-field`}
          className="sm:col-span-3"
        >
          <Input
            id={`step-${p}-field`}
            name={`step-${p}-field`}
            data-testid={`workflow-step-cond-${p}-field`}
            placeholder={strings.settings.workflows.condFieldPlaceholder}
            value={cond.field}
            onChange={(e) => onCond({ field: e.target.value })}
          />
        </Field>
        <Field label={strings.settings.workflows.condOp} htmlFor={`step-${p}-op`} className="sm:col-span-3">
          <Select
            id={`step-${p}-op`}
            name={`step-${p}-op`}
            data-testid={`workflow-step-cond-${p}-op`}
            value={cond.op}
            onChange={(e) => onCond({ op: e.target.value as CondOp })}
          >
            {COND_OPS.map((op) => (
              <option key={op} value={op}>
                {OP_LABELS[op]}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label={strings.settings.workflows.condValue} htmlFor={`step-${p}-value`}>
        <Input
          id={`step-${p}-value`}
          name={`step-${p}-value`}
          data-testid={`workflow-step-cond-${p}-value`}
          value={cond.value}
          onChange={(e) => onCond({ value: e.target.value })}
        />
      </Field>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Field label={strings.settings.workflows.condThen} htmlFor={`step-${p}-then`}>
          <Input
            id={`step-${p}-then`}
            name={`step-${p}-then`}
            data-testid={`workflow-step-cond-${p}-then`}
            type="number"
            value={step.then_step ?? -1}
            onChange={(e) => onJump("then_step", Number(e.target.value || 0))}
          />
        </Field>
        <Field label={strings.settings.workflows.condElse} htmlFor={`step-${p}-else`}>
          <Input
            id={`step-${p}-else`}
            name={`step-${p}-else`}
            data-testid={`workflow-step-cond-${p}-else`}
            type="number"
            value={step.else_step ?? -1}
            onChange={(e) => onJump("else_step", Number(e.target.value || 0))}
          />
        </Field>
      </div>
    </div>
  );
}

// ── Main editor ────────────────────────────────────────────────────────────

export function StepEditor({ workflow, onSave }: StepEditorProps): JSX.Element {
  const [steps, setSteps] = useState<WorkflowStep[]>(() => cloneSteps(workflow.steps));
  const [saving, setSaving] = useState(false);

  const dirty = useMemo(
    () => JSON.stringify(steps) !== JSON.stringify(workflow.steps),
    [steps, workflow.steps],
  );

  const patch = (i: number, next: Partial<WorkflowStep>) =>
    setSteps((s) => s.map((st, idx) => (idx === i ? { ...st, ...next } : st)));

  const patchConfig = (i: number, key: string, value: unknown) =>
    setSteps((s) =>
      s.map((st, idx) => (idx === i ? { ...st, config: { ...st.config, [key]: value } } : st)),
    );

  const patchCond = (i: number, next: Partial<NonNullable<WorkflowStep["if"]>>) =>
    setSteps((s) =>
      s.map((st, idx) =>
        idx === i ? { ...st, if: { field: "", op: "eq", value: "", ...st.if, ...next } } : st,
      ),
    );

  const move = (i: number, delta: number) =>
    setSteps((s) => {
      const j = i + delta;
      if (j < 0 || j >= s.length) return s;
      const next = [...s];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  const addAction = () =>
    setSteps((s) => [
      ...s,
      { type: "action", action_type: "notify", config: defaultConfigFor("notify") },
    ]);

  const addCondition = () =>
    setSteps((s) => [...s, { type: "condition", if: { field: "", op: "eq", value: "" } }]);

  const removeStep = (i: number) => setSteps((s) => s.filter((_, idx) => idx !== i));

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(steps);
      toast.success(strings.toasts.workflowStepsSaved);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div data-testid={`workflow-steps-${workflow.id}`} className="space-y-2 border-t border-line pt-2">
      <h4 className="text-[11px] font-semibold text-ink-0">
        {strings.settings.workflows.stepsEditorTitle}
      </h4>
      <p className="text-[11px] text-ink-2">{strings.settings.workflows.stepsHint}</p>

      {steps.map((step, i) => (
        <div
          key={i}
          data-testid={`workflow-step-${workflow.id}-${i}`}
          className="space-y-2 rounded-md border border-line bg-surface-1 p-2"
        >
          <div className="flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <span className="text-[11px] text-ink-2">#{i}</span>
              {step.type === "action" ? (
                <Zap size={11} className="text-accent" />
              ) : (
                <GitBranch size={11} className="text-ink-1" />
              )}
              <Badge tone={step.type === "action" ? "accent" : "neutral"}>
                {step.type === "action"
                  ? strings.settings.workflows.stepAction
                  : strings.settings.workflows.stepCondition}
              </Badge>
              {step.type === "action" && (
                <span className="text-[11px] text-ink-2">
                  {ACTION_LABELS[(step.action_type ?? "notify") as ActionType]}
                </span>
              )}
            </span>
            <span className="flex items-center gap-0.5">
              <IconButton
                aria-label={strings.settings.workflows.moveUpAria}
                disabled={i === 0}
                onClick={() => move(i, -1)}
              >
                <ArrowUp />
              </IconButton>
              <IconButton
                aria-label={strings.settings.workflows.moveDownAria}
                disabled={i === steps.length - 1}
                onClick={() => move(i, 1)}
              >
                <ArrowDown />
              </IconButton>
              <IconButton
                aria-label={strings.settings.workflows.deleteStepAria}
                onClick={() => removeStep(i)}
              >
                <Trash2 />
              </IconButton>
            </span>
          </div>

          {step.type === "action" ? (
            <ActionFields
              wfId={workflow.id}
              index={i}
              step={step}
              onType={(t) => patch(i, { action_type: t, config: defaultConfigFor(t) })}
              onConfig={(k, v) => patchConfig(i, k, v)}
            />
          ) : (
            <ConditionFields
              wfId={workflow.id}
              index={i}
              step={step}
              onCond={(next) => patchCond(i, next)}
              onJump={(k, v) => patch(i, { [k]: v } as Partial<WorkflowStep>)}
            />
          )}
        </div>
      ))}

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            data-testid={`workflow-step-add-action-${workflow.id}`}
            onClick={addAction}
            icon={<Plus />}
          >
            {strings.settings.workflows.addAction}
          </Button>
          <Button
            size="sm"
            variant="secondary"
            data-testid={`workflow-step-add-condition-${workflow.id}`}
            onClick={addCondition}
            icon={<Plus />}
          >
            {strings.settings.workflows.addCondition}
          </Button>
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="ghost"
            disabled={!dirty || saving}
            onClick={() => setSteps(cloneSteps(workflow.steps))}
          >
            {strings.settings.workflows.stepsRevert}
          </Button>
          <Button
            size="sm"
            variant="primary"
            data-testid={`workflow-steps-save-${workflow.id}`}
            disabled={!dirty || saving}
            onClick={() => void handleSave()}
          >
            {strings.settings.workflows.stepsSave}
          </Button>
        </div>
      </div>
    </div>
  );
}
