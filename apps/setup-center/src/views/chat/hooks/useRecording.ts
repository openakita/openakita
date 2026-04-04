import { useState, useRef, useCallback, useEffect } from "react";
import type { ChatAttachment } from "../../../types";
import { genId } from "../../../utils";

interface UseRecordingOptions {
  uploadFile: (file: Blob, filename: string) => Promise<string>;
  apiBaseRef: React.MutableRefObject<string>;
  setPendingAttachments: React.Dispatch<React.SetStateAction<ChatAttachment[]>>;
  notifyError: (msg: string) => void;
  t: (key: string, fallback: string) => string;
}

export function useRecording({
  uploadFile,
  apiBaseRef,
  setPendingAttachments,
  notifyError,
  t,
}: UseRecordingOptions) {
  const [isRecording, setIsRecording] = useState(false);
  const [recordingDuration, setRecordingDuration] = useState(0);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const blobUrlsRef = useRef<string[]>([]);
  const recordingTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Ref mirrors for stable closure access inside onstop callback
  const uploadFileRef = useRef(uploadFile);
  const setPendingAttRef = useRef(setPendingAttachments);
  const notifyErrorRef = useRef(notifyError);
  const tRef = useRef(t);
  useEffect(() => { uploadFileRef.current = uploadFile; }, [uploadFile]);
  useEffect(() => { setPendingAttRef.current = setPendingAttachments; }, [setPendingAttachments]);
  useEffect(() => { notifyErrorRef.current = notifyError; }, [notifyError]);
  useEffect(() => { tRef.current = t; }, [t]);

  const toggleRecording = useCallback(async () => {
    if (isRecording) {
      mediaRecorderRef.current?.stop();
      setIsRecording(false);
      if (recordingTimerRef.current) {
        clearInterval(recordingTimerRef.current);
        recordingTimerRef.current = null;
      }
      setRecordingDuration(0);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mimeType = MediaRecorder.isTypeSupported("audio/webm")
        ? "audio/webm"
        : MediaRecorder.isTypeSupported("audio/mp4")
          ? "audio/mp4"
          : MediaRecorder.isTypeSupported("audio/ogg")
            ? "audio/ogg"
            : "";
      const ext = mimeType.includes("mp4") ? "m4a" : mimeType.includes("ogg") ? "ogg" : "webm";
      const opts: MediaRecorderOptions = mimeType ? { mimeType } : {};
      const mediaRecorder = new MediaRecorder(stream, opts);
      const uploadId = genId();
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" });
        const localPreview = URL.createObjectURL(blob);
        blobUrlsRef.current.push(localPreview);
        const filename = `voice-${Date.now()}.${ext}`;
        const tempAtt: ChatAttachment = {
          type: "voice",
          name: filename,
          previewUrl: localPreview,
          size: blob.size,
          mimeType: mimeType || "audio/webm",
          _uploadId: uploadId,
        };
        setPendingAttRef.current((prev) => [...prev, tempAtt]);
        uploadFileRef.current(blob, filename)
          .then((serverUrl) => {
            setPendingAttRef.current((prev) =>
              prev.map((a) =>
                a._uploadId === uploadId ? { ...a, url: `${apiBaseRef.current}${serverUrl}` } : a,
              ),
            );
          })
          .catch(() => {
            notifyErrorRef.current(tRef.current("chat.voiceUploadFailed", "语音上传失败"));
            setPendingAttRef.current((prev) =>
              prev.filter((a) => a._uploadId !== uploadId || a.url),
            );
          });
        stream.getTracks().forEach((tr) => tr.stop());
      };

      mediaRecorderRef.current = mediaRecorder;
      mediaRecorder.start();
      setIsRecording(true);
      setRecordingDuration(0);
      recordingTimerRef.current = setInterval(() => setRecordingDuration((d) => d + 1), 1000);
    } catch (err: any) {
      const name = err?.name || "";
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        notifyErrorRef.current(tRef.current("chat.micPermissionDenied", "麦克风权限被拒绝，请在浏览器/系统设置中允许访问"));
      } else if (name === "NotFoundError") {
        notifyErrorRef.current(tRef.current("chat.micNotFound", "未检测到麦克风设备"));
      } else {
        notifyErrorRef.current(tRef.current("chat.micError", "无法访问麦克风，请检查浏览器权限设置"));
      }
    }
  }, [isRecording, apiBaseRef]);

  const cleanupRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      try { mediaRecorderRef.current.stop(); } catch { /* ignore */ }
    }
    mediaRecorderRef.current = null;
    if (recordingTimerRef.current) {
      clearInterval(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    for (const url of blobUrlsRef.current) {
      try { URL.revokeObjectURL(url); } catch {}
    }
    blobUrlsRef.current = [];
  }, []);

  useEffect(() => cleanupRecording, [cleanupRecording]);

  return {
    isRecording,
    recordingDuration,
    toggleRecording,
    cleanupRecording,
  };
}
