/**
 * User-facing Chinese copy — the single source of truth for UI strings.
 *
 * Every visible label, tooltip and aria-label lives here, grouped by UI
 * domain (layout / chat / settings / …), so wording stays consistent and
 * a future localization pass has one file to swap. Technical identifiers,
 * test ids, log messages and session-title *data* defaults stay out —
 * those belong to the stores, not the copy layer.
 *
 * Conventions:
 *  - Static copy: plain string properties.
 *  - Interpolated copy: small arrow functions (keeps call sites honest
 *    about their parameters).
 *  - Punctuation is full-width Chinese（，。…）except inside technical
 *    terms like "Ctrl+K" or "Worktree".
 */
export const strings = {
  /** Cross-domain shared words. */
  common: {
    close: "关闭",
    retry: "重试",
    settings: "设置",
    delete: "删除",
  },

  /** App-shell domain: TopBar, Sidebar, banners, overlays, footer. */
  layout: {
    topbar: {
      toggleSidebar: "切换侧栏",
      openCommandPalette: "打开命令面板",
      commandPaletteHint: "命令面板（Ctrl+K）",
      preview: "预览",
      settings: "设置",
    },

    sidebar: {
      brandTagline: "AI 编码助手",
      projectOptions: "项目选项",
      sessionOptions: "任务选项",
      untitled: "（未命名）",
      createWorktreeTask: "创建隔离的 Worktree 任务",
      newProject: "新建项目",
      searchTaskHistory: "搜索任务历史",
      searchHistoryPlaceholder: "搜索历史…",
      clearHistorySearch: "清除搜索",
    },

    commandPalette: {
      title: "命令面板",
      newTaskSubtitle: "新建聊天会话",
      previewSubtitle: "切换实时预览面板",
      skillsSubtitle: "技能库",
    },

    connection: {
      connectingTitle: "正在连接 Agent",
      connectingDetail: "正在准备本地运行时。",
      disconnectedTitle: "与 Agent 的连接已断开",
      disconnectedDetail: "后台正在自动重试。",
      errorTitle: "连接出错",
      errorDetail: "本地 Agent 未响应。",
      retry: "重试",
      dismiss: "关闭连接提示",
      nextRetry: (seconds: number) => `${seconds} 秒后重试。`,
    },

    storage: {
      title: "本地存储不可用",
      detail: "新的会话与消息不会被保存。重启 Agent 后通常可恢复。",
    },

    errorBoundary: {
      title: "出错了",
      reload: "重新加载组件",
    },

    gitStatus: {
      loading: "加载中…",
      clean: "无变更",
      changesCount: (count: number) => `${count} 处变更`,
      branchTitle: (branch: string, label: string) => `分支 ${branch} — ${label}`,
      loadingTitle: "正在加载 Git 状态…",
      panelLabel: (branch: string) => `${branch} 分支的 Git 状态`,
      modified: "已修改",
      staged: "已暂存",
      untracked: "未跟踪",
      workingTreeClean: "工作区无变更。",
      viewDiff: "查看差异",
      viewLog: "查看日志",
    },

    workspace: {
      switchedToast: "已切换工作区",
      empty: "暂无工作区",
    },

    theme: {
      toLight: "切换到浅色模式",
      toDark: "切换到深色模式",
      light: "浅色模式",
      dark: "深色模式",
    },

    notifications: {
      title: "通知",
      bellLabel: (unread: number) =>
        unread > 0 ? `通知（${unread} 条未读）` : "通知",
      unreadSuffix: (unread: number) => `（${unread} 条未读）`,
      markAllReadTitle: "全部标为已读",
      readAll: "全部已读",
      close: "关闭通知",
      markRead: "标为已读",
      delete: "删除",
    },

    shortcuts: {
      title: "键盘快捷键",
      close: "关闭快捷键面板",
      items: [
        { key: "?", label: "打开快捷键面板" },
        { key: "Esc", label: "关闭面板" },
        { key: "Enter", label: "发送消息" },
        { key: "Shift Enter", label: "换行" },
        { key: "@", label: "选择子 Agent" },
        { key: "Tab", label: "确认选中项" },
      ],
    },

    userBadge: {
      defaultName: "用户",
      defaultEmail: "未绑定邮箱",
      defaultPlan: "个人版",
      editPlan: "点击编辑计划名称",
      settings: "设置",
      signOut: "退出登录",
    },

    toast: {
      dismiss: "关闭",
    },
  },
} as const;

export type Strings = typeof strings;
