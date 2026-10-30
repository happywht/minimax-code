/**
 * AskUserCard — inline questionnaire rendered while the agent is
 * suspended inside the `ask_user` tool.
 *
 * Visibility contract:
 * - mounts when the chat store receives an `agent.ask_user` event whose
 *   session matches the current session;
 * - unmounts when submitAskUser succeeds (or the backend rejects the
 *   answer) and — as a safety net for timeout/cancel — when the matching
 *   ask_user tool_result event clears the store.
 *
 * Answer shape per question: single-select yields a label string (or
 * "other: <text>" when the free-text field is used); multi-select yields
 * the array of chosen labels. This mirrors the agent-side
 * `format_answers` renderer positionally.
 */

import { HelpCircle, Send } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useChat } from "../../stores/chat";
import { useSessionStore } from "../../stores/sessionStore";
import { strings } from "../../ui/strings";
import { Button } from "../../ui";
import { ipc, typedIPC } from "../../ipc";
import { useRunTimelineStore } from "../../stores/runStore";

// In-memory only: a draft belongs to one live request on one host.
const drafts = new Map<string, { selections: string[][]; others: Array<string | null> }>();

export interface AskUserCardProps {
  testId?: string;
}

export function AskUserCard({ testId = "ask-user-card" }: AskUserCardProps): JSX.Element | null {
  const askUser = useChat((s) => s.askUser);
  const submit = useChat((s) => s.submitAskUser);
  const skip = useChat((s) => s.skipAskUser);
  const sessionId = useSessionStore((s) => s.currentSessionId);
  const runs = useRunTimelineStore((s) => s.runs);
  const [selecting, setSelecting] = useState(false);
  const [selectionError, setSelectionError] = useState("");
  const selectionEpoch = useRef(0);
  useEffect(() => { ++selectionEpoch.current; setSelecting(false); setSelectionError(""); }, [sessionId]);
  useEffect(() => ipc.onSideCar(() => { ++selectionEpoch.current; setSelecting(false); setSelectionError("连接变化，请刷新待答问题。"); }), []);
  const alternatives = useMemo(() => Object.values(runs)
    .filter((run) => run.session_id === sessionId)
    .flatMap((run) => run.pendingQuestions ?? [])
    .filter((item) => item.request_id !== askUser?.request_id), [runs, sessionId, askUser?.request_id]);
  const cardRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const open = (event: Event) => {
      const detail = (event as CustomEvent<{ host: string; session: string; run: string; request: string }>).detail;
      const current = useChat.getState().askUser;
      if (!detail || ipc.agentBaseUrl !== detail.host || useSessionStore.getState().currentSessionId !== detail.session ||
          !current || current.session_id !== detail.session || current.run_id !== detail.run || current.request_id !== detail.request) return;
      cardRef.current?.scrollIntoView({ block: "nearest" });
      cardRef.current?.focus();
    };
    window.addEventListener("minimax:open-run-question", open);
    return () => window.removeEventListener("minimax:open-run-question", open);
  }, []);

  // One entry per question: chosen labels (multi for multiSelect).
  const [selections, setSelections] = useState<string[][]>([]);
  // Free-text "其他" draft per question. null = field closed; a string
  // (possibly empty) = field open. Non-empty text overrides the chip
  // selection for single-select and is appended for multi.
  const [others, setOthers] = useState<Array<string | null>>([]);
  const [submitting, setSubmitting] = useState(false);

  // Reset local draft state whenever a new questionnaire arrives.
  const requestKey = askUser ? JSON.stringify([ipc.agentBaseUrl, askUser.session_id, askUser.run_id, askUser.request_id]) : null;
  const questionCount = askUser?.questions.length ?? 0;
  useEffect(() => {
    if (!requestKey) { setSelections([]); setOthers([]); return; }
    const draft = drafts.get(requestKey);
    setSelections(draft?.selections ?? Array.from({ length: questionCount }, () => []));
    setOthers(draft?.others ?? askUser!.questions.map((q) => askUser!.engine === "codex" && q.options.length === 0 ? "" : null));
    setSubmitting(false);
  }, [requestKey, questionCount]);

  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  useEffect(() => {
    if (!askUser) { setRemainingSeconds(null); return; }
    const deadline = askUser.remaining_ms !== undefined
      ? Date.now() + askUser.remaining_ms
      : (askUser.expires_at_ms ?? Date.now() + askUser.timeout_s * 1000);
    const update = () => setRemainingSeconds(Math.max(0, Math.ceil((deadline - Date.now()) / 1000)));
    update();
    const timer = setInterval(update, 1000);
    return () => clearInterval(timer);
  }, [askUser]);

  const saveDraft = (nextSelections: string[][], nextOthers: Array<string | null>) => {
    if (!requestKey || askUser?.questions.some((q) => q.isSecret)) return;
    drafts.delete(requestKey);
    drafts.set(requestKey, { selections: nextSelections, others: nextOthers });
    if (drafts.size > 50) drafts.delete(drafts.keys().next().value!);
  };

  const visible = askUser !== null && askUser.session_id === sessionId;
  const otherText = (qi: number) => askUser?.questions[qi]?.isSecret
    ? (others[qi] ?? "") : (others[qi] ?? "").trim();
  const otherOpen = (qi: number) => others[qi] != null;
  const answered = useMemo(
    () =>
      askUser?.questions.map(
        (_, i) => selections[i]?.length > 0 || otherText(i) !== "",
      ) ?? [],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- otherText reads `others` from closure
    [askUser, selections, others],
  );
  const allAnswered = visible && answered.every(Boolean);
  const firstUnanswered = answered.findIndex((a) => !a) + 1;

  if (!visible) return null;

  const isOtherActive = (qi: number) => otherText(qi) !== "";

  const toggle = (qi: number, label: string, multi: boolean) => {
    setSelections((prev) => {
      const next = prev.map((entry) => [...entry]);
      const cur = next[qi] ?? [];
      const idx = cur.indexOf(label);
      if (multi) {
        if (idx >= 0) cur.splice(idx, 1);
        else cur.push(label);
      } else {
        next[qi] = idx >= 0 ? [] : [label];
      }
      saveDraft(next, others);
      return next;
    });
  };

  const buildAnswers = (): Array<string | string[]> =>
    askUser.questions.map((q, qi) => {
      const other = otherText(qi);
      const chosen = selections[qi] ?? [];
      if (q.multiSelect) {
        const values = [...chosen];
        if (other) values.push(`other: ${other}`);
        return values.length > 0 ? values : ["(no answer)"];
      }
      if (other) return askUser.engine === "codex" ? other : `other: ${other}`;
      return chosen[0] ?? "(no answer)";
    });

  const handleSubmit = async () => {
    if (!allAnswered || submitting) return;
    setSubmitting(true);
    try {
      await submit(buildAnswers());
      if (useChat.getState().askUser?.request_id !== askUser.request_id && requestKey) drafts.delete(requestKey);
    } finally {
      setSubmitting(false);
    }
  };

  const handleSkip = async () => {
    if (submitting) return;
    setSubmitting(true);
    try {
      await skip();
      if (useChat.getState().askUser?.request_id !== askUser.request_id && requestKey) drafts.delete(requestKey);
    } finally {
      setSubmitting(false);
    }
  };

  const selectQuestion = async (requestId: string, runId: string) => {
    if (selecting || !sessionId) return;
    const ticket = ++selectionEpoch.current, host = ipc.agentBaseUrl;
    const version = useChat.getState().askRecoveryVersion();
    const current = () => ticket === selectionEpoch.current && ipc.agentBaseUrl === host &&
      useSessionStore.getState().currentSessionId === sessionId;
    setSelecting(true); setSelectionError("");
    try {
      const before = await typedIPC.getServiceIdentity();
      const snapshot = await typedIPC.getRunSteps(runId);
      const after = await typedIPC.getServiceIdentity();
      if (!current()) return;
      const requests = snapshot.pending_ask_users ?? (snapshot.pending_ask_user ? [snapshot.pending_ask_user] : []);
      const question = requests.find((item) => item.request_id === requestId);
      if (!before.service_id || before.service_id !== after.service_id || snapshot.run.id !== runId ||
          snapshot.run.session_id !== sessionId || !question || question.run_id !== runId ||
          question.session_id !== sessionId || (question.remaining_ms !== undefined && question.remaining_ms <= 0)) {
        throw new Error("问题已结束或服务、会话归属已变化，请刷新。");
      }
      if (useChat.getState().askRecoveryVersion() !== version) throw new Error("待答问题已变化，请重试。");
      useChat.getState().reconcileAskUser(question, version, sessionId);
    } catch (error) { if (current()) setSelectionError(error instanceof Error ? error.message : String(error)); }
    finally { if (current()) setSelecting(false); }
  };

  return (
    <section
      ref={cardRef}
      tabIndex={-1}
      data-testid={testId}
      data-request-id={askUser.request_id}
      aria-labelledby={`${testId}-title`}
      className="mx-auto w-full max-w-[780px] shrink-0 rounded-xl border border-accent/30 bg-surface-1 p-3.5 shadow-modal sm:p-4"
    >
      <header className="mb-3 flex items-start gap-2.5">
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-accent/30 bg-accent-subtle text-accent">
          <HelpCircle size={16} aria-hidden="true" />
        </div>
        <div className="min-w-0 flex-1">
          <h2
            id={`${testId}-title`}
            data-testid={`${testId}-title`}
            className="text-sm font-semibold text-ink-0"
          >
            {strings.chat.askUser.title}
          </h2>
          <p className="mt-0.5 text-xs text-ink-2">
            {strings.chat.askUser.subtitle}
            {askUser.agent_name && <span> · 子 Agent：{askUser.agent_name}</span>}
            <span className="ml-1.5 text-ink-2/80">
              {remainingSeconds !== null && `剩余 ${Math.floor(remainingSeconds / 60)} 分 ${remainingSeconds % 60} 秒`}
            </span>
          </p>
        </div>
      </header>

      {alternatives.length > 0 && <details className="mb-3 rounded border border-line p-2 text-xs">
        <summary>其他待答问题（{alternatives.length}）</summary>
        <p>切换前会重新确认问题仍在等待；当前草稿保留在本页面。</p>
        <ul>{alternatives.map((item) => <li key={item.request_id} className="mt-1">
          <button type="button" className="text-left underline" disabled={selecting || submitting || !item.run_id}
            onClick={() => item.run_id && void selectQuestion(item.request_id, item.run_id)}>
            {item.agent_name ? `子 Agent ${item.agent_name}` : `运行 ${item.run_id ?? "未知"}`} · {item.questions[0]?.question ?? "待答问题"}
          </button>
        </li>)}</ul>
        {selectionError && <p role="alert">{selectionError}</p>}
      </details>}

      <div data-testid={`${testId}-questions`} className="space-y-3.5">
        {askUser.questions.map((q, qi) => (
          <fieldset
            key={`${askUser.request_id}-${qi}`}
            data-testid={`${testId}-question-${qi}`}
            className="rounded-lg border border-line bg-surface-2/40 px-3 py-2.5"
          >
            <legend className="sr-only">{q.header}</legend>
            <div className="mb-2 flex flex-wrap items-center gap-1.5">
              <span
                data-testid={`${testId}-header-${qi}`}
                className="rounded bg-accent-subtle px-1.5 py-0.5 text-[11px] font-medium text-accent"
              >
                {q.header}
              </span>
              <span className="text-xs text-ink-1">{q.question}</span>
            </div>
            <div className="flex flex-wrap gap-1.5" role={q.multiSelect ? "group" : "radiogroup"}>
              {q.options.map((opt) => {
                const selected =
                  !isOtherActive(qi) && (selections[qi] ?? []).includes(opt.label);
                return (
                  <button
                    key={opt.label}
                    type="button"
                    data-testid={`${testId}-option-${qi}`}
                    data-label={opt.label}
                    aria-pressed={selected}
                    title={opt.description}
                    onClick={() => toggle(qi, opt.label, q.multiSelect)}
                    className={
                      "max-w-full rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 " +
                      (selected
                        ? "border-accent bg-accent-subtle text-accent"
                        : "border-line bg-surface-1 text-ink-1 hover:border-line-strong hover:text-ink-0")
                    }
                  >
                    <span className="font-medium">{opt.label}</span>
                    {opt.description && (
                      <span className="ml-1.5 text-[11px] opacity-70">{opt.description}</span>
                    )}
                  </button>
                );
              })}
              {(askUser.engine !== "codex" || (q.isOther && q.options.length > 0)) && <button
                type="button"
                data-testid={`${testId}-other-${qi}`}
                aria-pressed={isOtherActive(qi)}
                onClick={() =>
                  setOthers((prev) => {
                    const next = [...prev];
                    // Toggling "其他" off discards the draft text.
                    next[qi] = otherOpen(qi) ? null : "";
                    saveDraft(selections, next);
                    return next;
                  })
                }
                className={
                  "rounded-md border px-2.5 py-1.5 text-xs transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 " +
                  (isOtherActive(qi)
                    ? "border-accent bg-accent-subtle text-accent"
                    : "border-dashed border-line bg-surface-1 text-ink-2 hover:border-line-strong hover:text-ink-0")
                }
              >
                {strings.chat.askUser.other}
              </button>}
            </div>
            {otherOpen(qi) && (
              <input
                type={q.isSecret ? "password" : "text"}
                autoComplete={q.isSecret ? "off" : undefined}
                data-testid={`${testId}-other-input-${qi}`}
                className="mt-2 w-full rounded-md border border-line bg-surface-1 px-2.5 py-1.5 text-xs text-ink-0 placeholder:text-ink-2/70 focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/30"
                placeholder={strings.chat.askUser.otherPlaceholder}
                value={others[qi] ?? ""}
                onChange={(e) =>
                  setOthers((prev) => {
                    const next = [...prev];
                    next[qi] = e.target.value;
                    saveDraft(selections, next);
                    return next;
                  })
                }
              />
            )}
          </fieldset>
        ))}
      </div>

      <footer className="mt-3.5 flex flex-wrap items-center justify-between gap-2">
        <p
          data-testid={`${testId}-hint`}
          className="text-[11px] text-ink-2"
          aria-live="polite"
        >
          {allAnswered ? "" : strings.chat.askUser.unanswered(firstUnanswered)}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            data-testid={`${testId}-skip`}
            onClick={() => void handleSkip()}
            disabled={submitting}
          >
            {askUser.engine === "codex" ? "取消提问" : strings.chat.askUser.skip}
          </Button>
          <Button
            variant="primary"
            size="sm"
            icon={<Send />}
            data-testid={`${testId}-submit`}
            onClick={() => void handleSubmit()}
            disabled={!allAnswered || submitting}
          >
            {strings.chat.askUser.submit}
          </Button>
        </div>
      </footer>
    </section>
  );
}
