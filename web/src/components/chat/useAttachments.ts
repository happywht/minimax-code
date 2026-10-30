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
  addSavedImage: (image: ContentPartImage) => string;
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
  const imagesRef = useRef<ContentPartImage[]>([]);
  const pendingImages = useRef(0);
  const [dragging, setDragging] = useState(false);
  const generation = useRef(0);

  useEffect(() => {
    let { currentSessionId: session, currentProjectId: project } = useSessionStore.getState();
    const reset = () => { generation.current += 1; imagesRef.current = []; setAttachedFiles([]); setAttachedImages([]); };
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
      const remaining = MAX_IMAGES - imagesRef.current.length - pendingImages.current;
      if (remaining <= 0) {
        toast.info("图片数量上限", `最多同时附加 ${MAX_IMAGES} 张图片`);
        return;
      }
      const batch = files.slice(0, remaining);
      if (files.length > remaining) {
        toast.info("图片数量上限", `仅选择前 ${remaining} 张图片；其余图片未加入。`);
      }
      for (const file of batch) {
        if (!file.type.startsWith("image/")) continue;
        const currentGeneration = generation.current;
        const reader = new FileReader();
        pendingImages.current += 1;
        reader.onload = () => {
          pendingImages.current -= 1;
          if (generation.current !== currentGeneration) {
            toast.info("图片未插入", "读取期间会话、项目或连接发生变化，或草稿已提交；请重新选择图片。");
            return;
          }
          const dataUrl = reader.result as string;
          // dataUrl is "data:<media>;base64,<data>" — extract base64 part
          const base64 = dataUrl.split(",")[1] || "";
          const mediaType = file.type || "image/png";
          if (imagesRef.current.length >= MAX_IMAGES) {
            toast.info("图片未插入", "图片数量已达上限，请移除后重新选择。");
            return;
          }
          imagesRef.current = [...imagesRef.current, { type: "image", media_type: mediaType, data: base64 }];
          setAttachedImages(imagesRef.current);
        };
        reader.onerror = () => { pendingImages.current -= 1; toast.error(strings.chat.toast.imageReadFailed, file.name); };
        reader.onabort = () => { pendingImages.current -= 1; };
        try { reader.readAsDataURL(file); }
        catch { pendingImages.current -= 1; toast.error(strings.chat.toast.imageReadFailed, file.name); }
      }
    },
    [],
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
    imagesRef.current = imagesRef.current.filter((_, i) => i !== index);
    setAttachedImages(imagesRef.current);
  }, []);

  const addSavedImage = useCallback((image: ContentPartImage): string => {
    if (pendingImages.current) return "本地图片仍在读取，请完成后重试。";
    const images = imagesRef.current;
    if (images.length >= MAX_IMAGES) return "草稿图片已达 5 张上限；请先移除一张。";
    const bytes = (data: string) => Math.floor(data.length * 3 / 4) - (data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0);
    if ([...images, image].some((item) => !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(item.media_type)) ||
        [...images, image].reduce((total, item) => total + bytes(item.data), 0) > 4 * 1024 * 1024) {
      return "加入后图片格式或总大小超出 Codex 限制；请先调整草稿图片。";
    }
    imagesRef.current = [...images, image];
    setAttachedImages(imagesRef.current);
    return "已加入当前草稿，尚未发送；请核对图片和预算。";
  }, []);

  const clearAll = useCallback(() => {
    generation.current += 1;
    setAttachedFiles([]);
    imagesRef.current = [];
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
    addSavedImage,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    clearAll,
  };
}
