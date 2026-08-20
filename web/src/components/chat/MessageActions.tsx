/**
 * Message-level actions: the hover-reveal copy overlay pinned to the
 * bubble corner, and the failed-message footer with the retry button.
 */
import { Check, Copy, RefreshCw } from "lucide-react";
import { useChat } from "../../stores";
import type { Message } from "../../types/ipc";
import { Button, IconButton } from "../../ui";
import { strings } from "../../ui/strings";
import { toast } from "../layout/ErrorBoundary";
import { useCopyFeedback } from "./useCopyFeedback";

export interface MessageCopyOverlayProps {
  messageId: string;
  /** Raw message text written to the clipboard. */
  text: string;
  /** True when the bubble sits on an accent fill (user messages). */
  onAccent?: boolean;
}

export function MessageCopyOverlay({
  messageId,
  text,
  onAccent = false,
}: MessageCopyOverlayProps): JSX.Element {
  const { copied, copy } = useCopyFeedback();

  const handleCopy = async () => {
    const ok = await copy(text);
    if (ok) {
      toast.success(strings.chat.actions.messageCopied);
    } else {
      toast.error(strings.chat.actions.copyFailed, strings.chat.actions.copyFailedDetail);
    }
  };

  return (
    <IconButton
      size="sm"
      data-testid={`message-copy-${messageId}`}
      onClick={() => void handleCopy()}
      aria-label={strings.chat.actions.copyMessage}
      title={strings.chat.actions.copyMessage}
      className={
        "absolute top-1.5 opacity-0 transition-all duration-200 focus:opacity-100 group-hover:opacity-100 " +
        (onAccent
          ? "left-1.5 !text-accent-contrast/80 hover:!bg-accent-contrast/10 hover:!text-accent-contrast"
          : "right-1.5")
      }
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
    </IconButton>
  );
}

export function FailedMessageFooter({ message }: { message: Message }): JSX.Element {
  const retryMessage = useChat((s) => s.retryMessage);

  return (
    <div className="mt-2 flex items-center justify-between gap-3 border-t border-status-error/20 pt-2">
      <span className="text-[11px] text-status-error/80">
        {message.error ?? message.text}
      </span>
      <Button
        variant="danger"
        size="sm"
        data-testid={`message-retry-${message.id}`}
        icon={<RefreshCw />}
        onClick={() => void retryMessage(message.id)}
      >
        {strings.chat.actions.retry}
      </Button>
    </div>
  );
}
