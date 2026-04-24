import { useState, useRef, useCallback, useEffect } from "react";
import type { ChatAttachment } from "../../../types";
import { genId } from "../../../utils";
import { readFileBase64, onDragDrop, IS_TAURI, logger } from "../../../platform";
import { notifyError } from "../../../utils/notify";
import { PASTE_CHAR_THRESHOLD } from "../utils/chatHelpers";

interface UseAttachmentsOptions {
  uploadFile: (file: Blob, filename: string) => Promise<string>;
  apiBaseRef: React.MutableRefObject<string>;
  setPendingAttachments: React.Dispatch<React.SetStateAction<ChatAttachment[]>>;
  activeConvId: string;
}

export function useAttachments({
  uploadFile,
  apiBaseRef,
  setPendingAttachments,
  activeConvId,
}: UseAttachmentsOptions) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [pastedLargeText, setPastedLargeText] = useState<{ text: string; lines: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => { setPastedLargeText(null); }, [activeConvId]);

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of Array.from(files)) {
      const uploadId = genId();
      const att: ChatAttachment = {
        type: file.type.startsWith("image/") ? "image"
          : file.type.startsWith("video/") ? "video"
            : file.type.startsWith("audio/") ? "voice"
              : file.type === "application/pdf" ? "document" : "file",
        name: file.name,
        size: file.size,
        mimeType: file.type,
        _uploadId: uploadId,
      };
      if (att.type === "video" && file.size > 7 * 1024 * 1024) {
        notifyError(`视频文件过大 (${(file.size / 1024 / 1024).toFixed(1)}MB)，桌面端最大支持 7MB（base64 编码后需 < 10MB）`);
        continue;
      }
      if (att.type === "image" || att.type === "video") {
        const reader = new FileReader();
        reader.onload = () => {
          att.previewUrl = att.type === "image" ? reader.result as string : undefined;
          att.url = reader.result as string;
          setPendingAttachments((prev) => [...prev, att]);
        };
        reader.onerror = () => {
          notifyError(`文件读取失败: ${file.name}`);
        };
        reader.readAsDataURL(file);
      } else {
        setPendingAttachments((prev) => [...prev, att]);
        uploadFile(file, file.name)
          .then((serverUrl) => {
            setPendingAttachments((prev) =>
              prev.map((a) => a._uploadId === uploadId
                ? { ...a, url: `${apiBaseRef.current}${serverUrl}` } : a),
            );
          })
          .catch(() => {
            notifyError(`文件上传失败: ${file.name}`);
            setPendingAttachments((prev) =>
              prev.filter((a) => a._uploadId !== uploadId || a.url));
          });
      }
    }
    e.target.value = "";
  }, [uploadFile, apiBaseRef, setPendingAttachments]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    const plainText = e.clipboardData?.getData("text/plain") || "";
    if (plainText.length > PASTE_CHAR_THRESHOLD) {
      e.preventDefault();
      const lineCount = plainText.split("\n").length;
      setPastedLargeText({ text: plainText, lines: lineCount });
      return;
    }

    for (const item of Array.from(items)) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (!file) continue;
        const reader = new FileReader();
        reader.onload = () => {
          setPendingAttachments((prev) => [...prev, {
            type: "image",
            name: `粘贴图片-${Date.now()}.png`,
            previewUrl: reader.result as string,
            url: reader.result as string,
            size: file.size,
            mimeType: file.type,
          }]);
        };
        reader.readAsDataURL(file);
      }
    }
  }, [setPendingAttachments]);

  // Tauri native drag-drop
  useEffect(() => {
    if (!IS_TAURI) return;
    let cancelled = false;
    let unlisten: (() => void) | null = null;

    const mimeMap: Record<string, string> = {
      png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg",
      gif: "image/gif", webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
      mp4: "video/mp4", webm: "video/webm", avi: "video/x-msvideo",
      mov: "video/quicktime", mkv: "video/x-matroska",
      pdf: "application/pdf", txt: "text/plain", md: "text/plain",
      json: "application/json", csv: "text/csv",
    };

    const FILE_MAX_SIZE = 50 * 1024 * 1024;

    // 把 "data:<mime>;base64,<b64>" 解析为 Blob。失败返回 null。
    const dataUrlToBlob = (dataUrl: string, mimeType: string): Blob | null => {
      try {
        const commaIdx = dataUrl.indexOf(",");
        const b64 = commaIdx >= 0 ? dataUrl.slice(commaIdx + 1) : dataUrl;
        const bin = atob(b64);
        const bytes = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
        return new Blob([bytes], { type: mimeType });
      } catch {
        return null;
      }
    };

    const handleDroppedPaths = (paths: string[]) => {
      logger.info("Chat.Upload", "drag.drop", { count: paths.length });
      for (const filePath of paths) {
        const name = filePath.split(/[\\/]/).pop() || "file";
        const ext = (name.split(".").pop() || "").toLowerCase();
        const isImage = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"].includes(ext);
        const isVideo = ["mp4", "webm", "avi", "mov", "mkv"].includes(ext);
        const mimeType = mimeMap[ext] || "application/octet-stream";
        readFileBase64(filePath)
          .then((dataUrl) => {
            if (cancelled) return;
            const commaIdx = dataUrl.indexOf(",");
            const base64Len = commaIdx >= 0 ? dataUrl.length - commaIdx - 1 : dataUrl.length;
            const estimatedSize = base64Len * 3 / 4;
            if (estimatedSize > FILE_MAX_SIZE) {
              notifyError(`文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 50MB`);
              return;
            }
            if (isVideo) {
              const VIDEO_MAX_SIZE = 7 * 1024 * 1024;
              if (estimatedSize > VIDEO_MAX_SIZE) {
                notifyError(`视频文件过大 (${(estimatedSize / 1024 / 1024).toFixed(1)}MB)，最大支持 7MB（base64 编码后需 < 10MB）`);
                return;
              }
            }

            // 图片 / 视频：保留 dataUrl，后端 multimodal 路径会直接消费（不会拼进文本 prompt）
            if (isImage || isVideo) {
              setPendingAttachments((prev) => [...prev, {
                type: isImage ? "image" : "video",
                name,
                previewUrl: isImage ? dataUrl : undefined,
                url: dataUrl,
                size: estimatedSize,
                mimeType,
              }]);
              return;
            }

            // 文档 / 其他文件：必须上传到后端拿短 URL，否则 base64 dataUrl 会被
            // 拼进 LLM prompt 文本 → token 爆炸 + 被中间环节截断（→ "..."）
            // → 模型反复说"找不到文件 / 内容被截断"。与 handleFileSelect 路径保持一致。
            const blob = dataUrlToBlob(dataUrl, mimeType);
            if (!blob) {
              notifyError(`文件解码失败: ${name}`);
              logger.error("Chat.Upload", "DragDrop dataUrl decode failed", { name });
              return;
            }
            const uploadId = genId();
            const isPdf = ext === "pdf" || mimeType === "application/pdf";
            const att: ChatAttachment = {
              type: isPdf ? "document" : "file",
              name,
              size: estimatedSize,
              mimeType,
              _uploadId: uploadId,
            };
            setPendingAttachments((prev) => [...prev, att]);
            uploadFile(blob, name)
              .then((serverUrl) => {
                if (cancelled) return;
                setPendingAttachments((prev) => prev.map((a) =>
                  a._uploadId === uploadId
                    ? { ...a, url: `${apiBaseRef.current}${serverUrl}` }
                    : a,
                ));
              })
              .catch((err) => {
                notifyError(`文件上传失败: ${name}`);
                logger.error("Chat.Upload", "DragDrop uploadFile failed", { name, error: String(err) });
                setPendingAttachments((prev) => prev.filter((a) =>
                  a._uploadId !== uploadId || a.url));
              });
          })
          .catch((err) => {
            notifyError(`文件读取失败: ${name}`);
            logger.error("Chat", "DragDrop read_file_base64 failed", { name, error: String(err) });
          });
      }
    };

    onDragDrop({
      onEnter: () => { if (!cancelled) setDragOver(true); },
      onOver: () => { if (!cancelled) setDragOver(true); },
      onLeave: () => { if (!cancelled) setDragOver(false); },
      onDrop: (paths) => {
        if (cancelled) return;
        setDragOver(false);
        handleDroppedPaths(paths);
      },
    }).then((unsub) => { unlisten = unsub; });

    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    fileInputRef,
    pastedLargeText,
    setPastedLargeText,
    dragOver,
    setDragOver,
    handleFileSelect,
    handlePaste,
  };
}
