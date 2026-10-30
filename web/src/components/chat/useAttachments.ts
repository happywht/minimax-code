/**
 * useAttachments — file/image attachment state for the composer.
 *
 * Text files are read and injected into the draft as fenced code
 * blocks; images are read as base64 data for multimodal sends.
 * Also owns the drag-and-drop state and the hidden <input type=file>
 * refs + change handlers.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "../layout/ErrorBoundary";
import { strings } from "../../ui/strings";
import type { ContentPartImage } from "../../types/ipc";
import { ipc } from "../../ipc";
import { useSessionStore } from "../../stores/sessionStore";
import {
  ACCEPTED_EXTS,
  MAX_ATTACHMENTS,
  MAX_FILE_CHARS,
  MAX_IMAGES,
  type AttachedFile,
} from "./constants";

export interface ComposerAttachments {
  attachedFiles: AttachedFile[];
  attachedImages: ContentPartImage[];
  dragging: boolean;
  fileInputRef: React.RefObject<HTMLInputElement>;
  imageInputRef: React.RefObject<HTMLInputElement>;
  handleFileSelect: (e: React.ChangeEvent<HTMLInputElement>) => void;
  handleImageSelect: (e: React.ChangeEvent<HTMLInputElement>) => void;
  removeFile: (index: number) => void;
  removeImage: (index: number) => void;
  handleDragOver: (e: React.DragEvent) => void;
  handleDragLeave: (e: React.DragEvent) => void;
  handleDrop: (e: React.DragEvent) => void;
  /** Clear both file and image attachments (after a send). */
  clearAll: () => void;
}

export function useAttachments(
  textareaRef: React.RefObject<HTMLTextAreaElement>,
  setValue: React.Dispatch<React.SetStateAction<string>>,
): ComposerAttachments {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  const [attachedImages, setAttachedImages] = useState<ContentPartImage[]>([]);
  const [dragging, setDragging] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    let { currentSessionId: session, currentProjectId: project } = useSessionStore.getState();
    const reset = () => { generation.current += 1; setAttachedFiles([]); setAttachedImages([]); };
    const unsubscribe = useSessionStore.subscribe((state) => {
      if (state.currentSessionId !== session || state.currentProjectId !== project) {
        session = state.currentSessionId; project = state.currentProjectId; reset();
      }
    });
    const stopSidecar = ipc.onSideCar(reset);
    return () => { generation.current += 1; unsubscribe(); stopSidecar(); };
  }, []);

  /** Read one or more files and inject code blocks into the textarea. */
  const processFiles = useCallback(
    (files: File[]) => {
      const remaining = MAX_ATTACHMENTS - attachedFiles.length;
      if (remaining <= 0) {
        toast.info("文件数量上限", `最多同时附加 ${MAX_ATTACHMENTS} 个文件`);
        return;
      }
      const batch = files.slice(0, remaining);
      const origin = useSessionStore.getState();
      const session = origin.currentSessionId, project = origin.currentProjectId, host = ipc.agentBaseUrl;
      const currentGeneration = generation.current;
      let invalidated = false, pending = batch.length;
      const unsubscribe = useSessionStore.subscribe((state) => {
        if (state.currentSessionId !== session || state.currentProjectId !== project) invalidated = true;
      });
      const stopSidecar = ipc.onSideCar(() => { invalidated = true; });
      const finish = () => { if (--pending === 0) { unsubscribe(); stopSidecar(); } };
      if (files.length > remaining) {
        toast.info("文件数量上限", `仅附加前 ${remaining} 个文件（上限 ${MAX_ATTACHMENTS}）`);
      }
      for (const file of batch) {
        const reader = new FileReader();
        reader.onload = () => {
          finish();
          if (invalidated || generation.current !== currentGeneration || ipc.agentBaseUrl !== host) {
            toast.info("文件未插入", "读取期间会话、项目或连接发生变化，或草稿已提交；请重新选择文件。");
            return;
          }
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
          textareaRef.current?.focus();
        };
        reader.onerror = () => { finish(); toast.error(strings.chat.toast.fileReadFailed, file.name); };
        reader.onabort = finish;
        reader.readAsText(file);
      }
    },
    [attachedFiles.length, setValue, textareaRef],
  );

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
        const currentGeneration = generation.current;
        const reader = new FileReader();
        reader.onload = () => {
          if (generation.current !== currentGeneration) {
            toast.info("图片未插入", "读取期间会话、项目或连接发生变化，或草稿已提交；请重新选择图片。");
            return;
          }
          const dataUrl = reader.result as string;
          // dataUrl is "data:<media>;base64,<data>" — extract base64 part
          const base64 = dataUrl.split(",")[1] || "";
          const mediaType = file.type || "image/png";
          setAttachedImages((prev) => {
            if (prev.length >= MAX_IMAGES) return prev;
            return [...prev, { type: "image", media_type: mediaType, data: base64 }];
          });
        };
        reader.onerror = () => toast.error(strings.chat.toast.imageReadFailed, file.name);
        reader.readAsDataURL(file);
      }
    },
    [attachedImages.length],
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

  const handleImageSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(e.target.files ?? []);
      if (files.length > 0) processImageFiles(files);
      e.target.value = "";
    },
    [processImageFiles],
  );

  const removeFile = useCallback((index: number) => {
    setAttachedFiles((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const removeImage = useCallback((index: number) => {
    setAttachedImages((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const clearAll = useCallback(() => {
    generation.current += 1;
    setAttachedFiles([]);
    setAttachedImages([]);
  }, []);

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

  return {
    attachedFiles,
    attachedImages,
    dragging,
    fileInputRef,
    imageInputRef,
    handleFileSelect,
    handleImageSelect,
    removeFile,
    removeImage,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    clearAll,
  };
}
