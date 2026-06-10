/**
 * Floating composer with an @-agent picker.
 *
 * v0.3.0 §2: typing ``@`` in the textarea opens a dropdown listing
 * sub-agents the user can dispatch to. Selecting one (click or
 * arrow + Enter) inserts a marker in the textarea and primes a
 * "send" that invokes ``agent.spawn_subagent`` rather than the
 * regular chat ``send``. The plain ``Enter``-without-picker path
 * keeps the existing chat send behaviour.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AtSign, Bot, Camera, Loader2, Mic, MicOff, Paperclip, Send, Shield, ShieldCheck, Square, X } from "lucide-react";
import { useChat, usePermissionStore, useSubAgentStore } from "../stores";
import { typedIPC } from "../ipc";
import { ModelSelector } from "./ModelSelector";
import { ContextIndicator } from "./ContextIndicator";
import { ImagePreview } from "./ImagePreview";
import { toast } from "./ErrorBoundary";
import type { AgentInfo, ContentPartImage } from "../types/ipc";
import { useSessionStore } from "../stores";

export interface MessageInputProps {
  testId?: string;
  /** Override the agent list fetcher (for tests). */
  loadAgents?: () => Promise<AgentInfo[]>;
}

const SUGGESTION_EVENT = "minimax:suggestion";
const MAX_ATTACHMENTS = 5;
const MAX_FILE_CHARS = 32_000;
const MAX_INPUT_CHARS = 8000;

const ACCEPTED_EXTS =
  ".txt,.md,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.cfg,.sh,.bat,.sql,.html,.css,.csv,.log,.xml,.ini,.env,.gitignore,.editorconfig,.eslintrc,.prettierrc";

interface AttachedFile {
  name: string;
}

interface PickerState {
  open: boolean;
  query: string;
  /** Index in the filtered list, -1 = none. */
  cursor: number;
  /** Anchor position (textarea character offset) where the @ token starts. */
  anchor: number;
}

const INITIAL_PICKER: PickerState = { open: false, query: "", cursor: -1, anchor: -1 };

export function MessageInput({
  testId = "message-input",
  loadAgents,
}: MessageInputProps): JSX.Element {
  const [value, setValue] = useState("");
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [agentsError, setAgentsError] = useState<string | null>(null);
  const [picker, setPicker] = useState<PickerState>(INITIAL_PICKER);
  const status = useChat((s) => s.status);
  const send = useChat((s) => s.send);
  const addLocalMessage = useChat((s) => s.addLocalMessage);
  const cancel = useChat((s) => s.cancel);
  const alwaysAllow = usePermissionStore((s) => s.alwaysAllow);
  const setAlwaysAllow = usePermissionStore((s) => s.setAlwaysAllow);
  const subInit = useSubAgentStore((s) => s.init);
  const subRegister = useSubAgentStore((s) => s.register);
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [dragging, setDragging] = useState(false);
  const [attachedImages, setAttachedImages] = useState<ContentPartImage[]>([]);
  const [listening, setListening] = useState(false);
  const disabled = status === "sending" || status === "streaming" || status === "cancelling";
  const streaming = status === "streaming" || status === "sending" || status === "cancelling";
  const cancelling = status === "cancelling";
  const overLimit = value.length > MAX_INPUT_CHARS;
  const inputUsagePct = Math.min(100, (value.length / MAX_INPUT_CHARS) * 100);
  const inputNearLimit = inputUsagePct >= 80;
  const inputCritical = inputUsagePct >= 95 || overLimit;
  const tokenToneClass = overLimit
    ? "text-status-error font-semibold"
    : inputCritical
      ? "text-status-error"
      : inputNearLimit
        ? "text-amber-300"
        : "text-minimax-muted";
  const meterToneClass = overLimit
    ? "bg-status-error"
    : inputCritical
      ? "bg-status-error"
      : inputNearLimit
        ? "bg-amber-400"
        : "bg-minimax-accent";
  const imageInputRef = useRef<HTMLInputElement>(null);
  const recognitionRef = useRef<any>(null);

  // Auto-grow textarea up to ~8 rows — shrink back when text is deleted.
  // Setting height to "0px" first forces the browser to recalculate
  // scrollHeight from the actual content, preventing stale height values.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);

  useEffect(() => {
    const onSuggestion = (e: Event) => {
      const ce = e as CustomEvent<string>;
      if (typeof ce.detail === "string") {
        setValue(ce.detail);
        ref.current?.focus();
      }
    };
    window.addEventListener(SUGGESTION_EVENT, onSuggestion as EventListener);
    return () =>
      window.removeEventListener(SUGGESTION_EVENT, onSuggestion as EventListener);
  }, []);

  // Fetch the agent list lazily — used by the @-picker.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = loadAgents
          ? await loadAgents()
          : (await typedIPC.listAgents()).agents;
        if (!cancelled) setAgents(list.filter((a) => a.enabled));
      } catch (err) {
        if (!cancelled) setAgentsError(err instanceof Error ? err.message : String(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadAgents]);

  const filtered = useMemo(() => {
    if (!picker.open) return [];
    const q = picker.query.toLowerCase();
    return agents
      .filter(
        (a) =>
          !q ||
          a.id.toLowerCase().includes(q) ||
          a.name.toLowerCase().includes(q),
      )
      .slice(0, 6);
  }, [agents, picker.open, picker.query]);

  const closePicker = useCallback(() => setPicker(INITIAL_PICKER), []);

  const handlePickerSelect = useCallback(
    async (agent: AgentInfo) => {
      const el = ref.current;
      // Build the prompt by stripping the @token (everything up to and
      // including the active @ match). The token sits between
      // ``picker.anchor`` and the caret; the part of the textarea
      // after the caret is preserved so multi-line prompts survive.
      const caret = el?.selectionStart ?? value.length;
      const head = value.slice(0, picker.anchor);
      const tail = value.slice(caret);
      const promptText = tail.trim() || "(no prompt)";
      const marker = `@${agent.name} `;
      const newValue = `${head}${marker}${tail}`;
      setValue(newValue);
      closePicker();
      ref.current?.focus();
      // Best-effort: append the user's chosen agent as a sentinel
      // user-message so the chat stream shows the trigger, then
      // spawn the sub-agent.
      const sessionId = useSessionStore.getState().currentSessionId;
      const runId = `run_${Math.random().toString(36).slice(2, 10)}`;
      try {
        await subInit();
        subRegister({
          run_id: runId,
          agent_id: agent.id,
          agent_name: agent.name,
          display_name: agent.name,
          parent_session_id: sessionId ?? undefined,
          prompt: promptText,
          status: "started",
          progress: 0,
          summary: `queued for ${agent.name}`,
          started_at: Date.now(),
          updated_at: Date.now(),
        });
        await typedIPC.spawnSubagent({
          agent_id: agent.id,
          agent_name: agent.name,
          prompt: promptText,
          parent_session_id: sessionId ?? undefined,
          display_name: agent.name,
          run_id: runId,
        });
        // Mirror the trigger to the chat stream as a local user msg
        // so the user sees the pick in history — no backend round-trip.
        addLocalMessage(`${marker}${promptText}`);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        subRegister({
          run_id: runId,
          agent_id: agent.id,
          agent_name: agent.name,
          display_name: agent.name,
          parent_session_id: sessionId ?? undefined,
          prompt: promptText,
          status: "failed",
          progress: 1,
          summary: "failed",
          error: message,
          started_at: Date.now(),
          updated_at: Date.now(),
          finished_at: Date.now(),
        });
        toast.error("Sub-agent spawn failed", message);
      }
    },
    [value, picker.anchor, closePicker, subInit, subRegister, addLocalMessage],
  );

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const next = e.target.value;
    setValue(next);
    // Detect the @-token at-or-before the caret.
    const caret = e.target.selectionStart ?? next.length;
    const head = next.slice(0, caret);
    const match = /(^|\s)@([\w-]*)$/.exec(head);
    if (match) {
      const anchor = caret - match[2].length - 1; // -1 for the "@" itself
      setPicker({ open: true, query: match[2], cursor: 0, anchor });
    } else if (picker.open) {
      setPicker(INITIAL_PICKER);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "?") {
      e.stopPropagation();
    }
    if (picker.open && filtered.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setPicker((p) => ({ ...p, cursor: (p.cursor + 1) % filtered.length }));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setPicker((p) => ({
          ...p,
          cursor: (p.cursor - 1 + filtered.length) % filtered.length,
        }));
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        const idx = picker.cursor >= 0 ? picker.cursor : 0;
        e.preventDefault();
        void handlePickerSelect(filtered[idx]);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closePicker();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSubmit(e);
    }
  };

  /** Read one or more files and inject code blocks into the textarea. */
  const processFiles = useCallback(
    (files: File[]) => {
      const remaining = MAX_ATTACHMENTS - attachedFiles.length;
      if (remaining <= 0) {
        toast.info("文件数量上限", `最多同时附加 ${MAX_ATTACHMENTS} 个文件`);
        return;
      }
      const batch = files.slice(0, remaining);
      if (files.length > remaining) {
        toast.info("文件数量上限", `仅附加前 ${remaining} 个文件（上限 ${MAX_ATTACHMENTS}）`);
      }
      for (const file of batch) {
        const reader = new FileReader();
        reader.onload = () => {
          const raw = reader.result as string;
          const truncated = raw.length > MAX_FILE_CHARS;
          const snippet = truncated ? raw.slice(0, MAX_FILE_CHARS) + "\n… (truncated)" : raw;
          const ext = file.name.includes(".") ? file.name.split(".").pop()! : "txt";
          const block = `\n\`\`\`${ext}\n${snippet}\n\`\`\`\n`;
          setAttachedFiles((prev) => {
            if (prev.length >= MAX_ATTACHMENTS) return prev;
            return [...prev, { name: file.name }];
          });
          setValue((v) => v + block);
          ref.current?.focus();
        };
        reader.onerror = () => toast.error("Failed to read file", file.name);
        reader.readAsText(file);
      }
    },
    [attachedFiles.length],
  );

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (files.length > 0) processFiles(files);
      // Reset so the same file can be re-selected.
      e.target.value = "";
    },
    [processFiles],
  );

  const removeFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // ── Image handling ──────────────────────────────────────────
  const MAX_IMAGES = 5;

  const processImageFiles = useCallback(
    (files: File[]) => {
      const remaining = MAX_IMAGES - attachedImages.length;
      if (remaining <= 0) {
        toast.info("图片数量上限", `最多同时附加 ${MAX_IMAGES} 张图片`);
        return;
      }
      const batch = files.slice(0, remaining);
      for (const file of batch) {
        if (!file.type.startsWith("image/")) continue;
        const reader = new FileReader();
        reader.onload = () => {
          const dataUrl = reader.result as string;
          // dataUrl is "data:<media>;base64,<data>" — extract base64 part
          const base64 = dataUrl.split(",")[1] || "";
          const mediaType = file.type || "image/png";
          setAttachedImages((prev) => {
            if (prev.length >= MAX_IMAGES) return prev;
            return [...prev, { type: "image", media_type: mediaType, data: base64 }];
          });
        };
        reader.onerror = () => toast.error("Failed to read image", file.name);
        reader.readAsDataURL(file);
      }
    },
    [attachedImages.length],
  );

  const handleImageSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (files.length > 0) processImageFiles(files);
      e.target.value = "";
    },
    [processImageFiles],
  );

  const removeImage = useCallback((index: number) => {
    setAttachedImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  // ── Voice input (SpeechRecognition) ────────────────────────
  const toggleVoice = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      toast.info("语音输入不可用", "当前浏览器不支持 SpeechRecognition API");
      return;
    }

    if (listening && recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
      setListening(false);
      return;
    }

    const recognition = new SR();
    recognition.lang = "zh-CN";
    recognition.continuous = false;
    recognition.interimResults = true;

    recognition.onresult = (event: any) => {
      let transcript = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        transcript += event.results[i][0].transcript;
      }
      if (transcript) {
        setValue((v) => {
          // Append voice text to existing content
          const sep = v && !v.endsWith(" ") && !v.endsWith("\n") ? " " : "";
          return v + sep + transcript;
        });
      }
    };

    recognition.onend = () => {
      setListening(false);
      recognitionRef.current = null;
    };

    recognition.onerror = (event: any) => {
      setListening(false);
      recognitionRef.current = null;
      if (event.error !== "no-speech") {
        toast.error("语音识别出错", event.error);
      }
    };

    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
  }, [listening]);

  // ── Drag-and-drop ───────────────────────────────────────────
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.types.includes("Files")) setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragging(false);
      const files = Array.from(e.dataTransfer.files).filter((f) => {
        const ext = f.name.includes(".") ? `.${f.name.split(".").pop()!.toLowerCase()}` : "";
        return ACCEPTED_EXTS.split(",").includes(ext) || f.type.startsWith("text/");
      });
      if (files.length > 0) processFiles(files);
    },
    [processFiles],
  );

  const handleSubmit = async (e?: React.FormEvent | React.KeyboardEvent) => {
    e?.preventDefault();
    if (disabled) return;
    if (overLimit) return;

    const hasText = value.trim().length > 0;
    const hasImages = attachedImages.length > 0;
    if (!hasText && !hasImages) return;

    if (hasImages) {
      // Build multimodal ContentPart array
      const parts: import("../types/ipc").ContentPart[] = [];
      if (hasText) {
        parts.push({ type: "text", text: value.trim() });
      }
      parts.push(...attachedImages);
      setValue("");
      setAttachedFiles([]);
      setAttachedImages([]);
      closePicker();
      await send(parts);
    } else {
      const text = value;
      setValue("");
      setAttachedFiles([]);
      setAttachedImages([]);
      closePicker();
      await send(text);
    }
  };

  const handleToggleAlwaysAllow = () => {
    const next = !alwaysAllow;
    setAlwaysAllow(next);
    toast.info(
      next ? "始终授权已开启" : "始终授权已关闭",
      next
        ? "所有 tool 调用将自动放行（仅当前会话）"
        : "tool 调用将再次弹窗询问",
    );
  };

  return (
    <form
      onSubmit={handleSubmit}
      data-testid={testId}
      data-floating="true"
      className="pointer-events-none fixed inset-x-0 bottom-6 z-20 flex justify-center px-4"
    >
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={
          "pointer-events-auto relative w-full max-w-[720px] rounded-xl border bg-minimax-panel/95 shadow-2xl backdrop-blur supports-[backdrop-filter]:bg-minimax-panel/80 " +
          (dragging
            ? "border-minimax-accent ring-2 ring-minimax-accent/30"
            : "border-minimax-border")
        }
      >
        {/* Drop zone overlay */}
        {dragging && (
          <div
            data-testid="message-input-dropzone"
            className="absolute inset-0 z-10 flex items-center justify-center rounded-xl bg-minimax-accent/5"
          >
            <span className="text-xs font-medium text-minimax-accent">
              Drop files here…
            </span>
          </div>
        )}
        {attachedFiles.length > 0 && (
          <div className="flex flex-wrap items-center gap-1 border-b border-minimax-border px-3 py-1.5 text-xs text-minimax-muted">
            <Paperclip size={10} className="shrink-0" />
            {attachedFiles.map((f, i) => (
              <span
                key={`${f.name}-${i}`}
                data-testid={`message-attached-file-${i}`}
                className="inline-flex items-center gap-1 rounded bg-minimax-border/50 px-1.5 py-0.5"
              >
                <span className="truncate max-w-[120px]">{f.name}</span>
                <button
                  type="button"
                  aria-label={`Remove ${f.name}`}
                  className="rounded p-0.5 hover:bg-minimax-border hover:text-minimax-fg"
                  onClick={() => removeFile(i)}
                >
                  <X size={8} />
                </button>
              </span>
            ))}
          </div>
        )}
        {attachedImages.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5 border-b border-minimax-border px-3 py-1.5">
            {attachedImages.map((img, i) => (
              <ImagePreview
                key={`img-${i}`}
                data={img.data}
                mediaType={img.media_type}
                index={i}
                onRemove={() => removeImage(i)}
              />
            ))}
          </div>
        )}
        <div className="flex items-end gap-2 px-2.5 py-2">
          <button
            type="button"
            aria-label="Attach file"
            data-testid="message-attach-btn"
            onClick={() => fileInputRef.current?.click()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title="Attach a text file"
          >
            <Paperclip size={14} />
          </button>
          <button
            type="button"
            aria-label="Attach image"
            data-testid="message-image-btn"
            onClick={() => imageInputRef.current?.click()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg"
            title="Attach an image"
          >
            <Camera size={14} />
          </button>
          <input
            ref={imageInputRef}
            type="file"
            accept="image/*"
            multiple
            className="hidden"
            data-testid="message-image-input"
            onChange={handleImageSelect}
          />
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTS}
            className="hidden"
            data-testid="message-file-input"
            onChange={handleFileSelect}
          />
          <textarea
            ref={ref}
            value={value}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask MiniMax anything…  (Enter to send · @agent to spawn a sub-agent)"
            rows={1}
            data-testid="message-input-textarea"
            disabled={disabled}
            className="flex-1 resize-none bg-transparent px-1 py-1.5 text-sm text-minimax-fg placeholder:text-minimax-muted focus:outline-none"
          />
          {streaming ? (
            <button
              type="button"
              data-testid="message-input-cancel"
              onClick={() => void cancel()}
              disabled={cancelling}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-minimax-border text-minimax-fg transition-colors duration-200 hover:bg-red-500/20 disabled:cursor-wait disabled:opacity-70"
              title={cancelling ? "Stopping..." : "Stop"}
              aria-label={cancelling ? "Stopping..." : "Stop"}
            >
              {cancelling ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
            </button>
          ) : (
            <>
              <button
                type="button"
                aria-label={listening ? "Stop voice input" : "Start voice input"}
                data-testid="message-voice-btn"
                onClick={toggleVoice}
                className={
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-md " +
                  (listening
                    ? "bg-red-500/20 text-status-error animate-pulse"
                    : "text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg")
                }
                title={listening ? "Listening… click to stop" : "Voice input"}
              >
                {listening ? <MicOff size={14} /> : <Mic size={14} />}
              </button>
              <button
                type="submit"
                data-testid="message-input-send"
                disabled={(!value.trim() && attachedImages.length === 0) || overLimit}
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-minimax-accent text-white disabled:opacity-40"
                title="Send"
                aria-label="Send"
              >
                <Send size={14} />
              </button>
            </>
          )}
        </div>
        {picker.open && (
          <div
            data-testid="message-input-agent-picker"
            className="absolute bottom-full left-2.5 right-2.5 mb-1.5 overflow-hidden rounded-md border border-minimax-border bg-minimax-panel shadow-lg"
          >
            <div className="flex items-center gap-1 border-b border-minimax-border/60 px-2 py-1 text-[11px] uppercase tracking-wider text-minimax-muted">
              <AtSign size={10} />
              <span>Spawn sub-agent</span>
            </div>
            {agentsError ? (
              <div className="px-2 py-1.5 text-[11px] text-status-error" title={agentsError}>
                Failed to load agents
              </div>
            ) : filtered.length === 0 ? (
              <div className="px-2 py-1.5 text-[11px] italic text-minimax-muted">
                No agents match “{picker.query}”
              </div>
            ) : (
              <ul data-testid="message-input-agent-picker-list">
                {filtered.map((a, i) => (
                  <li key={a.id}>
                    <button
                      type="button"
                      data-testid={`message-input-agent-picker-item-${a.id}`}
                      onClick={() => void handlePickerSelect(a)}
                      className={
                        "flex w-full items-center gap-2 px-2 py-1 text-left text-[11px] " +
                        (i === picker.cursor
                          ? "bg-minimax-accent/10 text-minimax-fg"
                          : "text-minimax-fg/90 hover:bg-minimax-border/40")
                      }
                    >
                      <Bot size={10} className="text-minimax-accent" />
                      <span className="font-medium">{a.name}</span>
                      <span className="font-mono text-[11px] text-minimax-muted">
                        @{a.id}
                      </span>
                      <span className="flex-1 truncate text-[11px] text-minimax-muted">
                        {a.description}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
        <div className="flex items-center justify-between border-t border-minimax-border/60 px-2.5 py-1.5">
          <button
            type="button"
            role="switch"
            aria-checked={alwaysAllow}
            data-testid="chat-input-always-allow"
            onClick={handleToggleAlwaysAllow}
            className={
              "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition-colors " +
              (alwaysAllow
                ? "text-emerald-300 hover:bg-emerald-500/10"
                : "text-minimax-muted hover:bg-minimax-border hover:text-minimax-fg")
            }
            title={
              alwaysAllow
                ? "始终授权已开启 — tool 调用将自动放行"
                : "始终授权：下次 tool 调用前不再询问"
            }
          >
            {alwaysAllow ? <ShieldCheck size={11} /> : <Shield size={11} />}
            {alwaysAllow ? "始终授权：开" : "始终授权"}
          </button>
          <div className="flex items-center gap-2">
            {cancelling && (
              <span
                data-testid="message-input-stopping"
                className="text-[11px] text-amber-300 transition-opacity duration-200"
              >
                正在停止...
              </span>
            )}
            <ContextIndicator />
            <div className="flex min-w-[92px] flex-col items-end gap-1">
              <span
                data-testid="message-input-token-count"
                className={`text-[11px] transition-colors duration-200 ${tokenToneClass}`}
              >
                {value.length}/{MAX_INPUT_CHARS}
              </span>
              <div
                data-testid="message-input-token-meter"
                className="h-0.5 w-full overflow-hidden rounded-full bg-minimax-border/60"
                aria-hidden
              >
                <div
                  className={`h-full rounded-full transition-all duration-200 ${meterToneClass}`}
                  style={{ width: `${inputUsagePct}%` }}
                />
              </div>
            </div>
            <span
              data-testid="message-input-token-warning"
              className={`min-w-[74px] text-right text-[11px] transition-opacity duration-200 ${
                inputNearLimit ? "opacity-100" : "opacity-0"
              } ${inputCritical ? "text-status-error" : "text-amber-300"}`}
              aria-live="polite"
            >
              {overLimit ? "已超限" : inputCritical ? "即将超限" : "接近上限"}
            </span>
            <span className="sr-only" aria-live="polite">
              {value.length}/{MAX_INPUT_CHARS}
            </span>
            <ModelSelector variant="inline" />
          </div>
        </div>
      </div>
    </form>
  );
}
