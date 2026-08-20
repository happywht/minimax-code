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

  /** Chat domain: header, composer, messages, banners, mention picker. */
  chat: {
    header: {
      titleInput: "会话标题",
      renameHint: "双击重命名",
      rename: "重命名会话",
      newTask: "新建任务",
      newButton: "+ 新建",
      search: "搜索消息",
      searchPlaceholder: "搜索消息…",
      filtering: "筛选中",
      refresh: "刷新",
      refreshSessions: "刷新会话列表",
      more: "更多",
      moreOptions: "更多选项",
      clearSearch: "清除搜索",
    },

    headerStatus: {
      sending: "发送中",
      streaming: "生成中",
      error: "错误",
      ready: "就绪",
    },

    messageStatus: {
      queued: "排队中",
      sending: "发送中",
      streaming: "生成中",
      failed: "失败",
      cancelling: "停止中…",
      cancelled: "已停止",
    },

    input: {
      dropFilesHere: "松开以添加文件…",
      attachFile: "添加文件",
      attachTextFile: "添加文本文件",
      attachImage: "添加图片",
      attachAnImage: "添加一张图片",
      messageLabel: "消息输入",
      placeholder: "问 MiniMax 任何问题…（Enter · @agent · @repo · #file）",
      stopping: "正在停止…",
      stop: "停止",
      stopVoice: "停止语音输入",
      startVoice: "开始语音输入",
      listening: "正在聆听…点击停止",
      voiceInput: "语音输入",
      loadingContext: "正在加载上下文…",
      send: "发送",
    },

    actions: {
      copy: "复制",
      copied: "已复制",
      copyMessage: "复制消息",
      messageCopied: "消息已复制",
      copyFailed: "复制失败",
      copyFailedDetail: "剪贴板访问被拒绝。",
      retry: "重试",
      menu: "消息操作",
    },

    list: {
      jumpToLatest: "跳到最新消息",
      newCount: (count: number) => `${count} 条新消息`,
      latest: "最新",
      searchSummary: (shown: number, total: number) => `共 ${total} 条消息，匹配 ${shown} 条`,
      loadEarlier: (shown: number, hidden: number) =>
        `↑ 加载更早的 ${shown} 条消息（还有 ${hidden} 条）`,
    },

    fileRefs: {
      groupLabel: "引用的文件",
      copyReference: "复制文件引用",
    },

    attachments: {
      removeFile: (name: string) => `移除 ${name}`,
    },

    toolCall: {
      fallbackName: "工具",
      mcpServer: (server: string) => `MCP 服务器：${server}`,
    },

    mermaid: {
      copySource: "复制图表源码",
      renderFailed: "无法渲染 Mermaid 图表",
      rendering: "正在渲染图表…",
    },

    context: {
      title: (used: string, total: string) => `上下文：已用 ${used} / 上限 ${total} tokens`,
    },

    turnSummary: "本轮摘要",
    sources: "来源",

    memory: {
      savedCount: (count: number) => `已保存 ${count} 条记忆`,
    },

    providerBanner: {
      noModelTitle: "未选择模型",
      providerDisabledTitle: "提供商已禁用",
      demoTitle: "演示模式",
      noModelDetail: "开始任务前请先选择模型。",
      providerDetail: (model: string, provider: string) =>
        `${model} 将返回模拟响应，直到 ${provider} 配置 API 密钥。`,
      unavailableDetail: (model: string) => `${model} 关联的提供商不可用。`,
      chooseModel: "选择模型",
      configureProvider: "配置提供商",
    },

    mention: {
      agentHeader: "生成子 Agent",
      repoHeader: "仓库上下文",
      fileHeader: "文件上下文",
      loadFailed: "加载 Agent 失败",
    },

    modelSelector: {
      fallback: "模型",
    },

    composer: {
      exportSession: "将当前会话导出为 Markdown",
    },

    toast: {
      sessionCreateFailed: "创建会话失败",
      fileReadFailed: "读取文件失败",
      imageReadFailed: "读取图片失败",
      subagentFailed: "子 Agent 生成失败",
      mentionTitle: "上下文提及",
      mentionNeedQuestion: "请在 @repo 后补充问题。",
      mentionLoadFailed: "加载提及上下文失败",
    },
  },
} as const;

export type Strings = typeof strings;
