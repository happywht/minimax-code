/**
 * Lifecycle status pill for assistant messages (Waiting / Sending /
 * Generating / Failed / Stopping... / Stopped).
 */
import { AlertCircle } from "lucide-react";
import type { MessageStatus } from "../../types/ipc";
import { Badge, Spinner, type BadgeTone } from "../../ui";
import { strings } from "../../ui/strings";

const STATUS_LABELS: Record<MessageStatus, string> = {
  queued: strings.chat.messageStatus.queued,
  sending: strings.chat.messageStatus.sending,
  streaming: strings.chat.messageStatus.streaming,
  completed: "",
  failed: strings.chat.messageStatus.failed,
  cancelling: strings.chat.messageStatus.cancelling,
  cancelled: strings.chat.messageStatus.cancelled,
};

const STATUS_TONES: Record<MessageStatus, BadgeTone> = {
  queued: "accent",
  sending: "accent",
  streaming: "accent",
  completed: "neutral",
  failed: "error",
  cancelling: "warning",
  cancelled: "neutral",
};

export interface MessageStatusBadgeProps {
  messageId: string;
  status: MessageStatus;
}

export function MessageStatusBadge({ messageId, status }: MessageStatusBadgeProps): JSX.Element {
  const isFailed = status === "failed";
  const isBusy = status === "queued" || status === "sending" || status === "cancelling";

  return (
    <div className="mb-1.5">
      <Badge tone={STATUS_TONES[status]} data-testid={`message-status-${messageId}`}>
        {isFailed ? (
          <AlertCircle size={11} />
        ) : isBusy ? (
          <Spinner size={10} />
        ) : null}
        {STATUS_LABELS[status]}
      </Badge>
    </div>
  );
}
