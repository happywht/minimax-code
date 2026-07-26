/**
 * useCommandPalette — items, filtering, keyboard nav and shortcut registration.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSessionStore } from "../../stores";
import type { SettingsTab } from "../settings/SettingsPage";

export type PaletteItemType = "action" | "session" | "setting";

export interface PaletteItem {
  id: string;
  type: PaletteItemType;
  title: string;
  subtitle?: string;
  keywords?: string;
}

export interface UseCommandPaletteOptions {
  onOpenSkills?: () => void;
  onOpenSettings?: (tab: SettingsTab) => void;
  onTogglePreview?: () => void;
}

export function useCommandPalette({
  onOpenSkills,
  onOpenSettings,
  onTogglePreview,
}: UseCommandPaletteOptions) {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);

  const sessions = useSessionStore((s) => s.sessions);
  const setCurrentSession = useSessionStore((s) => s.setCurrent);
  const createSession = useSessionStore((s) => s.create);
  const creatingSession = useSessionStore((s) => s.creating);

  const open = useCallback(() => {
    setQuery("");
    setSelectedIndex(0);
    setIsOpen(true);
  }, []);

  const close = useCallback(() => {
    setIsOpen(false);
    setQuery("");
  }, []);

  const toggle = useCallback(() => {
    setIsOpen((v) => !v);
    setQuery("");
    setSelectedIndex(0);
  }, []);

  // Register Cmd/Ctrl+K to open; Esc is handled by the Modal.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const isModifier = event.metaKey || event.ctrlKey;
      if (isModifier && event.key.toLowerCase() === "k") {
        event.preventDefault();
        toggle();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [toggle]);

  const items = useMemo<PaletteItem[]>(() => {
    const list: PaletteItem[] = [];

    // Actions
    list.push(
      {
        id: "action:new-task",
        type: "action",
        title: "新建任务",
        subtitle: "New chat session",
        keywords: "new task chat create",
      },
      {
        id: "action:preview",
        type: "action",
        title: "切换 Preview",
        subtitle: "Toggle live preview panel",
        keywords: "preview toggle",
      },
      {
        id: "action:skills",
        type: "action",
        title: "打开技能面板",
        subtitle: "Skills library",
        keywords: "skills library",
      },
    );

    // Settings
    const settingsTabs: { tab: SettingsTab; label: string; keywords: string }[] = [
      { tab: "models", label: "设置：模型", keywords: "settings models" },
      { tab: "providers", label: "设置：服务商", keywords: "settings providers" },
      { tab: "agents", label: "设置：Agents", keywords: "settings agents" },
      { tab: "teams", label: "设置：团队", keywords: "settings teams" },
      { tab: "scheduled", label: "设置：定时任务", keywords: "settings scheduled jobs" },
      { tab: "permissions", label: "设置：权限", keywords: "settings permissions" },
      { tab: "webhooks", label: "设置：Webhooks", keywords: "settings webhooks" },
      { tab: "workflows", label: "设置：工作流", keywords: "settings workflows" },
      { tab: "audit", label: "设置：审计", keywords: "settings audit" },
      { tab: "api-key", label: "设置：API 密钥", keywords: "settings api keys" },
    ];
    for (const { tab, label, keywords } of settingsTabs) {
      list.push({
        id: `setting:${tab}`,
        type: "setting",
        title: label,
        keywords,
      });
    }

    // Recent sessions
    const recent = [...sessions]
      .sort((a, b) => b.updated_at - a.updated_at)
      .slice(0, 10);
    for (const s of recent) {
      list.push({
        id: `session:${s.id}`,
        type: "session",
        title: s.title || "(untitled)",
        subtitle: "历史会话",
        keywords: s.id,
      });
    }

    return list;
  }, [sessions]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((it) => {
      const hay = `${it.title} ${it.subtitle ?? ""} ${it.keywords ?? ""}`.toLowerCase();
      return hay.includes(q);
    });
  }, [items, query]);

  const execute = useCallback(
    (item: PaletteItem) => {
      if (item.id === "action:new-task") {
        void createSession("New task");
      } else if (item.id === "action:preview") {
        onTogglePreview?.();
      } else if (item.id === "action:skills") {
        onOpenSkills?.();
      } else if (item.type === "setting" && item.id.startsWith("setting:")) {
        const tab = item.id.replace("setting:", "") as SettingsTab;
        onOpenSettings?.(tab);
      } else if (item.type === "session" && item.id.startsWith("session:")) {
        const sid = item.id.replace("session:", "");
        setCurrentSession(sid);
      }
      close();
    },
    [close, createSession, onOpenSettings, onOpenSkills, onTogglePreview, setCurrentSession],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLInputElement>) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex((i) => (i + 1) % filtered.length);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex((i) => (i - 1 + filtered.length) % filtered.length);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const item = filtered[selectedIndex];
        if (item) execute(item);
      }
    },
    [filtered, selectedIndex, execute],
  );

  return {
    isOpen,
    open,
    close,
    toggle,
    query,
    setQuery,
    selectedIndex,
    setSelectedIndex,
    filtered,
    handleKeyDown,
    execute,
    creatingSession,
  };
}
