// ─── Shared constants for Setup Center ───

import type { ProviderInfo } from "./types";
import SHARED_PROVIDERS from "@shared/providers.json";

// 内置 Provider 列表（打包模式下 venv 不可用时作为回退）
// 数据来源：@shared/providers.json（与 Python 后端共享同一份文件）
// registry_class 字段仅 Python 使用，前端忽略
export const BUILTIN_PROVIDERS: ProviderInfo[] = SHARED_PROVIDERS as ProviderInfo[];

/** STT 推荐模型（按 provider slug 索引） */
export const STT_RECOMMENDED_MODELS: Record<string, { id: string; note: string }[]> = {
  "openai":          [{ id: "gpt-4o-transcribe", note: "推荐" }, { id: "whisper-1", note: "" }],
  "dashscope":       [{ id: "qwen3-asr-flash", note: "推荐 (文件识别 ≤5min)" }],
  "dashscope-intl":  [{ id: "qwen3-asr-flash", note: "recommended (file ≤5min)" }],
  "groq":            [{ id: "whisper-large-v3-turbo", note: "推荐" }, { id: "whisper-large-v3", note: "" }],
  "siliconflow":     [{ id: "FunAudioLLM/SenseVoiceSmall", note: "推荐" }, { id: "TeleAI/TeleSpeechASR", note: "" }],
  "siliconflow-intl":[{ id: "FunAudioLLM/SenseVoiceSmall", note: "推荐" }, { id: "TeleAI/TeleSpeechASR", note: "" }],
};

export const PIP_INDEX_PRESETS: { id: "official" | "tuna" | "aliyun" | "custom"; label: string; url: string }[] = [
  { id: "aliyun", label: "阿里云（默认）", url: "https://mirrors.aliyun.com/pypi/simple/" },
  { id: "tuna", label: "清华 TUNA", url: "https://pypi.tuna.tsinghua.edu.cn/simple" },
  { id: "official", label: "官方 PyPI", url: "https://pypi.org/simple/" },
  { id: "custom", label: "自定义…", url: "" },
];

