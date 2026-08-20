/**
 * Labeled copy button with transient "Copied" feedback.
 * Shared by code-block headers, mermaid headers, and
 * file-reference cards.
 */
import { Check, Copy } from "lucide-react";
import { Button } from "../../ui";
import { strings } from "../../ui/strings";
import { useCopyFeedback } from "./useCopyFeedback";

export interface CopyButtonProps {
  /** Raw text written to the clipboard. */
  text: string;
  testId?: string;
  title?: string;
  className?: string;
}

export function CopyButton({
  text,
  testId,
  title = strings.chat.actions.copy,
  className = "",
}: CopyButtonProps): JSX.Element {
  const { copied, copy } = useCopyFeedback();
  return (
    <Button
      variant="ghost"
      size="sm"
      data-testid={testId}
      title={title}
      aria-label={title}
      icon={copied ? <Check /> : <Copy />}
      onClick={() => void copy(text)}
      className={className}
    >
      {copied ? strings.chat.actions.copied : strings.chat.actions.copy}
    </Button>
  );
}
