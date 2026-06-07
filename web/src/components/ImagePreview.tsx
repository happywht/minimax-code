/**
 * Image thumbnail preview for multimodal message input.
 *
 * Displays a base64-encoded image as a small thumbnail with a
 * delete button overlay. Used inside MessageInput when the user
 * attaches screenshots or images.
 */
import { X } from "lucide-react";

export interface ImagePreviewProps {
  /** base64 data URI or raw base64 string. */
  data: string;
  /** MIME type, e.g. "image/png". */
  mediaType?: string;
  /** Callback to remove this image. */
  onRemove: () => void;
  /** Index for test targeting. */
  index?: number;
}

export function ImagePreview({
  data,
  mediaType = "image/png",
  onRemove,
  index = 0,
}: ImagePreviewProps): JSX.Element {
  // Build a data URI if not already one
  const src = data.startsWith("data:")
    ? data
    : `data:${mediaType};base64,${data}`;

  return (
    <div
      data-testid={`image-preview-${index}`}
      className="group relative inline-flex h-14 w-14 items-center justify-center overflow-hidden rounded-md border border-minimax-border bg-minimax-border/30"
    >
      <img
        src={src}
        alt={`Attached image ${index + 1}`}
        className="h-full w-full object-cover"
      />
      <button
        type="button"
        aria-label={`Remove image ${index + 1}`}
        data-testid={`image-preview-remove-${index}`}
        onClick={onRemove}
        className="absolute right-0.5 top-0.5 flex h-4 w-4 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity group-hover:opacity-100"
      >
        <X size={10} />
      </button>
    </div>
  );
}
