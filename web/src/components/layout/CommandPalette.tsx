/**
 * CommandPalette — Cmd/Ctrl+K quick navigation for sessions, actions and settings.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { Command, FileText, Settings, Sparkles } from "lucide-react";
import { Modal, Input } from "../../ui";
import { strings } from "../../ui/strings";
import { useCommandPalette } from "./useCommandPalette";
import type { PaletteItemType } from "./useCommandPalette";
import type { SettingsTab } from "../settings/SettingsPage";

const ICONS: Record<PaletteItemType, JSX.Element> = {
  action: <Sparkles size={14} />,
  session: <FileText size={14} />,
  setting: <Settings size={14} />,
};

export interface CommandPaletteHandle {
  toggle: () => void;
}

export interface CommandPaletteProps {
  onOpenSkills?: () => void;
  onOpenSettings?: (tab: SettingsTab) => void;
  onTogglePreview?: () => void;
}

export const CommandPalette = forwardRef<CommandPaletteHandle, CommandPaletteProps>(
  function CommandPalette(
    { onOpenSkills, onOpenSettings, onTogglePreview }: CommandPaletteProps,
    ref,
  ): JSX.Element {
    const {
      isOpen,
      close,
      query,
      setQuery,
      filtered,
      selectedIndex,
      setSelectedIndex,
      handleKeyDown,
      execute,
      toggle,
    } = useCommandPalette({
      onOpenSkills,
      onOpenSettings,
      onTogglePreview,
    });

    useImperativeHandle(ref, () => ({ toggle }), [toggle]);

    const inputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
      if (isOpen) {
        // Focus the search input when the palette opens.
        const t = window.setTimeout(() => inputRef.current?.focus(), 50);
        return () => window.clearTimeout(t);
      }
    }, [isOpen]);

    if (!isOpen) return <></>;

    return (
    <Modal
      testId="command-palette"
      title={strings.layout.commandPalette.title}
      onClose={close}
      widthClass="max-w-xl"
      footer={
        <div className="flex items-center gap-3 text-[11px] text-ink-2">
          <span className="flex items-center gap-1"><kbd className="rounded border border-line bg-surface-2 px-1">↑↓</kbd> 选择</span>
          <span className="flex items-center gap-1"><kbd className="rounded border border-line bg-surface-2 px-1">Enter</kbd> 执行</span>
          <span className="flex items-center gap-1"><kbd className="rounded border border-line bg-surface-2 px-1">Esc</kbd> 关闭</span>
        </div>
      }
    >
      <div className="flex flex-col gap-2">
        <div className="relative">
          <Command size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-2" />
          <Input
            ref={inputRef}
            data-testid="command-palette-input"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={handleKeyDown}
            placeholder="搜索会话、动作或设置…"
            fieldSize="md"
            className="pl-8"
          />
        </div>

        <ul
          data-testid="command-palette-list"
          className="max-h-[360px] min-h-[120px] overflow-y-auto rounded-md border border-line bg-surface-1 py-1"
          role="listbox"
        >
          {filtered.length === 0 && (
            <li className="px-3 py-6 text-center text-xs text-ink-2">无匹配结果</li>
          )}
          {filtered.map((item, idx) => (
            <li
              key={item.id}
              data-testid={`command-palette-item-${item.id}`}
              role="option"
              aria-selected={idx === selectedIndex}
              className={
                "flex cursor-pointer items-center gap-2.5 px-2.5 py-2 text-sm transition-colors " +
                (idx === selectedIndex
                  ? "bg-accent-subtle text-ink-0"
                  : "text-ink-1 hover:bg-surface-3 hover:text-ink-0")
              }
              onMouseEnter={() => setSelectedIndex(idx)}
              onClick={() => execute(item)}
            >
              <span className={"flex h-6 w-6 shrink-0 items-center justify-center rounded-md " + (idx === selectedIndex ? "text-accent" : "text-ink-2")}>
                {ICONS[item.type]}
              </span>
              <span className="min-w-0 flex-1 truncate">{item.title}</span>
              {item.subtitle && (
                <span className="truncate text-[11px] text-ink-2">{item.subtitle}</span>
              )}
            </li>
          ))}
        </ul>
      </div>
    </Modal>
    );
  },
);
