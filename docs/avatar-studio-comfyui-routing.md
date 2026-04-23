# avatar-studio · ComfyUI / RunningHub 双后端接入路线

> 本文档是 avatar-studio 接入 ComfyKit / RunningHub 作为第二后端的实施路线决策。**只谈业务对接，不讲 ComfyUI 基础**——基础概念见 `D:/OpenAkita_AI_Video/findings/comfyui_runninghub_intro.md`。
>
> Status: **Plan / Pending implementation**
> Last updated: 2026-04-22

---

## 0. TL;DR

- **背景**：百炼 `wan2.2-s2v` 调不通（403 AccessDenied / 业务空间未授权 / 模型授权门槛高）+ 价格表全错（少算 5 倍）+ 模型选型完全硬编码。
- **决策**：B 档 —— **保留 DashScope 通道，新加 ComfyKit 通道（RunningHub + 本地 ComfyUI 两选一）**，全局选后端 + 创建页内换具体模型/workflow。TTS 引擎设置里支持 cosyvoice-v2 (百炼) 和 edge-tts (微软免费) 双选。
- **工作量**：约 3.5~4 天（不含 RunningHub workflow 调试）。
- **不破坏**：现有 DashScope 链路、OSS 上传、4 模式定义、UI 框架不动。

---

## 1. 背景与问题

### 1.1 已知问题清单

| # | 问题 | 严重度 | 现象 |
|---|---|---|---|
| P0 | `PRICE_TABLE` 全错 | 🔴 高 | s2v 480P 写 0.10/秒，实际 0.5/秒；720P 写 0.20，实际 0.9。Cost preview 比真实账单少 5 倍 |
| P0 | wan2.2-s2v 调用 403 | 🔴 高 | 用户主账号也调不通，模型广场又看不到"开通"按钮，UX 黑盒 |
| P1 | 模型 ID 全硬编码 | 🟡 中 | 想换模型只能改 `avatar_dashscope_client.py:111-117` 重启 |
| P1 | 单一后端 | 🟡 中 | DashScope 挂了 / 授权不通 = 整个插件不可用 |
| P2 | 价格表覆盖不全 | 🟢 低 | 没收录 emo-v1 / liveportrait / wan2.7-Image / wan2.2-animate-move |
| P2 | TTS 单一 | 🟢 低 | 只有 cosyvoice-v2，国际用户没微软系音色可选 |

### 1.2 百炼 403 五大可能原因

参考 [官方错误码](https://help.aliyun.com/zh/model-studio/error-code) + 实际项目踩坑：

| # | 原因 | 怎么自查 | 怎么修 |
|---|---|---|---|
| ① | RAM 子账号没有 `AliyunBailianFullAccess` | 控制台 → 账号信息 | RAM → 加策略 |
| ② | 主账号未实名 / 个人未升企业 | 账号信息 → 实名等级 | 完成实名 / 升企业 |
| ③ | API Key 地域 ≠ 调用地域 | API Key 详情页 | 设置里 base_url 改对 |
| ④ | **业务空间未勾选数字人模型**（最常见）| 控制台 → 业务空间 → 模型授权 | 勾选 wan2.2-s2v / animate-mix / videoretalk |
| ⑤ | 余额 / 资源包失效 | 账单 | 充值 |

### 1.3 为什么不能"只修 P0"

修了价格和 403 之后，仍然存在 "用户授权不下来 → 插件完全不能用" 的硬限制。引入第二后端是**容灾**，不是"锦上添花"。

---

## 2. 决策与依据

### 2.1 已确认的 4 个决策

| 项目 | 选择 | 备选 | 选择理由 |
|---|---|---|---|
| 后端策略 | **B 档：DashScope + ComfyKit 双存** | A (只修 P0) / C (完全切 ComfyKit) | 既给百炼一个修复机会，也给容灾兜底 |
| TTS 引擎 | **双 TTS：cosyvoice-v2 + edge-tts** | 单一 / 删除 | 国内国际都覆盖，edge-tts 免费 |
| workflow_id 来源 | **空 placeholder + 用户自填** | 我们预置 / 用户提供 | RH 公开 workflow 可能下线，预置维护成本高 |
| UI 粒度 | **后端全局选 + 模型创建页可换** | 全局 / 每模式独立 | 平衡灵活性与 UX 复杂度 |

### 2.2 为什么选 ComfyKit 而不是直连 RunningHub OpenAPI

| 维度 | 直连 RH OpenAPI | comfykit |
|---|---|---|
| 代码量 | 3 个 endpoint 自己包 | 一行 `await kit.execute(...)` |
| 双后端切换 | 我们自己写 if/else | SDK 自动识别 |
| 本地 ComfyUI 适配 | 还得再写一套 | 同一 API |
| 维护 | 自己跟 RH API 演进 | 跟着 puke3615 升级 |
| 成熟度 | — | PyPI 周下载 2.8k，Pixelle 在用 |

结论：**没必要重复造轮子**。

### 2.3 为什么不删除 DashScope 通道

1. **百炼数字人在中国用户群里仍是首选**（不卡白名单的小账号能用）
2. **DashScope 的 `cosyvoice-v2` 比 edge-tts 中文自然度好很多**
3. **OSS 上传链路已经写好**，DashScope 必须公网 URL，删了就要重写
4. **回滚需要**——RunningHub 万一抽风，用户能切回去

---

## 3. 整体架构

### 3.1 后端 dispatch 流程

```
用户点"生成"
     │
     ▼
plugin.py / _run_one_pipeline
     │
     ├──→ settings.backend == "dashscope" ──┐
     │                                       ▼
     │                         avatar_dashscope_client.py
     │                                       │
     │                            ┌──────────┴──────────┐
     │                            ▼                     ▼
     │                       OSS 上传             DashScope HTTP
     │                            │                     │
     │                            └──────┬──────────────┘
     │                                   ▼
     │                              视频 URL
     │
     ├──→ settings.backend == "runninghub" ──┐
     │                                        ▼
     │                          avatar_comfy_client.py
     │                                        │
     │                                        ▼
     │                            comfykit.ComfyKit(rh_key)
     │                                        │
     │                                        ▼
     │                              RunningHub OpenAPI
     │                                        │
     │                                        ▼
     │                                   视频 URL
     │
     └──→ settings.backend == "comfyui_local" ──┐
                                                 ▼
                                  avatar_comfy_client.py
                                                 │
                                                 ▼
                                  comfykit.ComfyKit(comfyui_url)
                                                 │
                                                 ▼
                                       本地 ComfyUI HTTP
                                                 │
                                                 ▼
                                            视频 URL
```

### 3.2 TTS dispatch

```
pipeline 第 4 步 (TTS)
     │
     ├──→ settings.tts_engine == "cosyvoice" ──→ avatar_dashscope_client.synth_voice()
     │                                                │
     │                                                ▼
     │                              百炼 cosyvoice-v2 (WebSocket)
     │
     └──→ settings.tts_engine == "edge"     ──→ avatar_tts_edge.synth_voice()
                                                      │
                                                      ▼
                                  微软 edge-tts (HTTP, 免费, 走 Azure 公开端点)
```

---

## 4. 文件改动清单

### 4.1 新增文件

| 文件 | 行数估算 | 内容 |
|---|---|---|
| `avatar_model_registry.py` | ~150 | 4 模式 × N 候选模型注册表（含后端类型、价格、授权要求） |
| `avatar_comfy_client.py` | ~300 | comfykit 包装，与 dashscope_client 同形 |
| `avatar_tts_edge.py` | ~120 | edge-tts 包装，与 cosyvoice 同形 |
| `workflows/photo_speak.json` | ~10 | RH workflow_id 占位 + 注释 |
| `workflows/video_relip.json` | ~10 | 同上 |
| `workflows/video_reface.json` | ~10 | 同上 |
| `workflows/avatar_compose.json` | ~10 | 同上 |
| `tests/test_model_registry.py` | ~80 | 注册表回归 |
| `tests/test_comfy_client.py` | ~150 | mock comfykit 的提交/查询/失败路径 |
| `tests/test_tts_edge.py` | ~80 | mock edge-tts 的合成/异常 |

### 4.2 修改文件

| 文件 | 改动点 |
|---|---|
| `requirements.txt` | + `comfykit>=0.1.12`、+ `edge-tts>=6.1.0` |
| `avatar_models.py` | 修正 PRICE_TABLE（s2v 480P→0.5、720P→0.9）；新增 emo-v1、liveportrait、wan2.7-Image、wan2.2-animate-move、animate-anyone-gen2、runninghub_rhcoin 价目；调整 estimate_cost 按当前 backend 算 |
| `plugin.py` | 新增 settings 字段 `backend`、`runninghub_api_key`、`comfyui_url`、`comfyui_api_key`、`tts_engine`、`tts_voice_edge`、`mode_model_overrides`；按 backend dispatch；保留 OSS（DashScope 用） |
| `avatar_pipeline.py` | 第 5/6/7 步按 backend 分流到对应 client；第 4 步按 tts_engine 分流；output_relocate 不变 |
| `ui/dist/index.html` | SettingsTab 加「后端通道」radio + 各后端独立配置块 + TTS 引擎下拉；CreateTab 加"模型/workflow 选择"下拉；i18n 中英 |
| `README.md` | 三后端配置教程 + RH workflow 怎么获取 |
| `SKILL.md` | 接入指南更新 |
| `USER_TEST_CASES.md` | 新增双后端 / 双 TTS 测试用例 |
| `CHANGELOG.md` | 版本号 + 变更说明 |

### 4.3 不动文件（保护清单）

- `avatar_studio_inline/oss_uploader.py` — DashScope 链路仍需要
- `avatar_studio_inline/llm_json_parser.py`
- `avatar_studio_inline/vendor_client.py`
- `avatar_task_manager.py`
- `avatar_dashscope_client.py` — 仅作为后端之一保留，不删

---

## 5. 关键设计

### 5.1 settings schema 演进

新增字段，旧字段全部保留：

```python
class SettingsBody(BaseModel):
    # 旧字段（不动）
    api_key: str = ""              # 百炼 API Key
    base_url: str = "..."          # 百炼 endpoint
    timeout: float = 60.0
    region: str = "cn-beijing"
    oss_endpoint: str = ""         # OSS（仅 DashScope 用）
    oss_bucket: str = ""
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""
    oss_url_expire_seconds: int = 3600
    custom_data_dir: str = ""
    output_subdir_mode: str = "task"
    output_naming_rule: str = "{filename}"
    retention_days: int = 30
    cost_threshold_cny: float = 5.0

    # 新字段
    backend: Literal["dashscope", "runninghub", "comfyui_local"] = "dashscope"
    runninghub_api_key: str = ""
    runninghub_instance_type: str = ""   # ""/"standard"/"plus"
    comfyui_url: str = "http://127.0.0.1:8188"
    comfyui_api_key: str = ""             # 本地 ComfyUI 的鉴权（可选）
    tts_engine: Literal["cosyvoice", "edge"] = "cosyvoice"
    tts_voice_edge: str = "zh-CN-XiaoxiaoNeural"  # edge-tts 默认音色
    mode_model_overrides: dict[str, str] = {}     # {mode_id: model_or_workflow_id}
```

### 5.2 模型注册表

`avatar_model_registry.py` 暴露：

```python
@dataclass(frozen=True)
class ModelOption:
    backend: Literal["dashscope", "runninghub", "comfyui_local"]
    model_id: str           # DashScope: 模型名；ComfyKit: workflow_id 或 .json 路径
    label_zh: str
    label_en: str
    is_default: bool = False
    requires_auth: bool = False    # 是否需要单独授权（百炼数字人）
    price_per_sec_cny: float = 0
    notes: str = ""

MODEL_REGISTRY: dict[str, list[ModelOption]] = {
    "photo_speak": [
        ModelOption("dashscope", "wan2.2-s2v", "万相数字人 wan2.2-s2v",
                    "Wan 2.2 S2V (Bailian)", is_default=True,
                    requires_auth=True, price_per_sec_cny=0.5),
        ModelOption("dashscope", "emo-v1", "悦动人像 EMO（便宜）", "EMO v1",
                    requires_auth=True, price_per_sec_cny=0.08),
        ModelOption("dashscope", "liveportrait", "灵动人像（>20s）",
                    "LivePortrait", requires_auth=True, price_per_sec_cny=0.04),
        ModelOption("runninghub", "", "RunningHub 工作流（自填）",
                    "RunningHub workflow", price_per_sec_cny=0.002,
                    notes="去 runninghub.cn 搜 wan2.2-s2v 复制 ID"),
        ModelOption("comfyui_local", "workflows/photo_speak.json",
                    "本地 ComfyUI", "Local ComfyUI"),
    ],
    "video_relip": [...],
    "video_reface": [...],
    "avatar_compose": [...],
}

def options_for(mode: str, backend: str) -> list[ModelOption]: ...
def resolve_model(mode: str, backend: str, override: str | None) -> ModelOption: ...
```

### 5.3 workflow JSON 格式

```json
// workflows/photo_speak.json
{
  "_comment": [
    "RunningHub workflow_id 占位文件。",
    "1. 注册 https://www.runninghub.cn",
    "2. 工作流广场搜「wan2.2-s2v 数字人」",
    "3. 选一个跑通的（看运行次数和评分），fork 到自己账号",
    "4. URL 末尾的数字串就是 workflow_id，填到下面 runninghub.workflow_id",
    "本地 ComfyUI 的话，把 selfhost.graph 替换为完整 workflow 内容"
  ],
  "runninghub": {
    "workflow_id": "",
    "node_mappings": {
      "image_url": {"node_id": "", "field": "image"},
      "audio_url": {"node_id": "", "field": "audio"}
    }
  },
  "selfhost": {
    "graph": null,
    "node_mappings": {}
  }
}
```

`node_mappings` 解决 RunningHub OpenAPI 的痛点 —— 提交参数要按 `nodeId/fieldName` 注入，而不是按变量名。

### 5.4 双 TTS 引擎接口约定

```python
# 共同接口
class TTSResult(TypedDict):
    bytes: bytes
    duration_sec: float

async def synth_voice(text: str, voice_id: str, *, speed: float = 1.0) -> TTSResult:
    ...
```

`avatar_dashscope_client.synth_voice` 已经有，新增 `avatar_tts_edge.synth_voice`，pipeline 第 4 步按 `tts_engine` 选一个调。

edge-tts 默认音色（12 个，跟 cosyvoice 数量对齐）：

| voice_id | 中文名 | 性别 | 风格 |
|---|---|---|---|
| zh-CN-XiaoxiaoNeural | 晓晓 | 女 | 温暖知性 |
| zh-CN-XiaoyiNeural | 晓伊 | 女 | 活泼可爱 |
| zh-CN-YunjianNeural | 云健 | 男 | 沉稳磁性 |
| zh-CN-YunxiNeural | 云希 | 男 | 朝气阳光 |
| zh-CN-YunxiaNeural | 云夏 | 男 | 少年清亮 |
| zh-CN-YunyangNeural | 云扬 | 男 | 新闻播报 |
| zh-CN-XiaohanNeural | 晓涵 | 女 | 知性温柔 |
| zh-CN-XiaomengNeural | 晓梦 | 女 | 甜美可爱 |
| zh-CN-XiaomoNeural | 晓墨 | 女 | 优雅 |
| zh-CN-XiaoqiuNeural | 晓秋 | 女 | 成熟稳重 |
| zh-CN-XiaoruiNeural | 晓睿 | 女 | 老成 |
| zh-CN-XiaoxuanNeural | 晓萱 | 女 | 自信 |

---

## 6. UI 改动详细

### 6.1 SettingsTab 新增"后端通道"section

位置：在「API Key」section 之后、「OSS 配置」section 之前。

```
┌─ 后端通道 ──────────────────────────────────────────┐
│                                                      │
│  ⦿ 阿里云百炼 (Bailian)        [当前: ✓ 已配置]     │
│  ○ RunningHub (云上 ComfyUI)   [未配置]            │
│  ○ 本地 ComfyUI                [未配置]            │
│                                                      │
│  说明：选择 avatar-studio 调用 AI 模型的后端。       │
│  详细对比见文档。                                     │
└──────────────────────────────────────────────────────┘
```

每个 radio 选中时下方展开对应配置块：

- **Bailian**：现有 API Key + base_url（不变）
- **RunningHub**：API Key 输入 + 实例规格下拉 + 「测试连接」按钮
- **本地 ComfyUI**：URL 输入（默认 http://127.0.0.1:8188）+ API Key 可选 + 「测试连接」按钮

### 6.2 SettingsTab 新增"语音合成 (TTS)"section

```
┌─ 语音合成 (TTS) ────────────────────────────────────┐
│                                                      │
│  TTS 引擎：[百炼 cosyvoice-v2 (中文佳) ▼]           │
│            （或：edge-tts (微软免费)）               │
│                                                      │
│  默认音色：[龙小淳 (女, 知性温暖) ▼]                │
│            （edge-tts 切换后下拉变成 12 个微软音色） │
└──────────────────────────────────────────────────────┘
```

### 6.3 CreateTab 顶部新增"模型选择"

每个 mode 卡片下面新增一行下拉：

```
┌─ 照片说话 (photo_speak) ────────────────────────────┐
│                                                      │
│  模型: [万相数字人 wan2.2-s2v ▼] (DashScope)        │
│        ├─ 万相数字人 wan2.2-s2v  ¥0.5/秒            │
│        ├─ 悦动人像 EMO（便宜）   ¥0.08/秒          │
│        ├─ 灵动人像 LivePortrait  ¥0.04/秒          │
│        └─ RunningHub 工作流       ¥0.002/秒         │
│                                                      │
│  ...原有图片/音频上传区...                           │
└──────────────────────────────────────────────────────┘
```

下拉只显示**当前 backend** 下的候选。如果用户在设置里把 backend 切到 RunningHub，这里下拉就只显示 RH 工作流候选。

### 6.4 i18n key 新增

```
settings.backend.title          后端通道 / Backend
settings.backend.bailian        阿里云百炼
settings.backend.runninghub     RunningHub
settings.backend.comfyui_local  本地 ComfyUI
settings.backend.help           …
settings.rh.api_key             RunningHub API Key
settings.rh.instance_type       实例规格
settings.rh.test_btn            测试连接
settings.comfyui.url            ComfyUI 地址
settings.tts.title              语音合成 (TTS)
settings.tts.engine             TTS 引擎
settings.tts.voice              默认音色
create.mode.model_select        模型 / Workflow
create.mode.model_help          ...
```

---

## 7. 7 阶段实施清单

| 阶段 | 内容 | 时长 | 风险 | 阻塞依赖 |
|---|---|---|---|---|
| 1 | 修 PRICE_TABLE + 加依赖 + 写 model_registry 骨架 | 1 小时 | 低 | — |
| 2 | 写 `avatar_comfy_client.py` + workflow JSON 占位 | 4 小时 | 中 | 1 |
| 3 | 写 `avatar_tts_edge.py` + edge-tts 集成测试 | 3 小时 | 中 | 1 |
| 4 | 改 `plugin.py`：settings 字段 + dispatch + 测试连接接口 | 6 小时 | 高 | 1, 2, 3 |
| 5 | 改 `avatar_pipeline.py`：8 步按 backend / tts_engine 分流 | 4 小时 | 高 | 4 |
| 6 | 改 UI：后端 radio + TTS 下拉 + 模型选择 + i18n | 8 小时 | 中 | 4 |
| 7 | 测试矩阵 + 文档 + CHANGELOG | 4 小时 | 低 | 5, 6 |

**合计：约 30 小时（4 个工作日）**

---

## 8. 测试矩阵

### 8.1 单元测试新增

| 测试文件 | 覆盖 |
|---|---|
| `test_model_registry.py` | 注册表完整性、`options_for(mode, backend)` 路径、`resolve_model` 默认/覆盖逻辑 |
| `test_comfy_client.py` | mock `comfykit.ComfyKit.execute` 成功 / 失败 / 超时；workflow 参数注入；本地/RH 双路径 |
| `test_tts_edge.py` | mock `edge-tts.Communicate` 成功 / 网络失败；音频时长检测 |

### 8.2 端到端 / smoke

| 用例 | 步骤 | 预期 |
|---|---|---|
| E1 | 设置 backend=dashscope → 跑 photo_speak | 走原有链路，OSS 上传 + DashScope 调用 |
| E2 | 设置 backend=runninghub + 填 RH key + 填 workflow_id → 跑 photo_speak | 不走 OSS，调用 RH OpenAPI，拿到视频 URL |
| E3 | 设置 backend=comfyui_local + 启动本地 ComfyUI → 跑 photo_speak | 不走 OSS，调本地 8188，拿到视频 URL |
| E4 | 切换 tts_engine=edge | 第 4 步走 edge-tts，前后步不变 |
| E5 | mode_model_overrides 切换 photo_speak 到 emo-v1 | 第 6 步调 emo-v1 而不是 wan2.2-s2v |
| E6 | RunningHub workflow_id 留空 → 点生成 | UI 报"未配置 workflow_id"，不调用 |
| E7 | 跨后端切换：先 dashscope 跑完 → 切 runninghub → 任务历史保留 | task_manager 不受 backend 影响 |

### 8.3 回归测试

- 现有 `test_pipeline.py` 全部通过（DashScope 路径不能挂）
- 现有 `test_dashscope_client.py` 全部通过
- 现有 `test_task_manager.py` 全部通过
- 现有存储 / OSS / settings 烟雾测试全部通过

---

## 9. 风险登记

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| RH 公开 workflow 下线 | 高 | 中 | 设计上让用户自填 ID + 在 UI 给"如何获取 workflow_id"链接 |
| RH OpenAPI 限流 | 中 | 中 | comfykit 内置重试；UI 显示 RH 排队提示 |
| 本地 ComfyUI 节点版本不兼容 | 中 | 低 | 文档说明"建议用 ComfyUI-Manager 装最新节点" |
| edge-tts 被 Azure 端点限流 | 低 | 中 | comfykit 同款重试 + fallback 到 cosyvoice |
| settings 迁移失败（旧用户升级） | 低 | 高 | 新字段全部给默认值；保留旧字段；写迁移测试 |
| 双后端 UI 复杂度溢出 | 中 | 中 | 默认 backend=dashscope（旧行为），不主动暴露其他选项；高级用户主动选 |
| comfykit 0.1.x 是 Beta 版 | 中 | 高 | pin 到 0.1.12，每次升级前手动 smoke；准备直连 RH OpenAPI 的回滚方案 |

---

## 10. 回滚方案

每个阶段都可独立回滚：

1. 阶段 1 改的是数据，git revert 即可
2. 阶段 2-3 是新文件，删除即可（不影响旧链路）
3. 阶段 4-5 改了 plugin.py / pipeline.py，git revert 即可
4. 阶段 6 改了 UI，git revert + 重新载入插件
5. 阶段 7 是文档

整体回滚：`git revert <commit_range>` + 重新 `pip install` 旧 `requirements.txt`。

---

## 11. 下一步

按用户的指示：
- ✅ 已写本文档
- ⏸ 等用户审阅本文档 + `findings/comfyui_runninghub_intro.md`
- ⏸ 用户确认后按"7 阶段实施清单"顺序开工

---

## 附录 A：文件位置速查

| 路径 | 用途 |
|---|---|
| `D:/OpenAkita/plugins/avatar-studio/` | 插件代码 |
| `D:/OpenAkita/plugins/avatar-studio/workflows/` | （新）workflow JSON |
| `D:/OpenAkita/docs/avatar-studio-comfyui-routing.md` | 本文档 |
| `D:/OpenAkita_AI_Video/findings/comfyui_runninghub_intro.md` | 基础扫盲 |
| `D:/OpenAkita_AI_Video/findings/pixelle_video_deep.md` | Pixelle-Video 调研 |
| `D:/OpenAkita_AI_Video/refs/Pixelle-Video/` | Pixelle-Video 源码参考 |

## 附录 B：相关链接

- ComfyUI: https://github.com/comfyanonymous/ComfyUI
- RunningHub: https://www.runninghub.cn
- comfykit: https://github.com/puke3615/ComfyKit
- comfykit PyPI: https://pypi.org/project/comfykit/
- edge-tts: https://github.com/rany2/edge-tts
- 百炼模型总览: https://help.aliyun.com/zh/model-studio/use-video-generation
- 百炼 wan2.2-s2v: https://help.aliyun.com/zh/model-studio/wan-s2v-overview/
- 百炼错误码: https://help.aliyun.com/zh/model-studio/error-code
