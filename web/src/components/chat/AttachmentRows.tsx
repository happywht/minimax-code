/**
 * AttachmentRows — the chip strip for attached text files and the
 * thumbnail strip for attached images, rendered above the textarea.
 */
import { Paperclip, X } from "lucide-react";
import { ImagePreview } from "../panels/ImagePreview";
import type { ContentPartImage } from "../../types/ipc";
import type { AttachedFile } from "./constants";

export interface AttachmentRowsProps {
  attachedFiles: AttachedFile[];
  attachedImages: ContentPartImage[];
  onRemoveFile: (index: number) => void;
  onRemoveImage: (index: number) => void;
}

export function AttachmentRows({
  attachedFiles,
  attachedImages,
  onRemoveFile,
  onRemoveImage,
}: AttachmentRowsProps): JSX.Element | null {
  if (attachedFiles.length === 0 && attachedImages.length === 0) return null;

  return (
    <>
      {attachedFiles.length > 0 && (
        <div className="flex flex-wrap items-center gap-1 border-b border-line px-3 py-1.5 text-xs text-ink-2">
          <Paperclip size={10} className="shrink-0" />
          {attachedFiles.map((f, i) => (
            <span
              key={`${f.name}-${i}`}
              data-testid={`message-attached-file-${i}`}
              className="inline-flex items-center gap-1 rounded-md border border-line bg-surface-2 px-1.5 py-0.5"
            >
              <span className="max-w-[120px] truncate">{f.name}</span>
              <button
                type="button"
                aria-label={`Remove ${f.name}`}
                className="rounded p-0.5 text-ink-2 transition-colors duration-150 hover:bg-surface-3 hover:text-ink-0"
                onClick={() => onRemoveFile(i)}
              >
                <X size={8} />
              </button>
            </span>
          ))}
        </div>
      )}
      {attachedImages.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-3 py-1.5">
          {attachedImages.map((img, i) => (
            <ImagePreview
              key={`img-${i}`}
              data={img.data}
              mediaType={img.media_type}
              index={i}
              onRemove={() => onRemoveImage(i)}
            />
          ))}
        </div>
      )}
    </>
  );
}
