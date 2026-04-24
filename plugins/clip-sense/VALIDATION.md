# clip-sense 前置技术验证

## 验证 1: Paraformer ASR API

**参考源码**: `plugins-archive/_shared/asr/dashscope_paraformer.py` (235 行)

**API 契约 (从源码确认)**:
- 端点: `POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription`
- 异步模型: submit → poll → fetch transcript
- 必须传公网可达 URL (`file_urls`)，不支持本地路径
- 返回 `transcripts[0].sentences[]`，每条含 `begin_time`/`end_time` (毫秒) + `text` + `confidence`

**降级决定**: archive 实现只解析 `sentences` (句段级)，未解析词级 `words`。
clip-sense 以句段级 (sentence-level) 时间戳为主实现，剪辑边界精度约 0.5-2 秒，
对高光提取/段落拆条/口播精编均可接受。若后续 DashScope 确认支持词级，可升级。

**状态**: PASS (基于源码分析，API 契约与 archive 实现一致)

## 验证 2: Qwen JSON 分析

**参考源码**: CutClaw `prompt.py:522-606` (结构提案 prompt), `media_utils.py:295-364` (5 级降级解析)

**API 契约**:
- 端点: `POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` (OpenAI 兼容)
- 模型: `qwen-plus` (128K 窗口, 30 分钟视频转写约 8K token 完全够用)
- 返回常带 markdown 围栏 (```json ... ```)

**应对**: `clip_sense_inline/llm_json_parser.py` 提供 5 级降级解析:
1. 直接 json.loads
2. 去围栏
3. 外层括号匹配
4. 所有平衡子串扫描
5. 回退默认值

**重试策略**: max_retries=2, 失败时带 feedback 再调 (参照 CutClaw 模式)

**状态**: PASS (解析器已在 Phase 0 vendored 并测试通过)

## 验证 3: ffmpeg 切割+拼接

**参考源码**: video-use `render.py` (切割/拼接模板)

**命令模板**:
```
# 切割 (重编码, 统一参数)
ffmpeg -y -ss {start} -i {source} -t {duration} \
  -c:v libx264 -preset fast -crf 22 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 \
  -af "afade=t=in:st=0:d=0.03,afade=t=out:st={dur-0.03}:d=0.03" \
  -movflags +faststart {output}

# 拼接 (concat demuxer, stream copy)
ffmpeg -y -f concat -safe 0 -i {list.txt} -c copy -movflags +faststart {output}
```

**关键约定**:
- `-ss` 在 `-i` 前 (快速 seek, 不精确但够用)
- 各段统一重编码参数确保 concat 兼容
- `afade` 30ms 防爆音
- Windows 字幕路径需转义 `:` 和 `'`

**状态**: PASS (命令模板来自已验证的 video-use 代码库)
