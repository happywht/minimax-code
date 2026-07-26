import { AlertTriangle, ArrowRight, CircleOff } from "lucide-react";
import { useModelStore, useProviderStore } from "../../stores";

export interface ProviderReadinessBannerProps {
  onOpenProviders: () => void;
  onOpenModels: () => void;
}

export function ProviderReadinessBanner({
  onOpenProviders,
  onOpenModels,
}: ProviderReadinessBannerProps): JSX.Element | null {
  const models = useModelStore((state) => state.models);
  const currentModelId = useModelStore((state) => state.current);
  const providers = useProviderStore((state) => state.providers);
  const initialized = useProviderStore((state) => state.initialized);

  if (!initialized) return null;

  const model = models.find((entry) => entry.id === currentModelId);
  const provider = model
    ? providers.find((entry) => entry.id === model.provider_id)
      ?? providers.find((entry) => entry.name === model.provider)
    : null;

  if (model && provider?.enabled && provider.api_key_configured) return null;

  const noModel = !model;
  const title = noModel
    ? "No Active Model"
    : provider?.enabled === false
      ? "Provider Disabled"
      : "Demo Mode";
  const detail = noModel
    ? "Select a model before starting a task."
    : provider
      ? `${model.name} will return mock responses until ${provider.name} has an API key.`
      : `${model.name} is linked to a provider that is not available.`;
  const action = noModel ? "Choose Model" : "Configure Provider";
  const handleAction = noModel ? onOpenModels : onOpenProviders;

  return (
    <aside
      data-testid="provider-readiness-banner"
      role="status"
      aria-live="polite"
      className="shrink-0 border-t border-amber-500/20 bg-amber-500/5 px-3 py-2 sm:px-4"
    >
      <div className="mx-auto flex w-full max-w-[780px] items-center gap-2.5">
        {noModel ? (
          <CircleOff size={14} aria-hidden="true" className="shrink-0 text-amber-300" />
        ) : (
          <AlertTriangle size={14} aria-hidden="true" className="shrink-0 text-amber-300" />
        )}
        <div className="min-w-0 flex-1 text-[11px] leading-4">
          <span className="font-medium text-amber-200">{title}</span>
          <span className="ml-1.5 text-minimax-muted">{detail}</span>
        </div>
        <button
          type="button"
          data-testid="provider-readiness-action"
          onClick={handleAction}
          className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-amber-200 transition-colors duration-200 hover:bg-amber-500/10 hover:text-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-400/60"
        >
          {action}
          <ArrowRight size={11} aria-hidden="true" />
        </button>
      </div>
    </aside>
  );
}
