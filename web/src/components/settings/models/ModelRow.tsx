/**
 * ModelRow — a single selectable model in the Models tab list.
 */
import { Check } from "lucide-react";
import { Badge, Button } from "../../../ui";
import { strings } from "../../../ui/strings";
import type { ModelInfo } from "../../../types/ipc";

export interface ModelRowProps {
  model: ModelInfo;
  isCurrent: boolean;
  onSelect: (id: string) => void;
}

export function ModelRow({ model, isCurrent, onSelect }: ModelRowProps): JSX.Element {
  return (
    <li
      data-testid={`settings-model-${model.id}`}
      className={
        "flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm transition-colors duration-150 " +
        (isCurrent
          ? "border-accent/40 bg-accent-subtle"
          : "border-line bg-surface-2 hover:border-line-strong")
      }
    >
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="max-w-full truncate text-xs font-medium text-ink-0">{model.name}</span>
          {isCurrent && (
            <Badge tone="accent" data-testid={`settings-model-current-${model.id}`}>
              {strings.settings.modelsRow.current}
            </Badge>
          )}
          {model.protocol && (
            <Badge tone="neutral" className="font-mono">
              {model.protocol}
            </Badge>
          )}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-ink-2">
          {model.provider} ·{" "}
          {strings.settings.modelsRow.contextK((model.context_window / 1000).toFixed(0))}
          {model.supports_tools ? ` · ${strings.settings.modelsRow.supportsTools}` : ""}
        </div>
      </div>
      <Button
        size="sm"
        variant={isCurrent ? "ghost" : "subtle"}
        data-testid={`settings-model-select-${model.id}`}
        onClick={() => { if (!isCurrent) onSelect(model.id); }}
        disabled={isCurrent}
        icon={isCurrent ? <Check /> : undefined}
      >
        {isCurrent ? strings.settings.modelsRow.selected : strings.settings.modelsRow.use}
      </Button>
    </li>
  );
}
