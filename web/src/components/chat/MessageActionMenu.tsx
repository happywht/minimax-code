/**
 * MessageActionMenu — small dropdown for per-message actions.
 */
import { useState, useRef, useEffect } from "react";
import { MoreHorizontal, Pencil, Trash2 } from "lucide-react";
import { IconButton } from "../../ui";

export interface MessageActionMenuProps {
  messageId: string;
  editable?: boolean;
  deletable?: boolean;
  onEdit?: (messageId: string) => void;
  onDelete?: (messageId: string) => void;
}

export function MessageActionMenu({
  messageId,
  editable = false,
  deletable = true,
  onEdit,
  onDelete,
}: MessageActionMenuProps): JSX.Element {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <IconButton
        size="sm"
        aria-label="Message actions"
        data-testid={`message-actions-${messageId}`}
        onClick={() => setOpen((v) => !v)}
        className="text-ink-2 hover:text-ink-0"
      >
        <MoreHorizontal size={12} />
      </IconButton>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-28 overflow-hidden rounded-md border border-line bg-surface-1 shadow-pop">
          {editable && (
            <button
              type="button"
              data-testid={`message-edit-${messageId}`}
              onClick={() => {
                setOpen(false);
                onEdit?.(messageId);
              }}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-ink-1 hover:bg-surface-3 hover:text-ink-0"
            >
              <Pencil size={12} /> 编辑
            </button>
          )}
          {deletable && (
            <button
              type="button"
              data-testid={`message-delete-${messageId}`}
              onClick={() => {
                setOpen(false);
                onDelete?.(messageId);
              }}
              className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs text-status-error hover:bg-[var(--status-error-subtle)]"
            >
              <Trash2 size={12} /> 删除
            </button>
          )}
        </div>
      )}
    </div>
  );
}
