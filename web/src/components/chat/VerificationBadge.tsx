import type { VerificationEvidence } from "../../types/ipc";

export function VerificationBadge({ evidence }: { evidence?: VerificationEvidence }): JSX.Element {
  const verdict = evidence?.version === 1 ? evidence.verdict ?? "unknown" : "unknown";
  const labels = { passed: "配置的验证命令通过", failed: "验证未通过", unknown: "验证状态未知", not_applicable: "验证不适用" };
  const reasons: Record<string, string> = {
    not_configured: "未配置验证命令", legacy_unverified: "历史记录未保存验证证据",
    engine_validation_not_supported: "此历史外部引擎任务未支持本地自动验证", plan_mode: "规划模式不执行验证",
    validation_missing: "验证尚未完成", validation_spawn_failed: "验证命令未能启动",
    validation_denied: "主机验证未获授权或已超时", validation_workspace_changed: "项目或任务工作区已变化，未执行验证",
    validation_timeout: "验证超时", validation_failed: "验证命令失败", validation_passed: "验证命令通过",
    failed_tool_calls: "存在失败的工具调用", execution_truncated: "任务尚未完成",
    receipt_unconfirmed: "持久回执或验证证据缺失、损坏或不一致，请核对原运行",
  };
  const reason = reasons[evidence?.reason ?? "legacy_unverified"] ?? "任务未正常完成，请核对保留的输出";
  return <span data-testid="verification-verdict" className={`text-[11px] ${evidence?.requires_review || verdict === "failed" ? "text-status-warning" : verdict === "passed" ? "text-status-success" : "text-ink-2"}`} title={reason}>
    {labels[verdict]} · {reason}{evidence?.requires_review ? " · 需要核对" : ""}
  </span>;
}
