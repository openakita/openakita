# Subtitle Craft · 字幕工坊

OpenAkita 的 AI 字幕全生命周期插件 — 一个插件涵盖 **自动字幕 → 翻译 → 修复 → 烧制** 的完整链路。

后端基于阿里云百炼 DashScope（Paraformer-v2 词级 ASR + Qwen-MT 多语言翻译 + Qwen-VL 角色识别），本地依赖 FFmpeg + Playwright（HTML 字幕渲染时按需启动），全部通过 4 张 SQLite 表持久化任务、转录、资产、配置。

---

## 1. 功能概览

| Mode | 中文名 | 输入 | 输出 | 主要 API |
|---|---|---|---|---|
| `auto_subtitle` | 自动字幕 | 视频/音频 | SRT + VTT（可选烧制） | Paraformer-v2 |
| `translate` | 字幕翻译 | SRT | 译制 SRT（支持双语） | Qwen-MT (flash/plus/lite) |
| `repair` | 字幕修复 | SRT | 修复后 SRT | 本地（无 API 费用） |
| `burn` | 字幕烧制 | 视频 + SRT | 烧入字幕的视频 | 本地 FFmpeg / Playwright |

可选增量功能：

- **角色识别**（Qwen-VL）：`auto_subtitle` 模式下,在开启 `diarization_enabled` 后可切换 `character_identify_enabled` 开关,把 `SPEAKER_00/01/02` 自动映射为角色名。识别失败保留原标签,**不阻塞流程**（P1-12）。
- **双语字幕**：翻译模式打开 `bilingual` 后,原文与译文同时呈现。
- **缓存复用**：同一文件第二次跑 `auto_subtitle` 走本地缓存,不再调用 Paraformer。

---

## 2. 安装与依赖

### 2.1 必需

- OpenAkita 主程序（SDK ≥ 0.7.0,< 0.8.0）
- Python 3.11+,以及 `aiosqlite`、`httpx`、`fastapi`、`pydantic` (主程序自带)
- `ffmpeg`（任意 4.x+）：放进系统 PATH 或在「设置 → 运行时」填入绝对路径
- DashScope API Key（用于 ASR 与翻译）

### 2.2 可选

- `playwright` Python 包 + Chromium 浏览器：仅在 **HTML 烧制引擎** 模式下需要;烧制 HTML 失败时会自动降级到 ASS。
  ```bash
  pip install playwright
  python -m playwright install chromium
  ```
- 中文/日韩字体：HTML 烧制依赖系统已安装目标语言字体。Linux 服务器请预装 Noto CJK 系列。

### 2.3 安装 / 启用

把整个 `plugins/subtitle-craft/` 目录放到 OpenAkita 的 `plugins/` 下,在主界面启用即可。第一次启动会自动创建 SQLite 数据库 `data/plugins/subtitle-craft/subtitle_craft.db`。

---

## 3. 快速开始（GUI）

1. 打开「字幕工坊」标签页 → **设置** → 填入 DashScope API Key → 保存。
2. 切到 **创建任务** → 选择「自动字幕」模式 → 拖入一段 30 秒以内的 mp4。
3. 右侧「成本预估」实时显示 ¥ 估算金额(¥0.00027/秒)。
4. 点 **开始任务** → 弹窗提示已创建 → **前往任务列表**。
5. 任务列表实时进度（SSE `task_update` 事件)显示 7 步流水线;完成后右侧详情面板出现 **下载 SRT** / **下载视频** 按钮。

---

## 4. 路由与工具

### 4.1 21 条 REST 路由（全部前缀 `/api/plugins/subtitle-craft/`）

```
POST   /tasks                            - 创建任务
GET    /tasks                            - 列出任务（支持 status/mode 过滤）
GET    /tasks/{task_id}                  - 单个任务详情
DELETE /tasks/{task_id}                  - 删除任务及产物
POST   /tasks/{task_id}/cancel           - 取消运行中任务（合作式）
POST   /tasks/{task_id}/retry            - 重试失败/已取消的任务
GET    /tasks/{task_id}/download         - 下载 SRT
GET    /tasks/{task_id}/download_video   - 下载烧制后的视频
GET    /tasks/{task_id}/preview_srt      - SRT/VTT 内容预览
POST   /upload                           - 上传源文件（视频/音频/SRT）
GET    /library/transcripts              - 历史转录缓存
GET    /library/srts                     - 历史字幕成品
GET    /library/styles                   - 内置 + 自定义样式
POST   /library/styles                   - 新增自定义样式
DELETE /library/styles/{style_id}        - 删除自定义样式
POST   /cost-preview                     - 独立成本预估
GET    /settings                         - 当前配置（API key 仅返回掩码）
PUT    /settings                         - 更新配置
GET    /storage/stats                    - 存储统计
GET    /modes                            - 模式 / 翻译模型 / 错误码字典
GET    /healthz                          - 健康检查（4 字段)
```

`/healthz` 返回固定 4 字段,**永不回显 API key 本体**:
```json
{
  "ffmpeg_ok": true,
  "playwright_ok": true,
  "playwright_browser_ready": false,
  "dashscope_api_key_present": true
}
```

### 4.2 4 个 Tool（OpenAkita 工具调用）

| Tool | 用途 |
|---|---|
| `subtitle_craft_create` | 用 LLM 触发新任务 |
| `subtitle_craft_status` | 查询任务状态 |
| `subtitle_craft_list` | 列出最近任务 |
| `subtitle_craft_cancel` | 请求取消任务 |

> v1.0 **不含** 跨插件协同 Handoff（路由 / Tool / UI 全部为 0）。Schema 已预埋（`assets_bus` 表、`tasks.origin_*` 字段),v2.0 补 UI 即可,无数据迁移。

---

## 5. 错误码（9 键标准）

与 `clip-sense` 字面量 1:1 对齐,UI ErrorPanel 跨插件复用：

| `error_kind` | 触发场景 | 中文提示 |
|---|---|---|
| `network` | 连接超时 / DNS 失败 | 请检查网络后重试 |
| `timeout` | Paraformer 任务 >900s | 操作超时,可在「设置」调高阈值 |
| `auth` | 401/403/429 部分（vendor 4xx） | API Key 错误或被拒,请检查百炼控制台 |
| `quota` | 余额不足 / 部分 429 | 配额/余额不足,请前往百炼控制台充值 |
| `moderation` | 审核未过 | 内容审核未通过,请调整文本/视频 |
| `dependency` | ffmpeg / Playwright 缺失 | 运行依赖缺失;HTML 烧制已自动降级到 ASS |
| `format` | 音视频/SRT 格式错 | 文件格式错误;SRT 编码请用 UTF-8 |
| `duration` | 音频 >12 小时 / 文件 >2GB | 媒体时长/体积超限,请先手动截取 |
| `unknown` | 其他 | 未知错误,详见日志 |

---

## 6. 配置项一览

通过 `/settings` 或「设置」标签页管理,持久化到 `config` 表：

| Key | 默认 | 说明 |
|---|---|---|
| `dashscope_api_key` | `""` | 必填;`/healthz.dashscope_api_key_present` 反映其是否非空 |
| `ffmpeg_path` | `""` | 留空则用系统 PATH 中的 `ffmpeg` |
| `paraformer_timeout` | `900` | Paraformer 单任务轮询上限（秒） |
| `translation_parallel` | `2` | Qwen-MT 翻译分片并发数 |
| `custom_styles_json` | `[]` | 自定义样式预设 JSON 数组 |

---

## 7. 烟测（手动 5 步,Phase 6 收尾必跑）

1. **API Key 配置**：「设置」填入 sk-... → 保存 → `/healthz.dashscope_api_key_present=true`
2. **自动字幕**：上传 30 秒中文视频 → `mode=auto_subtitle` → 任务进入 `running` → 7 步流水线滚动 → `succeeded` → 下载 SRT 内容正确,UTF-8。
3. **翻译**：上传一段已有 SRT → `mode=translate` + `target_lang=en` → 完成后 SRT 内容为英文,行序与原文对齐。
4. **修复**：故意构造时间重叠的 SRT → `mode=repair` → 修复后无重叠,行长 ≤40。
5. **烧制**：原视频 + 任意 SRT → `mode=burn` + `burn_engine=ass` → 输出视频包含可见硬字幕。

任意一步失败查 `/healthz` + `data/plugins/subtitle-craft/logs/subtitle-craft.log`。

---

## 8. 跨插件协同（v2.0 路线图）

v1.0 仅做 **schema 预埋**:`assets_bus` 表 + `tasks.origin_plugin_id` / `origin_task_id` 字段已建,但 v1.0 全程为空、不读不写。v2.0 将补：

- 路由 `POST /handoff/{from_plugin_id}/{from_task_id}` 接收来源任务
- UI 「送往 …」按钮（任务详情页底部行）
- 共享资产去重（`assets_bus.asset_id` 跨插件命中)

升级 v2.0 时**零数据迁移**,只追加路由 + UI。

---

## 9. 进一步阅读

- **设计与决策**：[`docs/subtitle-craft-plan.md`](../../docs/subtitle-craft-plan.md)（v2 + P-1~P-8 补丁定稿）
- **技术验证记录**：[`VALIDATION.md`](VALIDATION.md)（5 项前置验证 + P0-5/P0-15/P0-16 裁决）
- **Skill 触发清单**：[`SKILL.md`](SKILL.md)
- **变更历史**：[`CHANGELOG.md`](CHANGELOG.md)
- **用户验收测试用例**：[`USER_TEST_CASES.md`](USER_TEST_CASES.md)
