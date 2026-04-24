# avatar-studio 双后端扩展计划（B 档）

> 在 avatar-studio 现有 DashScope 单后端基础上，叠加 ComfyKit 通道（RunningHub 云 + 本地 ComfyUI 二选一）  
> 与 edge-tts 引擎，实现「百炼 / RunningHub / 本地 ComfyUI」三后端可切换。  
> 同时修正 PRICE_TABLE 5 倍价格偏差并加上「北京区 Key」前置校验。

---

## 0. 关键背景（本轮调研后修正）

### 0.1 wan2.2-s2v 的真相

- **不需要"开通"**：百炼数字人模型属于"实名后默认可用"，模型广场不显示"立即开通"按钮是设计如此。
- **北京区独占**：[wan-s2v-api 官方文档](https://help.aliyun.com/zh/model-studio/wan-s2v-api) 明确说"仅适用于中国内地（北京）地域"。
- **403 根因已定位并修复**：`auth_headers()` 给所有请求（包括同步接口 `face_detect`）都附带了 `X-DashScope-Async: enable`。`wan2.2-s2v-detect` 是同步接口，不接受异步头 → 百炼返回 `403 AccessDenied: current user api does not support asynchronous calls`。修复：把 `X-DashScope-Async` 从 `auth_headers()` 移出，仅在 `_submit_async()` 通过 `extra_headers` 传入。
- **价格官方现行**（已修正）：480P ¥0.5/秒、720P ¥0.9/秒。原写 0.10 / 0.20 偏低 5 倍。
- **限流**：5 RPS、并发 1、视频 ≤ 100 秒、轮询建议 15 秒。

### 0.2 已锁定的 4 个设计决策

| 决策 | 选择 |
|---|---|
| 路线 | B 档：保留 DashScope + 加 ComfyKit（RH/本地二选一）+ edge-tts |
| TTS | 双引擎：cosyvoice-v2（百炼）+ edge-tts（微软免费），设置可选 |
| workflow_id 来源 | 先发空 placeholder，UI 引导用户去 RH 搜 |
| UI 后端粒度 | 设置全局选后端 + 创建页内换具体模型 / workflow |

### 0.3 参考项目

- **Pixelle-Video**（`D:\OpenAkita_AI_Video\refs\Pixelle-Video`）：通过 `comfykit` 调 ComfyUI workflow，双后端（RunningHub 云 / 本地 ComfyUI）自动适配。数字人步骤封装为 3 个 workflow JSON，模型选择外移到 workflow 文件。
- **调研文档**：`D:\OpenAkita_AI_Video\findings\comfyui_runninghub_intro.md`（509 行 ComfyUI/RunningHub/comfykit 零基础扫盲）

---

## 1. 目标架构

```
┌─────────────────────────────────────────────────────────────────────┐
│ Settings UI                                                         │
│   backend radio: 「百炼 / RunningHub / 本地 ComfyUI」                │
│   tts radio:     「cosyvoice / edge-tts」                           │
└──────────┬──────────────────────────────────────────────────────────┘
           │
    ┌──────▼──────┐
    │  Dispatch   │  ← plugin.py 根据 backend + mode 选 client
    └──┬─────┬────┘
       │     │
  ┌────▼──┐ ┌▼───────────────────┐
  │ DS    │ │ ComfyKit client    │
  │ client│ │ (avatar_comfy_     │
  │       │ │  client.py)        │
  └───┬───┘ └───┬───────────┬────┘
      │         │           │
      ▼         ▼           ▼
  百炼北京区  RunningHub   本地 ComfyUI
  (DashScope) (OpenAPI)    (http://127.0.0.1:8188)

  TTS Dispatch:
    cosyvoice → 百炼 cosyvoice-v2 SDK
    edge_tts  → edge-tts 本地免费
```

---

## 2. 已完成（本轮）

### 2.1 修复 403 根因（P0）

| 文件 | 改动 |
|---|---|
| `avatar_dashscope_client.py:auth_headers()` | 移除 `X-DashScope-Async: enable` |
| `avatar_dashscope_client.py:_submit_async()` | 通过 `extra_headers=self._ASYNC_HEADER` 仅在异步提交时附带 |
| `plugin.py:_run_figure_detect()` | AccessDenied hint 文案改为更精准的 3 条诊断（北京区 Key / RAM 策略 / 业务空间） |

### 2.2 修正价格表（P0）

| 文件 | 改动 |
|---|---|
| `avatar_models.py:PRICE_TABLE` | `wan2.2-s2v` 480P: 0.10→**0.50**, 720P: 0.20→**0.90** |
| `avatar_models.py:MODES` | `cost_strategy` 文案同步更新 |
| `tests/test_models.py` | 价格快照断言 + cost estimate 断言全部同步 |
| `tests/test_dashscope_client.py` | `auth_headers` 测试改为验证 async header 不在基础 headers 中 |

---

## 3. 待实施（7 个阶段）

### 阶段 1 · 依赖（30 分钟）

`plugins/avatar-studio/requirements.txt` 追加：
```
comfykit>=0.1.12
edge-tts>=6.1
```

### 阶段 2 · 模型注册表 + workflow 占位（1 小时）

**新建** `plugins/avatar-studio/avatar_model_registry.py`：
- 4 模式 × N 候选模型注册表
- 每条：`{backend, model_id, label_zh, label_en, cost_note, is_default, requires_oss}`
- DashScope 候选：wan2.2-s2v / emo-v1 / liveportrait（photo_speak）; videoretalk（video_relip）; wan2.2-animate-mix / animate-anyone-gen2（video_reface）; wan2.5-i2i-preview + s2v 链（avatar_compose）
- RunningHub / comfyui_local 候选：空 workflow_id placeholder

**新建** `plugins/avatar-studio/workflows/` 目录：
```
workflows/
  photo_speak.runninghub.json      # {"source":"runninghub","workflow_id":""}
  video_relip.runninghub.json
  video_reface.runninghub.json
  avatar_compose.runninghub.json
  README.md                        # 教用户如何在 RunningHub 拿 workflow_id
```

**新增** `tests/test_model_registry.py`

### 阶段 3 · ComfyKit 客户端 + edge-tts 引擎（半天）

**新建** `plugins/avatar-studio/avatar_comfy_client.py`：
- 包 `comfykit.ComfyKit`，lazy 构造
- API 形态与 `avatar_dashscope_client.py` 对齐：`submit_workflow(mode, workflow_ref, params, backend)` → task_id; `query_task(task_id)` → {status, video_url, ...}; `cancel_task(task_id)`; `probe_models()`
- RunningHub 与本地 ComfyUI 的区别仅在 ComfyKit 构造参数（`runninghub_api_key` vs `comfyui_url`）

**新建** `plugins/avatar-studio/avatar_tts_edge.py`：
- 12 个微软中文音色（云希、云扬、云健、云夏、晓伊、晓辰、晓秋、晓睿、晓涵、晓墨、晓萱、晓双）
- `synth_voice(text, voice, output_path)` → `{bytes, duration_sec}` 与 cosyvoice 同形

**新增** `tests/test_comfy_client.py`（mock comfykit）、`tests/test_tts_edge.py`（mock edge_tts）

### 阶段 4 · plugin.py 后端调度（半天）

`SettingsBody` 新增字段：
- `backend`: `"dashscope" | "runninghub" | "comfyui_local"`，默认 `"dashscope"`
- `runninghub_api_key`: str
- `comfyui_url`: str
- `comfyui_api_key`: str
- `runninghub_instance_type`: `"standard" | "plus"`
- `tts_engine`: `"cosyvoice" | "edge"`，默认 `"cosyvoice"`
- `tts_voice_edge`: str，默认 `"zh-CN-YunxiNeural"`
- `per_mode_models`: dict[ModeId, str]

`_enriched_settings()` 新增：
- `backend_status_message`: 配置校验
- `region_warning`: dashscope 后端 + 非北京 base_url → 警告

`on_load`: 同时构造 DashScope client + Comfy client（lazy）

`_run_one_pipeline`: 根据当前模式的 ModelOption.backend dispatch

### 阶段 5 · pipeline 改造（半天）

`avatar_pipeline.py`:
- `run_pipeline` 新增 `backend_resolver` 参数
- 第 5/6/7 步（detect / submit_video / poll）按 backend 分流：DashScope → 走原 client + OSS; RH/local → 走 comfy_client，跳过 OSS
- 第 4 步（TTS）按 `tts_engine` 分流到 cosyvoice 或 edge_tts

### 阶段 6 · UI 改造（1 天）

`ui/dist/index.html`:
- SettingsTab 新增「后端通道」section（三选一 radio + 各后端独立配置块）
- TTS section 新增「TTS 引擎」radio + edge-tts 音色下拉
- CreateTab 模式选择下方新增「使用模型」下拉（根据后端过滤候选）
- RH/local 后端多一个 workflow_id 输入框
- ~30 个 i18n key（zh/en）

### 阶段 7 · 测试 + 文档 + 提交（半天）

- `pytest` 全量 + `ruff check`
- 新增 `tests/integration/test_dual_backend_smoke.py`
- 更新 `README.md` / `SKILL.md` / `USER_TEST_CASES.md` / `CHANGELOG.md`
- 拆 4~5 个英文 commit

---

## 4. 百炼所有数字人相关模型汇总（截至 2026-04）

| 功能 | 模型 ID | 状态 | 价格 | 备注 |
|---|---|---|---|---|
| 数字人对口型（推荐） | `wan2.2-s2v` + `wan2.2-s2v-detect` | 在线，北京区限定 | detect 0.004元/张; 480P 0.5元/秒; 720P 0.9元/秒 | 需先 detect 再 submit |
| 视频换人 | `wan2.2-animate-mix` | 在线 | wan-std 0.60元/秒; wan-pro 1.20元/秒 | |
| 图生动作 | `wan2.2-animate-move` | 在线 | 同 animate-mix | |
| 视频换嘴 | `videoretalk` | 在线 | 0.30元/秒 | |
| 悦动人像（旧） | `emo-v1` + `emo-detect-v1` | 在线，官方建议用 s2v 替代 | 1:1 0.08元/秒; 3:4 0.16元/秒 | 便宜 |
| 灵动人像（旧） | `liveportrait` + `liveportrait-detect` | 在线，官方建议用 s2v 替代 | 更便宜 | >20s 长视频场景 |
| 舞动人像（旧） | `animate-anyone-gen2` | 在线 | 更便宜 | 纯跳舞 |
| 图生图（合形象） | `wan2.5-i2i-preview` | 在线 | 0.20元/张 | avatar_compose 用 |
| TTS | `cosyvoice-v2` | 在线 | 0.20元/万字 | WebSocket SDK |
| 视觉 LLM | `qwen-vl-max` | 在线 | 0.02/0.06 元/千token | 可选 prompt 辅助 |

注：`wan2.7` 系列（t2v / i2v / Image）目前仅覆盖通用视频生成和图像编辑，**没有 s2v / animate-mix 的 2.7 变种**。

---

## 5. Pixelle-Video 技术路线对比

| 维度 | Pixelle-Video | avatar-studio（当前 + 计划） |
|---|---|---|
| 后端 | ComfyUI（RunningHub 或自建） | DashScope 直连 **+ ComfyKit（新增）** |
| 模型选择 | workflow.json 决定，UI 不暴露 | 模型注册表 + UI 下拉（新增） |
| 模型升级 | 换一个 workflow_id 即可 | 改注册表 or 换 workflow_id |
| 用户成本 | 自建零成本 / RunningHub 按 GPU 秒计费 | 百炼按模型公示价 / RH 同 Pixelle |
| 部署门槛 | 需要 ComfyUI 节点 / RH 账号 | 百炼只要 1 个 API Key；RH 额外需账号 |
| TTS | edge-tts (免费) / ComfyUI tts workflow | cosyvoice-v2 (百炼) **+ edge-tts（新增）** |
| OSS 依赖 | 不需要（comfykit 直传） | 百炼后端需要；RH/本地后端不需要 |

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| RH 社区 workflow_id 随时可能下线 | ship_empty 模式让用户自己管 ID；README 引导 fork 到自己账号 |
| 用户配了 RH 但忘填 workflow_id 直接跑 | 前端 + 后端双重校验，缺则拒绝并 toast 引导 |
| edge-tts 在 Windows 偶发证书错误 | try/except，失败时 fallback 提示切回 cosyvoice |
| 双 client 同时存活内存翻倍 | 都是 lazy 构造，只在首次 dispatch 时 init |
| 用户在百炼 backend 下却没北京区 Key | settings 保存时探测 base_url，非北京区立即红条警告 |

---

## 7. 落地后用户能做到的事

1. **百炼报 403 时一键切到 RunningHub**：去 RH 官网搜 wan2.2-s2v workflow → 复制 ID → 贴进 UI → 跑
2. **没有 RH 账号但有本地 GPU**：填 ComfyUI URL → 拷贝 workflow.json → 零费用跑
3. **不想付百炼 TTS 钱**：切 edge-tts，0 费用、12 个微软中文音色
4. **混搭**：photo_speak 用百炼（效果第一）+ avatar_compose 走 RH（便宜 100 倍）

---

## 8. 不在本计划范围（明确排除）

- 不引入第三方云（火山引擎 / 即梦 / 海螺等）
- 不实现 workflow.json 在线编辑器
- 不做"自动从 RH 广场抓推荐 workflow_id"功能
- 不删除 OSS 模块（DashScope 后端依然需要）
- 不做 wan2.7-Image 升级到生产环境（先标"实验"，等官方稳定）

