/**
 * Permissions tab — list / upsert / remove permission rules.
 */
import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { Badge, Button, IconButton, Input, Panel } from "../../ui";
import { strings } from "../../ui/strings";
import { usePermissionStore } from "../../stores";
import { toast } from "../layout/ErrorBoundary";
import type { PermissionRule } from "../../types/ipc";
import { required, regexFormat, compose } from "../../lib/validators";
import { requestConfirmation } from "../modals/ConfirmationDialog";
import { Field, InlineCode, Select, TabHeader } from "./fields";

export { PermissionsTab };

function PermissionsTab(): JSX.Element {
  const rules = usePermissionStore((s) => s.rules);
  const refresh = usePermissionStore((s) => s.refresh);
  const upsertRule = usePermissionStore((s) => s.upsertRule);
  const removeRule = usePermissionStore((s) => s.removeRule);
  const [draftTool, setDraftTool] = useState("*");
  const [draftPattern, setDraftPattern] = useState("");
  const [draftDecision, setDraftDecision] = useState<"allow" | "deny" | "ask">("allow");
  const [errors, setErrors] = useState<{ tool?: string; pattern?: string }>({});

  useEffect(() => {
    if (rules.length === 0) void refresh();
  }, [rules.length, refresh]);

  const handleAdd = async () => {
    const toolErr = required(strings.settings.permissions.fieldTool)(draftTool);
    const patternErr = compose(
      required(strings.settings.permissions.fieldPattern),
      regexFormat(),
    )(draftPattern);
    if (toolErr || patternErr) {
      setErrors({ tool: toolErr || undefined, pattern: patternErr || undefined });
      return;
    }
    await upsertRule({ tool: draftTool.trim(), pattern: draftPattern.trim(), decision: draftDecision });
    toast.success(strings.settings.permissions.savedToast, `${draftTool} ${draftPattern} → ${draftDecision}`);
    setDraftPattern("");
    setErrors({});
  };

  return (
    <section data-testid="settings-permissions" className="space-y-4">
      <TabHeader
        title={strings.settings.permissions.title}
        hint={
          <>
            {strings.settings.permissions.hintLead}{" "}
            <InlineCode>allow</InlineCode>
            {strings.settings.permissions.hintMid1}{" "}
            <InlineCode>deny</InlineCode>
            {strings.settings.permissions.hintMid2}{" "}
            <InlineCode>ask</InlineCode>
            {strings.settings.permissions.hintTail}
          </>
        }
      />

      <Panel title={strings.settings.permissions.addTitle}>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-12">
          <Field label={strings.settings.permissions.fieldTool} htmlFor="permission-tool" className="sm:col-span-3" hint={errors.tool}>
            <Input
              id="permission-tool"
              name="permission-tool"
              autoComplete="off"
              spellCheck={false}
              data-testid="settings-permission-tool"
              value={draftTool}
              onChange={(e) => { setDraftTool(e.target.value); setErrors((prev) => ({ ...prev, tool: undefined })); }}
              placeholder={strings.settings.permissions.placeholderTool}
              className={errors.tool ? "border-status-error" : ""}
            />
          </Field>
          <Field label={strings.settings.permissions.fieldPattern} htmlFor="permission-pattern" className="sm:col-span-5" hint={errors.pattern}>
            <Input
              id="permission-pattern"
              name="permission-pattern"
              autoComplete="off"
              spellCheck={false}
              data-testid="settings-permission-pattern"
              value={draftPattern}
              onChange={(e) => { setDraftPattern(e.target.value); setErrors((prev) => ({ ...prev, pattern: undefined })); }}
              placeholder={strings.settings.permissions.placeholderPattern}
              className={"font-mono " + (errors.pattern ? "border-status-error" : "")}
            />
          </Field>
          <Field label={strings.settings.permissions.fieldDecision} htmlFor="permission-decision" className="sm:col-span-2">
            <Select
              id="permission-decision"
              name="permission-decision"
              data-testid="settings-permission-decision"
              value={draftDecision}
              onChange={(e) => setDraftDecision(e.target.value as "allow" | "deny" | "ask")}
            >
              <option value="allow">allow</option>
              <option value="deny">deny</option>
              <option value="ask">ask</option>
            </Select>
          </Field>
          <div className="flex items-end sm:col-span-2">
            <Button
              size="sm"
              variant="subtle"
              data-testid="settings-permission-add"
              disabled={!draftPattern.trim() || !draftTool.trim()}
              onClick={() => void handleAdd()}
              icon={<Plus />}
              className="w-full"
            >
              {strings.settings.permissions.add}
            </Button>
          </div>
        </div>
      </Panel>

      <ul className="space-y-1.5" data-testid="settings-permissions-list">
        {rules.length === 0 && (
          <li className="rounded-lg border border-dashed border-line px-3 py-4 text-center text-xs text-ink-2">
            {strings.settings.permissions.empty}
          </li>
        )}
        {rules.map((r) => (
          <PermissionRuleRow
            key={r.id}
            rule={r}
            onDelete={async () => {
              const accepted = await requestConfirmation({
                title: strings.settings.permissions.deleteTitle,
                description: strings.settings.permissions.deleteDesc(r.tool, r.pattern),
                confirmLabel: strings.settings.permissions.deleteLabel,
              });
              if (accepted) await removeRule(r.id);
            }}
            onUpdate={(decision) => void upsertRule({ id: r.id, tool: r.tool, pattern: r.pattern, decision })}
          />
        ))}
      </ul>
    </section>
  );
}

function PermissionRuleRow({ rule, onDelete, onUpdate }: {
  rule: PermissionRule; onDelete: () => void;
  onUpdate: (decision: "allow" | "deny" | "ask") => void;
}): JSX.Element {
  return (
    <li
      data-testid={`settings-permission-row-${rule.id}`}
      className="flex items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm transition-colors duration-150 hover:border-line-strong"
    >
      <Badge tone="neutral">
        {rule.tool}
        {rule.origin === "default" ? strings.settings.permissions.defaultBadge : ""}
      </Badge>
      <code className="min-w-0 flex-1 truncate font-mono text-xs text-ink-0">{rule.pattern}</code>
      <Select
        data-testid={`settings-permission-decision-${rule.id}`}
        value={rule.decision}
        aria-label={strings.settings.permissions.decisionAria(rule.tool, rule.pattern)}
        onChange={(e) => onUpdate(e.target.value as "allow" | "deny" | "ask")}
        className="w-auto shrink-0"
      >
        <option value="allow">allow</option>
        <option value="deny">deny</option>
        <option value="ask">ask</option>
      </Select>
      {rule.origin === "default" ? (
        // Factory defaults can be overridden (Select above) but not
        // deleted — deleting would just fall back to this default.
        <span
          data-testid={`settings-permission-locked-${rule.id}`}
          className="w-8 text-center font-mono text-xs text-ink-2"
          title={strings.settings.permissions.defaultTitle}
        >
          —
        </span>
      ) : (
        <IconButton
          data-testid={`settings-permission-delete-${rule.id}`}
          onClick={onDelete}
          aria-label={strings.settings.permissions.deleteAria}
        >
          <Trash2 />
        </IconButton>
      )}
    </li>
  );
}
