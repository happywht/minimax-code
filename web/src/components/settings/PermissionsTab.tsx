/**
 * Permissions tab — list / upsert / remove permission rules.
 */
import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { usePermissionStore } from "../../stores";
import { toast } from "../ErrorBoundary";
import type { PermissionRule } from "../../types/ipc";
import { required, regexFormat, compose } from "../../lib/validators";
import { requestConfirmation } from "../ConfirmationDialog";

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

  return (
    <section data-testid="settings-permissions" className="space-y-4">
      <div>
        <h2 className="text-sm font-medium">Permission rules</h2>
        <p className="mt-0.5 text-[11px] text-minimax-muted">
          Patterns are matched against tool call arguments. A rule with
          decision <code>allow</code> skips the confirmation modal;{" "}
          <code>deny</code> blocks the call; <code>ask</code> always prompts.
        </p>
      </div>

      <div className="rounded-md border border-minimax-border bg-minimax-panel/40 p-3">
        <div className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-12">
          <div className="min-w-0 sm:col-span-3">
            <label htmlFor="permission-tool" className="mb-0.5 block text-[11px] text-minimax-muted">Tool</label>
            <input id="permission-tool" name="permission-tool" autoComplete="off" spellCheck={false}
              data-testid="settings-permission-tool" value={draftTool}
              onChange={(e) => { setDraftTool(e.target.value); setErrors((prev) => ({ ...prev, tool: undefined })); }}
              placeholder="e.g. bash…"
              className={`w-full rounded border bg-minimax-bg px-2 py-1 text-minimax-fg ${errors.tool ? "border-red-400" : "border-minimax-border"}`} />
          </div>
          <div className="min-w-0 sm:col-span-5">
            <label htmlFor="permission-pattern" className="mb-0.5 block text-[11px] text-minimax-muted">Argument Pattern</label>
            <input id="permission-pattern" name="permission-pattern" autoComplete="off" spellCheck={false}
              data-testid="settings-permission-pattern" value={draftPattern}
              onChange={(e) => { setDraftPattern(e.target.value); setErrors((prev) => ({ ...prev, pattern: undefined })); }}
              placeholder="e.g. ^git status$…"
              className={`w-full rounded border bg-minimax-bg px-2 py-1 font-mono text-minimax-fg ${errors.pattern ? "border-red-400" : "border-minimax-border"}`} />
          </div>
          <div className="min-w-0 sm:col-span-2">
            <label htmlFor="permission-decision" className="mb-0.5 block text-[11px] text-minimax-muted">Decision</label>
            <select id="permission-decision" name="permission-decision" data-testid="settings-permission-decision" value={draftDecision}
              onChange={(e) => setDraftDecision(e.target.value as "allow" | "deny" | "ask")}
              className="w-full rounded border border-minimax-border bg-minimax-bg px-2 py-1 text-minimax-fg">
              <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
            </select>
          </div>
          <button type="button" data-testid="settings-permission-add"
            disabled={!draftPattern.trim() || !draftTool.trim()}
            onClick={async () => {
              const toolErr = required("Tool")(draftTool);
              const patternErr = compose(required("Pattern"), regexFormat())(draftPattern);
              if (toolErr || patternErr) {
                setErrors({ tool: toolErr || undefined, pattern: patternErr || undefined });
                return;
              }
              await upsertRule({ tool: draftTool.trim(), pattern: draftPattern.trim(), decision: draftDecision });
              toast.success("Rule saved", `${draftTool} ${draftPattern} → ${draftDecision}`);
              setDraftPattern("");
              setErrors({});
            }}
            className="mt-auto inline-flex items-center justify-center gap-1 rounded border border-minimax-accent/40 bg-minimax-accent/10 px-2 py-1 text-xs text-minimax-accent hover:bg-minimax-accent/20 disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-2">
            <Plus size={12} /> Add
          </button>
        </div>
      </div>

      <ul className="space-y-1.5" data-testid="settings-permissions-list">
        {rules.length === 0 && (
          <li className="rounded border border-dashed border-minimax-border px-3 py-4 text-center text-xs text-minimax-muted">
            No permission rules yet
          </li>
        )}
        {rules.map((r) => (
          <PermissionRuleRow key={r.id} rule={r}
            onDelete={async () => {
              const accepted = await requestConfirmation({
                title: "Delete permission rule?",
                description: `The rule for ${r.tool} (${r.pattern}) will no longer control future tool calls.`,
                confirmLabel: "Delete Rule",
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
    <li data-testid={`settings-permission-row-${rule.id}`}
      className="flex items-center gap-2 rounded-md border border-minimax-border bg-minimax-panel/40 px-3 py-2 text-sm">
      <span className="rounded bg-minimax-bg px-1.5 py-0.5 text-[11px] text-minimax-muted">{rule.tool}</span>
      <code className="flex-1 truncate font-mono text-xs text-minimax-fg">{rule.pattern}</code>
      <select data-testid={`settings-permission-decision-${rule.id}`} value={rule.decision}
        aria-label={`Decision for ${rule.tool} ${rule.pattern}`}
        onChange={(e) => onUpdate(e.target.value as "allow" | "deny" | "ask")}
        className="rounded border border-minimax-border bg-minimax-bg px-2 py-0.5 text-xs text-minimax-fg">
        <option value="allow">allow</option><option value="deny">deny</option><option value="ask">ask</option>
      </select>
      <button type="button" data-testid={`settings-permission-delete-${rule.id}`}
        onClick={onDelete} aria-label="Delete rule"
        className="rounded border border-minimax-border p-1 text-minimax-muted hover:text-status-error">
        <Trash2 size={12} aria-hidden="true" />
      </button>
    </li>
  );
}
