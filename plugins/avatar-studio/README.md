# Avatar Studio (`avatar-studio`)

> 多后端数字人工作室（DashScope / RunningHub / 本地 ComfyUI）。一线插件、零 SDK contrib 依赖、零 host UI 资源挂载。
> 与 [`tongyi-image`](../tongyi-image/) / [`seedance-video`](../seedance-video/) 并列同构。

| | |
|---|---|
| **版本** | 1.1.0 |
| **SDK 范围** | `>=0.7.0,<0.8.0` |
| **入口** | `plugin.py` (`PluginBase`) + `ui/dist/index.html` |
| **形态** | 5 生成模式 + 3 后端 + 双 TTS 引擎 + 5 个 Tab + 9 工具 |

---

## 1 · 概览

avatar-studio 支持三种后端（阿里云 DashScope / RunningHub / 本地 ComfyUI）和
双 TTS 引擎（CosyVoice / Edge-TTS），提供 5 种数字人生成模式。

| Mode | 中文名 | 输入 | DashScope 链路 | 典型时长 |
|---|---|---|---|---|
| `photo_speak` | 照片说话 | 1 张人像 + 文本/音频 | `wan2.2-s2v-detect` → `wan2.2-s2v` | 60–180 s |
| `video_relip` | 视频换嘴 | 1 段视频 + 文本/音频 | `videoretalk` | 60–180 s |
| `video_reface` | 视频换人 | 1 张人像 + 1 段视频 | `wan2.2-animate-mix` (`wan-std` / `wan-pro`) | 120–360 s |
| `avatar_compose` | 数字人合成 | 1–3 张参考图 + 融合 prompt + 音频 | `wan2.7-image` → `wan2.2-s2v-detect` → `wan2.2-s2v` | 180–360 s |
| `pose_drive` | 图生动作 | 1 张人像 + 1 段动作视频 | `wan2.2-animate-move` (`wan-std` / `wan-pro`) | 120–300 s |

### Backends

| Backend | 配置方式 | 计费 |
|---|---|---|
| **阿里云 DashScope** | API Key + OSS bucket | 按 DashScope 模型定价 |
| **RunningHub** | API Key + per-mode workflow_id 预设 | 按 RH 实际用量 |
| **本地 ComfyUI** | URL + per-mode workflow 预设 | 本地推理免费 |

### TTS Engines

| Engine | 计费 | 音色数 |
|---|---|---|
| **CosyVoice** (cosyvoice-v2) | 0.20 CNY / 万字 | 12 系统 + 自定义克隆 |
| **Edge-TTS** (Microsoft) | 免费 | 12 中文音色 |

> **没有「场景预设」、「滤镜」、「美颜」**：avatar-studio 是**调度层**，所有视觉
> 风格交回模型/workflow 本身决定。

---

## 2 · 4 个 Tab 一览

| Tab | 内容 | 关键交互 |
|---|---|---|
| **Create** | 顶部 5-mode 切换 + 后端选择 + 左输入 / 右预览的分栏 | 表单按 mode 和 backend 动态渲染；非 DashScope 后端显示 workflow 选择器 |
| **Tasks** | 自写表格 + 详情抽屉 | 详情抽屉内含 `<CostBreakdown>`（done 任务才显示）和 `<ErrorPanel>`（failed 才显示） |
| **Voices** | 12 个 cosyvoice-v2 系统音色 + 自定义克隆音色 | 试听、命名、克隆 |
| **Figures** | 自定义人像形象库 | 上传图、自动 `wan2.2-s2v-detect` 预检、命名保存；`photo_speak` / `video_reface` / `avatar_compose` 可直接复用 |
| **Settings** | 三平级后端配置 (阿里云/RH/ComfyUI) + TTS 引擎 + 默认偏好 / 存储 / 高级 / 关于 | API Key 缺失只 warn 不抛；每个后端有独立测试连接按钮 |

> Voices 和 Figures 各占一个 Tab，是有意为之。**音色 ≠ 形象**，混在同一个
> 「voice library」里既不便检索也不便复用 —— 早期 v4 计划吃过这个亏。

---

## 3 · 安装

avatar-studio 跟随 OpenAkita 主仓发布，不需要额外 `pip install`。开发态：

```bash
# 在仓库根目录
cd plugins/avatar-studio
py -3.11 -m pytest tests -q          # 应输出 "85 passed, 1 skipped"
py -3.11 -m ruff check .             # 0 error
```

如果你只想拷出去单独跑（罕见情况），把整个 `plugins/avatar-studio/` 复制到
目标 OpenAkita 实例的 `plugins/` 目录即可。所有依赖（vendored helpers、UI
Kit）都在插件内部，不依赖 `openakita_plugin_sdk.contrib`、不依赖
`/api/plugins/_sdk/*`。

---

## 4 · 配置

打开 OpenAkita → 插件 → Avatar Studio → 进入插件 UI → **Settings** Tab：

| 字段 | 默认 | 说明 |
|---|---|---|
| `api_key` | （空） | 阿里云百炼控制台获取 |
| `base_url` | `https://dashscope.aliyuncs.com` | 国际版填 `https://dashscope-intl.aliyuncs.com` |
| `timeout_sec` | 60 | 单次 HTTP 调用超时（与任务总超时 600 s 不是一回事） |
| `max_retries` | 2 | 网络层自动重试次数（`network` / `timeout` 类错误） |
| `cost_threshold_cny` | 5.00 | 任务预估 > 此值时强制弹窗确认 |
| `default_resolution` | `480P` | 新建任务的默认分辨率 |
| `default_voice` | `longxiaochun_v2` | 新建任务的默认音色 |
| `auto_archive` / `retention_days` | `false` / 30 | 过期任务清理（手动触发：Settings → 存储 → 一键清理） |

**API Key 热加载**：保存 API Key 后无需重启插件，下一次请求即生效（落实
Pixelle A10）。这一点也是 avatar-studio 区别于早期 OpenAkita 插件最重要的
工程改进 —— 用户不必为了改一个字符配置而重启整个 OpenAkita 主进程。

---

## 5 · 使用

```text
1. Create → 选 mode（如「照片说话」）
2. 拖入一张人像（或从 Figures 选）→ 输入文本 → 选音色
3. 点「估算费用」→ 弹窗显示 ¥0.50 之类的明细
4. 点「确认提交」→ 跳到 Tasks Tab，看进度条 / SSE 实时更新
5. done 后下载 mp4，或在抽屉里直接预览
```

> **不要**为了"看效果"而提交 720P + 15s + wan-pro 的视频换人 —— 那一次
> 就是 ¥18 的真金白银。**先在 480P + 3-5s 跑通**，再放量。

### 工具调用

avatar-studio 注册了 9 个 tool（见 `plugin.json::provides.tools`）。在
OpenAkita 主对话里：

```
@avatar_cost_preview mode=photo_speak audio_duration_sec=3 resolution=480P
@avatar_photo_speak image_url=... text="你好" voice_id=longxiaochun_v2
@avatar_video_relip video_url=... audio_url=...
@avatar_video_reface image_url=... video_url=... mode_pro=false
@avatar_compose ref_images_url=[...] prompt="..." text="..."
```

每个 mode 工具返回 `任务已创建：{id}（mode=...）`；后续状态在 Tasks Tab 查看。

---

## 6 · 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 顶部 API Key 灯红色 | 没在 Settings 填 Key | 填完 → 保存即生效（不必重启） |
| 提交后立刻报 `auth` | Key 与 base_url 区域不匹配 | 国内 Key 走 `dashscope.aliyuncs.com`；国际版走 `-intl` |
| 卡在 `pending` 不动 | DashScope 同时处理中超 1 | `Semaphore(1)` 串行化 submit；等当前任务完成 |
| `dependency: humanoid=false` | 上传图不是真人正脸 | 换一张正面、清晰、单人的照片 |
| `moderation` | 内容审核未通过 | 换素材；常见敏感：人脸不清晰 / 水印 / 暴力 / 政治 |
| `quota` | 余额不足 | 阿里云百炼控制台充值 |
| 任务一直 `pending`，10 分钟后转 `failed` | 命中 600 s 总超时 | 重试；如多次复现，反馈 task_id |

错误码完整清单：`avatar_models.py::ERROR_HINTS`（9 类 × 中英 hints）。

---

## 7 · 5 分钟用户烟测脚本

> **必须亲自跑过一遍**才能合并。不到 5 分钟、不到 ¥0.50。

```bash
# 0. 确保 OpenAkita 已启动并加载了 avatar-studio
#    （顶部 API Key 灯应该是绿色 — 如果是红的，先去 Settings 填 Key）

# 1. 准备一张人像 — 任意正面清晰单人照都行
#    示例：https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20240829/lyumdf/female_2.png

# 2. Create Tab → 默认 photo_speak
#    - 「人像」区贴上图 URL（或拖入文件）
#    - 「文本+声音」输入"你好，欢迎来到数字人工作室。"
#    - 音色选默认 longxiaochun_v2
#    - 分辨率 480P，时长保持自动（由音频决定）

# 3. 点「估算费用」
#    - 应弹出明细：face_detect ¥0.004 + s2v 480P×3s ¥0.30 + tts 18 字符 ≈ ¥0.0036
#    - 总价 ≈ ¥0.31，远低于阈值 ¥5
#    - 点「直接提交」（无需 cost_approved）

# 4. 跳转 Tasks Tab
#    - 顶部新行 status=pending → submitted → polling → succeeded
#    - SSE 推进度，无需手动刷新

# 5. 点新行展开抽屉
#    - 视频播放器自动播 mp4
#    - 「成本明细」展开看到与 step 3 一致的项目
#    - 「任务元数据」展开看到 dashscope_id / endpoint / asset_paths
#    - 点「下载」→ mp4 落到下载目录

# ✅ 5 步全过 = 烟测通过；任意一步异常 → 抓 task_id + 截图 metadata 反馈
```

如果你能用脚本/CI 跑通这条流程，建议直接走 `tests/integration/test_dashscope_smoke.py`：

```bash
$env:DASHSCOPE_API_KEY = "sk-..."
py -3.11 -m pytest tests/integration -m integration -v
```

---

## 8 · 目录结构

```
plugins/avatar-studio/
├── plugin.json                       # 元数据 + 9 tools + ui.entry
├── plugin.py                         # 入口 + 16 routes
├── avatar_models.py                  # MODES / VOICES / PRICE_TABLE / estimate_cost / ERROR_HINTS
├── avatar_task_manager.py            # 纯 aiosqlite + 严格白名单 update_task_safe
├── avatar_dashscope_client.py        # 8 业务方法 + 9 错误分类 + Semaphore(1) + 热加载
├── avatar_pipeline.py                # AvatarPipelineContext + run_pipeline 8 step
├── avatar_studio_inline/             # vendored helpers（自带、不 import contrib）
│   ├── vendor_client.py              # BaseVendorClient + 9 ERROR_KIND_*
│   ├── upload_preview.py             # /uploads/... 路由助手
│   ├── storage_stats.py
│   ├── llm_json_parser.py            # 5 层 JSON 兜底（A6）
│   └── parallel_executor.py          # 受控并发
├── tests/
│   ├── conftest.py                   # sys.path 注入 + integration marker
│   ├── test_models.py                # 16 cost case + ERROR_HINTS 全覆盖
│   ├── test_task_manager.py          # CRUD + 白名单守护 + cleanup_expired
│   ├── test_dashscope_client.py      # mock httpx + 错误分类 + cancel + Semaphore
│   ├── test_pipeline.py              # 4 mode happy + 失败注入 + cancel + P1 验收
│   ├── test_plugin.py                # on_load / 路由 / 工具 / on_unload
│   ├── test_smoke.py                 # vendored helpers import + _assets 存在
│   └── integration/
│       └── test_dashscope_smoke.py   # @pytest.mark.integration（默认 skip）
└── ui/dist/
    ├── index.html                    # ≈ 2360 行 React + Babel CDN 单文件
    └── _assets/                      # 自带 5 件套（无 host mount）
        ├── bootstrap.js
        ├── styles.css
        ├── icons.js
        ├── i18n.js
        └── markdown-mini.js
```

---

## 9 · 已知限制

- **DashScope 异步任务并发上限 = 1 / API Key**。这是平台级硬限，不是
  插件 bug。Settings 里同一个 Key 同一时刻只能跑一个生成任务。
- **task_id 24 h 后过期**。超时后 `query_task` 会返 `not_found`，UI 端
  显示对应 hint。历史任务在本地 sqlite 里仍可查看 metadata。
- **wan2.2-animate-mix `wan-pro` 单价 ¥1.20/s**。一段 15 秒视频换人就是
  ¥18，请务必依赖 cost-preview 弹窗确认（默认阈值 ¥5）。
- **不依赖 ffmpeg**。和 seedance-video 不同，avatar-studio 不需要本地
  ffmpeg 二进制（落实 Pixelle C4 教训：模块顶层强检查导致整个插件无法
  加载是反模式）。所有视频拼接由 DashScope 一站式完成。
- **i18n 仅 zh / en**。新增语言只需在 `index.html::I18N_DICT` 增加对应
  key 即可，无需改 `_assets/i18n.js` 引擎。
- **v1.0 不接火山引擎**。视频换嘴 / 换人在火山 Seedance 2.0 上也有，但
  归 [`plugins/seedance-video/`](../seedance-video/) 处理。avatar-studio
  专注 DashScope 万相 + s2v 系列。

---

## 10 · 与 `plugins-archive/avatar-speaker/` 的关系

avatar-studio 是**全新一线插件**。它**不**继承
`plugins-archive/avatar-speaker/` 的代码，也**不**导入
`from _shared import ...`（那是 archive 的兼容桩，仅旧插件能用）。

旧的 avatar-speaker 已迁移到 `plugins-archive/` 不再维护，但目录保留以保证
旧用户的项目不会突然报错。新功能、新 fix 一律走 avatar-studio。
