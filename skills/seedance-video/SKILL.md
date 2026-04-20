---
name: openakita/skills@seedance-video
description: "Generate AI videos using ByteDance Seedance models via Volcengine Ark API. Supports text-to-video, image-to-video (first frame, first+last frame), multimodal reference (images+videos+audio), video editing, video extension, web search enhancement, audio generation, draft mode, offline inference, and continuous video chaining. Use when user wants to generate, create, edit, or extend AI videos from text prompts, images, videos, or audio."
license: MIT
metadata:
  author: openakita
  version: "2.1.0"
---

# Seedance 视频生成

通过火山方舟 Ark API 使用字节跳动 Seedance 模型生成 AI 视频。

## 前置条件

需设置 ARK_API_KEY 环境变量：
export ARK_API_KEY="your-api-key-here"

Base URL: https://ark.cn-beijing.volces.com/api/v3

## 支持模型

| 模型 | 模型 ID | 能力 |
|------|---------|------|
| Seedance 2.0 | doubao-seedance-2-0-260128 | 全能力：文/图/多模态/编辑/延长/联网/有声 |
| Seedance 2.0 Fast | doubao-seedance-2-0-fast-260128 | 同 2.0，更快更便宜 |
| Seedance 1.5 Pro | doubao-seedance-1-5-pro-251215 | 文生视频、图生视频、音频、草稿模式、离线推理 |
| Seedance 1.0 Pro | doubao-seedance-1-0-pro-250528 | 文生视频、图生视频、离线推理 |
| Seedance 1.0 Pro Fast | doubao-seedance-1-0-pro-fast-251015 | 同 1.0 Pro，更快 |
| Seedance 1.0 Lite T2V | doubao-seedance-1-0-lite-t2v-250428 | 仅文生视频 |
| Seedance 1.0 Lite I2V | doubao-seedance-1-0-lite-i2v-250428 | 图生视频、参考图 |

默认模型: doubao-seedance-2-0-260128

## Seedance 2.0 能力总览

- **文生视频**: 纯文本 prompt 生成视频
- **图生视频**: 首帧(first_frame) / 首尾帧(first_frame+last_frame)
- **多模态参考**: 组合图片(0-9张)、视频(0-3个)、音频(0-3个)
- **视频编辑**: 替换主体、增删改对象、局部重绘/修复
- **视频延长**: 向前/向后延长、多段串联
- **联网搜索**: 纯文本模式 web_search，提升时效性
- **有声视频**: generate_audio=true 生成同步音频
- **返回尾帧**: return_last_frame=true 获取视频尾帧（用于连续生成）

## Content 结构

Ark API 使用 content 数组传递多模态输入：

| type | 子字段 | role | 说明 |
|------|--------|------|------|
| text | text | — | 文本提示词 |
| image_url | image_url.url | first_frame | 首帧图片 |
| image_url | image_url.url | last_frame | 尾帧图片 |
| image_url | image_url.url | reference_image | 参考图片（2.0 多模态） |
| video_url | video_url.url | reference_video | 参考视频（2.0 编辑/延长/多模态） |
| audio_url | audio_url.url | reference_audio | 参考音频（2.0 多模态） |

**素材引用规则**: 提示词中使用"素材类型+序号"引用，如「图片1」「视频2」「音频1」。序号为同类素材在 content 数组中的排序（从1开始）。不支持用 Asset ID 指代素材。

## 提示词技巧

### 基本公式
**提示词 = 主体 + 运动，背景 + 运动，镜头 + 运动**

### 通用建议
- 用简洁准确的自然语言描述想要的效果
- 将抽象描述换成具象描述，重要内容前置
- 文生视频随机性较大，可用于激发灵感
- 图生视频请尽量上传高清高质量图片
- 如有明确预期，建议先生图再图生视频

### 2.0 多模态参考公式
- **图片参考**: 参考/提取/结合「图片n」中的「主体描述」，生成「画面描述」，保持特征一致
- **视频参考**: 参考「视频n」的「动作/运镜/特效描述」，保持一致
- **音频参考**: 音色 → 「角色」说:"「台词」"，音色参考「音频n」；内容 → 时机+「音频n」

### 2.0 视频编辑公式
- **增加元素**: 描述「元素特征」+「出现时机」+「出现位置」
- **删除元素**: 点明删除目标，强调保持不变的元素
- **修改元素**: 清晰描述更换内容

### 2.0 视频延长公式
- **单段延长**: 向前/向后延长「视频n」+「延长内容描述」
- **多段串联**: 「视频1」+「过渡描述」+接「视频2」+「过渡描述」+接「视频3」

## 参数参考

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| ratio | string | 16:9 | 16:9, 4:3, 1:1, 3:4, 9:16, 21:9, adaptive |
| duration | int | 5 | 视频时长：2.0=4-15s, 1.5=4-12s, 1.0=2-12s |
| resolution | string | 720p | 480p, 720p (2.0); 480p, 720p, 1080p (1.x) |
| generate_audio | bool | true | 生成同步音频 |
| watermark | bool | false | 添加水印 |
| seed | int | — | 随机种子（复现结果） |
| camera_fixed | bool | false | 固定摄像头（1.x） |
| draft | bool | false | 草稿模式，低成本预览（仅 1.5 Pro） |
| return_last_frame | bool | false | 返回视频尾帧（用于连续生成） |
| tools | array | — | [{"type":"web_search"}] 联网搜索（仅 2.0 纯文本） |
| service_tier | string | default | default=在线推理, flex=离线推理半价（仅 1.5 Pro / 1.0 系列） |
| execution_expires_after | int | 172800 | 离线任务超时秒数（flex 模式生效） |
| callback_url | string | — | Webhook 回调 URL，任务状态变化时通知 |

## 进阶用法

### 离线推理（半价）
设置 service_tier="flex"，价格仅为在线推理的 50%。仅 1.5 Pro 和 1.0 系列模型支持，2.0 不支持。适合时延不敏感的批量生成场景。

### 样片模式（两步走）
1. draft=true 生成低成本预览视频，验证构图/镜头/动作
2. 确认后用 draft 视频 URL 作为 reference_video 生成正式视频

### 连续视频生成
设置 return_last_frame=true，用前一个视频的尾帧作为下一个视频的首帧，循环生成多段连续视频。可用 FFmpeg 拼接成长视频。

## 使用限制

- **图片**: jpeg/png/webp/bmp/tiff/gif/heic/heif, 300-6000px, <30MB, 宽高比 0.4-2.5
- **视频**: mp4/mov, 2-15s/个, 最多3个, 总时长≤15s, <50MB/个
- **音频**: wav/mp3, 2-15s/个, 最多3个, 总时长≤15s, <15MB/个
- **不支持组合**: "文本+音频"、"纯音频"输入
- **视频 URL 24h 过期**, 需立即下载
- **2.0 不支持上传含真人人脸的参考图/视频**, 可用虚拟人像(asset://ASSET_ID)

## 预置脚本

本 skill 提供 Python CLI（纯 stdlib，零依赖）：`scripts/seedance.py`

```bash
# 文生视频
python3 scripts/seedance.py create --prompt "小猫打哈欠" --wait --download ~/Desktop

# 图生视频（首帧）
python3 scripts/seedance.py create --prompt "人物转头微笑" --image photo.jpg --wait

# 多模态参考（2.0）
python3 scripts/seedance.py create --prompt "参考图片1的风格" \
  --ref-images style.jpg --ref-videos clip.mp4 --ref-audios bgm.mp3 --wait

# 视频编辑（2.0）
python3 scripts/seedance.py create --prompt "将视频1中的猫替换为狗" \
  --ref-videos original.mp4 --wait

# 视频延长（2.0）
python3 scripts/seedance.py create --prompt "视频1结束后接视频2" \
  --ref-videos clip1.mp4 clip2.mp4 --duration 10 --wait

# 联网搜索
python3 scripts/seedance.py create --prompt "玻璃蛙微距特写" --web-search --wait

# 离线推理（半价，仅 1.5 Pro / 1.0 系列）
python3 scripts/seedance.py create --prompt "日落海滩" \
  --model doubao-seedance-1-5-pro-251215 --service-tier flex --wait

# 连续视频链式生成（自动用尾帧串接）
python3 scripts/seedance.py chain \
  "女孩抱着狐狸，温柔地看向镜头" \
  "女孩和狐狸在草地上奔跑" \
  "女孩和狐狸坐在树下休息" \
  --image first_frame.jpg --download ~/Desktop

# 查询/列表/删除
python3 scripts/seedance.py status <TASK_ID>
python3 scripts/seedance.py list
python3 scripts/seedance.py delete <TASK_ID>
```

## Quality Gates (G1–G3)

| Gate | 检查内容                                            | 通过条件                                                  |
| ---- | --------------------------------------------------- | --------------------------------------------------------- |
| G1   | `ARK_API_KEY` 环境变量已设                          | `os.environ.get("ARK_API_KEY")` 非空                     |
| G2   | 输入素材符合"使用限制"清单（图/视频/音频规格）     | 调用前 `validate_assets()` 校验通过                       |
| G3   | 任务 polling 终态可识别（成功 / 失败 / 内容审核）  | `status in {"succeeded","failed","content_filter"}`       |

## Trust Hooks（你怎么知道我没乱花钱 / 偷数据）

| 信任点              | 怎么自查                                                         |
| ------------------- | ---------------------------------------------------------------- |
| 钱花在哪 / Cost     | 仅调用 `https://ark.cn-beijing.volces.com/api/v3` — 火山方舟    |
| 数据流向 / Data     | 上传素材以 URL 形式提交（你提供，CLI 不主动上传到第三方）        |
| 出错怎么办 / Errors | CLI 退出码非 0；JSON 模式输出 `{ok: false, error: ...}`          |
| 远程依赖 / Network  | 仅火山方舟；可用 `--dry-run` 离线打印 payload 不发请求           |

## storyboard 一键投喂 / Storyboard Bridge (Sprint 1)

`storyboard` 插件新增 `GET /api/plugins/storyboard/tasks/{task_id}/export-seedance.json`，
返回该路由会输出一份 `{shots: [{prompt, duration_sec, model, ratio, resolution}, ...],
cli_examples: [...]}` JSON。可直接 `jq` 抽取后批量喂给本 CLI：

```bash
curl -s localhost:8000/api/plugins/storyboard/tasks/<sb_task>/export-seedance.json \
  | jq -r '.cli_examples[]' \
  | sh
```

