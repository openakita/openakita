# 选题研析室 / Idea Research

> OpenAkita 第 9 个一线插件，定位「选题/爆款拆解雷达」：默认走公开 API/RSS 安全引擎开箱即用，
> 高级模式启用 MediaCrawler 风格的浏览器爬虫覆盖小红书/抖音/快手/B站登录态/微博 5 平台，
> **首发即接入 OpenAkita 记忆图谱（vector + memory_manager 双轨）让推荐越用越准**。
> UI 采用「单页雷达工作台 + Tasks 历史 + Settings」3 Tab，主 Tab 左输入永驻、右结果永驻、
> 改任意输入实时反馈。

| | |
|---|---|
| 版本 | 1.0.0（首发，OpenAkita 第 1 个真正接入 MDRM 的一线插件） |
| 兼容 | OpenAkita ≥ 1.28.0 / openakita-plugin-sdk `>=0.7.0,<0.8.0` / Python ≥ 3.11 |
| 模式 | `radar_pull` / `breakdown_url` / `compare_accounts` / `script_remix` |
| 平台 | 引擎 A: B站 / YouTube / 抖音 RSS / 小红书 RSS / 微博 RSS<br/>引擎 B: 抖音 / 小红书 / 快手 / B站登录态 / 微博 |
| 接入面 | 26 routes + 9 tools + 1 sidebar UI |

---

## 1 简介与截图

```
┌──────────────────────────────────────────────────────────────────────┐
│  📡 选题研析室   Workbench   Tasks   Settings   📚 已学 12 个 hook 模式 │
├──────────────────────────────────────────────────────────────────────┤
│  brain ●  vector ●  memory ●  [Reindex] [Clear]                      │
├──────────────────────┬───────────────────────────────────────────────┤
│  1 ▾ 赛道订阅        │  今日推荐已更新 3m ago · 共 20 条 · 引擎 A+B     │
│    平台: [B站][抖音] │  ┌───────────────────────────────────────────┐ │
│    关键词: [AI] [+]  │  │ [cover] AI 三分钟讲懂 LangGraph           │ │
│    时间窗: 24h ▾     │  │  B站 · 张三 · 👍 23k 💬 1.2k 👁 230k       │ │
│  2 ▸ 对标账号        │  │  🪝 数据冲击 · 📚 已学命中 ×2              │ │
│  3 ▸ Persona         │  │  [展开拆解] [送往 clip-sense] [保存] [脚本] │ │
│  4 ▾ 粘 URL 即拆     │  └───────────────────────────────────────────┘ │
│    [https://... ]    │  ┌───────────────────────────────────────────┐ │
│    [▶ 创建任务]      │  │ ...                                        │ │
│  5 ▾ 高级            │  └───────────────────────────────────────────┘ │
│    引擎B [○]         │                                                │
│    数量: 20          │      [3 项变更将影响推荐] [应用变更] [稍后]     │
│    MDRM 加权 [●]     │                                                │
└──────────────────────┴───────────────────────────────────────────────┘
```

---

## 2 安装与依赖

### 2.1 主程序

把仓库整体放到 `plugins/idea-research/`，OpenAkita 启动时会自动发现并加载。

### 2.2 必装依赖

```powershell
# 主程序 venv 已有
pip install httpx>=0.27 pydantic>=2.6
# 拆解 pipeline 必备（系统命令）
winget install yt-dlp
winget install Gyan.FFmpeg     # 或 choco install ffmpeg
```

### 2.3 可选依赖（按需）

| 依赖 | 用途 | 安装命令 | 缺失影响 |
|---|---|---|---|
| `faster-whisper` | 本地 ASR（< 10min 视频） | `pip install faster-whisper` | 自动降级走 DashScope Paraformer |
| `playwright` | 引擎 B 浏览器爬虫 | `pip install playwright && playwright install chromium` | 引擎 B 不可用，仍可用引擎 A |
| `cryptography` + `keyring` | cookies Fernet 加密存储 | `pip install cryptography keyring` | cookies 走明文降级 + UI 黄色 warn |

### 2.4 必填配置

进入 Settings Tab → AI Keys：

- **DashScope API Key**（必填）— 用于 ASR / VLM / LLM；获取地址 https://dashscope.aliyun.com
- **YouTube Data API Key**（可选）— 引擎 A 拉 YouTube trending 用；缺失走 RSS Hub 降级
- **RSS Hub 实例 URL**（可选）— 默认 `https://rsshub.app`，建议自部署提速

---

## 3 4 Mode 概览

### 3.1 `radar_pull` 雷达拉榜

> 用例：每天早上拉 24h 内多平台爆款列表 → 选题灵感

```python
# 通过 SDK 工具调用
result = await ctx.call_tool("idea_radar_pull", {
    "platforms": ["bilibili", "youtube"],
    "keywords": ["AI", "智能体"],
    "time_window": "24h",
    "engine": "auto",
    "limit": 20,
})
# → {"task_id": "...", "status": "pending", "eta_s": 30}
```

或 HTTP：

```bash
curl -X POST http://localhost:8080/api/plugins/idea-research/tasks \
  -H "Content-Type: application/json" \
  -d '{"mode":"radar_pull","input":{"platforms":["bilibili"],"keywords":["AI"],"time_window":"24h","limit":20,"mdrm_weighting":true}}'
```

### 3.2 `breakdown_url` 单条拆解（核心）

> 用例：粘一条爆款 URL → 8 步 pipeline 输出 ASR + 关键帧 + 结构化钩子 + 评论摘要 + persona takeaways

```bash
curl -X POST http://localhost:8080/api/plugins/idea-research/tasks \
  -H "Content-Type: application/json" \
  -d '{"mode":"breakdown_url","input":{"url":"https://www.bilibili.com/video/BV1xx411c7XX","persona":"B站知识博主","write_to_mdrm":true}}'
```

8 步流水：`setup_environment → resolve_source → download_media → asr_transcribe →
visual_keyframes → structure_analyze → comment_summary → finalize`（含 MDRM 双轨写入）。

### 3.3 `compare_accounts` 对标分析

> 用例：贴 N 个对标账号主页 → 输出 hook 分布、发文节奏、主题图谱、绩效排名

```bash
curl -X POST http://localhost:8080/api/plugins/idea-research/tasks \
  -H "Content-Type: application/json" \
  -d '{"mode":"compare_accounts","input":{"account_urls":["https://space.bilibili.com/12345"],"window":"30d","max_videos_per_account":20}}'
```

### 3.4 `script_remix` 选题→脚本

> 用例：从推荐流挑一条 → 改写成 N 版我的品牌脚本（自动注入 MDRM 历史成功 hook）

```bash
curl -X POST http://localhost:8080/api/plugins/idea-research/tasks \
  -H "Content-Type: application/json" \
  -d '{"mode":"script_remix","input":{"trend_item_id":"<id>","my_persona":"小红书运营专家","my_brand_keywords":["国货","护肤"],"target_duration_seconds":60,"num_variants":3,"target_platform":"xhs","use_mdrm_hints":true}}'
```

---

## 4 双引擎 + 风险声明

| 引擎 | 永远开 | 平台 | 优点 | 缺点 |
|---|---|---|---|---|
| **A 公开 API + RSS** | ✓ | B站 / YouTube / 抖音 RSS / 小红书 RSS / 微博 RSS / yt-dlp | 零风险、稳定、免维护 | 数据质量参差（RSS 无互动数据） |
| **B 浏览器爬虫**（高级） | 默认关 | 抖音 / 小红书 / 快手 / B站登录态 / 微博 | 数据全（含登录态） | 需贴 cookies、可能被风控、违反平台 ToS |

### 4.1 ⚠ 风险声明（启用引擎 B 前必读）

启用引擎 B 即代表你**理解并承担**以下风险：

- **可能违反目标平台用户协议**（小红书 / 抖音 / 快手 / 微博 / B 站均明确禁止自动化抓取）
- **cookies 可能被平台风控** — 帐号可能被临时封禁或限流
- **法律责任由用户自行承担** — OpenAkita 仅提供技术能力，不对滥用负责
- **采集频率请克制** — 每平台单次抓取上限 30-100 条；建议 ≥ 5 分钟间隔

启用步骤：Settings → 数据源 → 勾选「我已知晓风险」→ 引擎 B toggle 打开 → 5 张 cookies 卡片各自填入。

### 4.2 5 平台 cookies 获取教程

| 平台 | 必备 cookies key | 获取方式 |
|---|---|---|
| 抖音 | `sessionid_ss`, `s_v_web_id`, `ttwid` | 浏览器开发者工具 / Cookie-Editor 扩展导出 |
| 小红书 | `web_session`, `xsecappid`, `a1` | 同上 |
| 快手 | `did`, `kpf`, `kpn`, `clientid` | 同上 |
| B站登录态 | `SESSDATA`, `bili_jct`, `DedeUserID` | 同上 |
| 微博 | `SUB`, `SUBP`, `XSRF-TOKEN` | 同上（移动端 m.weibo.cn 不需登录） |

---

## 5 MDRM 记忆图谱说明（首发亮点）

idea-research 是 OpenAkita 第 1 个真正使用 MDRM 4 SDK 接入口的一线插件：

| SDK 接入口 | 权限 | 用法 |
|---|---|---|
| `api.get_brain()` | `brain.access` | DashScope 故障兜底 / 复用宿主 LLM |
| `api.get_memory_manager()` | `memory.read` / `memory.write` | 记录「该赛道历史 hook 模式 / 该 persona 偏好」 |
| `api.get_vector_store()` | `vector.access` | 向量化 hook 文本，下次推荐检索相似 |
| `api.register_memory_backend()` | `memory.write` | **不用**（idea-research 是参与者不是替代者） |

### 5.1 双轨写入 / 单轨读出

- **写入**：每次 `breakdown_url` 完成 → 提取 hook → vector add_documents + memory_manager 结构化记录 → 本地 `hook_library` 表镜像
- **读出**：每次 `radar_pull` 算分时 → vector_store.search 相似 hook → 命中越多，score 加权越大（最多 +100%）
- **快照**：UI 顶部状态条实时显示「📚 已学 N 个 hook 模式」

### 5.2 3 权限授予指南

进入 OpenAkita Plugin Manager → idea-research → Permissions 面板，逐个勾选：

```
☑ brain.access     # DashScope 故障兜底
☑ vector.access    # hook 向量检索（推荐越用越准）
☑ memory.write     # hook 结构化记忆（跨 persona 复用）
☑ memory.read      # 反查历史决策
```

### 5.3 降级矩阵（任一权限缺失 / 服务不可用，决不阻断主流程）

| 状态 | brain 兜底 | hook 写入 | hook 检索 | UI 状态条 |
|---|---|---|---|---|
| 全 3 权限 + vector_ready | ✓ | vector ✓ + memory ✓ | ✓ | 📚 MDRM 已学 N 个 hook 模式 |
| brain.access 缺 | ✗ | 同上 | 同上 | 黄 warn「未授权 brain.access」 |
| vector.access 缺 | 同上 | memory ✓ + vector skipped | ✗ | 黄 warn「未授权 vector.access」 |
| memory.write 缺 | 同上 | vector ✓ + memory skipped | ✓ | 黄 warn「未授权 memory.write」 |
| 全权限缺 | ✗ | 全 skipped | ✗ | 红 warn「MDRM 完全未启用」 |
| ChromaDB 模型未下载完 | 视情况 | vector queued | ✗ | 灰 hint「向量模型加载中（首次约 5min）」 |

---

## 6 API Reference

### 6.1 26 routes 简表

> 全部前缀：`/api/plugins/idea-research`

| # | Method | Path | 用途 |
|---|---|---|---|
| 1 | POST | `/tasks` | 创建任务（4 mode） |
| 2 | POST | `/cost-preview` | 预估费用（不真跑） |
| 3 | GET | `/tasks` | 列表 |
| 4 | GET | `/tasks/{id}` | 详情 |
| 5 | POST | `/tasks/{id}/cancel` | 取消 |
| 6 | POST | `/tasks/{id}/retry` | 重试 |
| 7 | DELETE | `/tasks/{id}` | 删除（含工作目录） |
| 8 | GET | `/tasks/{id}/breakdown` | 取 breakdown.json |
| 9 | GET | `/recommendations` | Workbench 右栏数据源 |
| 10 | POST | `/items/{id}/save` | 保存选题 |
| 11 | GET | `/subscriptions` | 列订阅 |
| 12 | POST | `/subscriptions` | 新建/更新订阅 |
| 13 | DELETE | `/subscriptions/{id}` | 删订阅 |
| 14 | GET | `/settings` | 加载所有 settings |
| 15 | PUT | `/settings` | 写 settings |
| 16 | GET | `/sources` | 数据源面板（含 5 platform cookies status） |
| 17 | POST | `/sources/cookies/{platform}` | 写 cookies |
| 18 | POST | `/sources/cookies/{platform}/test` | 测试 cookies 连通性 |
| 19 | POST | `/accounts/preview` | 对标账号预览 |
| 20 | POST | `/cleanup` | 数据清理 |
| 21 | GET | `/healthz` | 健康检查 |
| 22 | POST | `/upload` | 上传本地视频 |
| 23 | GET | `/uploads/{rel_path}` | 上传文件预览 |
| 24 | GET | `/mdrm/stats` | MDRM 状态 |
| 25 | POST | `/mdrm/clear` | 清空学习记录 |
| 26 | POST | `/mdrm/reindex` | 重新索引历史 breakdown |

### 6.2 9 tools 简表

| # | name | description |
|---|---|---|
| 1 | `idea_radar_pull` | 拉取多平台爆款列表（按 互动+时效+关键词+MDRM 评分排序） |
| 2 | `idea_breakdown_url` | 拆解单条视频 URL → ASR + 关键帧 + 结构化 + 评论摘要 + persona takeaways |
| 3 | `idea_compare_accounts` | 对标 N 个账号近期视频，输出共性、差异、空白与建议 |
| 4 | `idea_script_remix` | 把选题改写成 N 版可执行脚本（可选 MDRM 历史 hook 注入） |
| 5 | `idea_subscribe` | 创建/更新雷达订阅 |
| 6 | `idea_unsubscribe` | 删除订阅 |
| 7 | `idea_list_subscriptions` | 列出所有订阅 |
| 8 | `idea_export` | 导出选题/拆解结果（json/markdown/csv） |
| 9 | `idea_cancel` | 取消运行中任务 |

---

## 7 错误码 11 类对照表

| # | error_kind | 触发场景 | hint_zh | hint_en |
|---|---|---|---|---|
| 1 | `network` | ConnectError / 5xx / region_block | 网络异常或目标平台无法访问，请检查网络/代理后重试 | Network error or platform unreachable |
| 2 | `timeout` | TimeoutError / ReadTimeout | 请求超时，请稍后重试或减小数据量 | Request timeout |
| 3 | `auth` | 401 / cookies 过期 / API key 无效 | 鉴权失败，请检查 API Key 或重新导入 cookies | Auth failed |
| 4 | `quota` | 429 quota exceeded / 余额不足 | 配额或余额不足，请充值或更换 key | Quota or balance exhausted |
| 5 | `moderation` | LLM 返回内容审核拒绝 | 内容被平台审核拦截，请调整输入 | Content blocked by moderation |
| 6 | `rate_limit` | 429 短时限速 | 请求过于频繁，已自动 backoff，请稍候 | Rate limited |
| 7 | `dependency` | yt-dlp/ffmpeg/playwright 缺失 | 缺少系统依赖，请运行对应安装命令 | Missing system dep |
| 8 | `format` | 字段缺失 / JSON 解析失败 / URL 不合法 | 数据格式异常，请检查输入或联系反馈 | Bad data format |
| 9 | `unknown` | 未识别异常 | 未知异常，详情见日志 | Unknown error |
| 10 | `cookies_expired` | 引擎 B cookies 测试失败 | cookies 已过期，请到 Settings → 数据源 重新导入 | Cookies expired |
| 11 | `crawler_blocked` | 引擎 B 被风控 / 验证码 | 平台风控触发，建议更换 cookies 或切回 API 引擎 A | Anti-bot triggered |

> MDRM 写入失败 / 检索失败 **不计入错误码**，仅 `logger.warning` + UI 黄色提示，因为不是用户感知的「任务失败」。

---

## 8 5 分钟烟测脚本（亲跑过 5/5 步过 = 验收通过）

```powershell
# 假设 D:\OpenAkita 主程序已启动，DASHSCOPE_API_KEY 已在 Settings 配置
# 假设 brain.access / vector.access / memory.write 三权限已在 Plugin Manager 授予

# Step 1 (60s) — 健康检查 + radar_pull
curl http://localhost:8080/api/plugins/idea-research/healthz
curl http://localhost:8080/api/plugins/idea-research/mdrm/stats
curl -X POST http://localhost:8080/api/plugins/idea-research/tasks `
  -H "Content-Type: application/json" `
  -d '{"mode":"radar_pull","input":{"platforms":["bilibili"],"keywords":["AI"],"time_window":"24h","limit":10,"mdrm_weighting":true}}'

# Step 2 (60s) — 等任务完成 + 拉推荐
Start-Sleep 30
curl 'http://localhost:8080/api/plugins/idea-research/recommendations?limit=10'

# Step 3 (90s) — breakdown_url 单条拆解（用 Step 2 返回的第一个 URL）
$url = "https://www.bilibili.com/video/BV1xx411c7XX"
curl -X POST http://localhost:8080/api/plugins/idea-research/tasks `
  -H "Content-Type: application/json" `
  -d "{`"mode`":`"breakdown_url`",`"input`":{`"url`":`"$url`",`"persona`":`"B站知识博主`",`"write_to_mdrm`":true}}"

# Step 4 (60s) — 检查 breakdown 完成 + 取详情 + MDRM 写入状态
Start-Sleep 60
curl http://localhost:8080/api/plugins/idea-research/tasks
curl http://localhost:8080/api/plugins/idea-research/tasks/<task_id>/breakdown
curl http://localhost:8080/api/plugins/idea-research/mdrm/stats   # hook_count 应 +1

# Step 5 (30s) — UI 检查
# 浏览器打开 http://localhost:8080/#/plugins/idea-research
# - Workbench Tab 顶部状态条显示「📚 已学 1 个 hook 模式」
# - 右栏出现 ≥ 1 张推荐卡
# - 点「展开拆解」抽屉滑出，含脚本 + 词频 + 帧 + persona takeaways
# - Tasks Tab 出现 2 条记录（radar_pull + breakdown_url，status=done），
#   详情抽屉显示「MDRM: vector ok / memory ok」
# - Settings Tab 5 section 全可见，MDRM 子卡显示 caps 全 ✓ + hook_count=1，
#   引擎 B toggle 可切换 5 张 cookies 卡
```

5 步全过 = 烟测通过。

---

## 9 文档与协同

- **SKILL.md** — 给后续 AI agent 提供 10 节修改 skill
- **USER_TEST_CASES.md** — 31 case 用户验收清单（4 mode × 7 + 3 MDRM）
- **CHANGELOG.md** — 版本变更记录
- **idea-research → clip-sense** — `script_remix` 输出可一键 handoff 给剪辑插件
- **idea-research → avatar-studio** — `script_remix` 数字人版本输出
- **idea-research → IM 通道** — 早 9 点定时推送当日 5 选题
- **idea-research → MDRM** — hook 模式累积，跨插件复用

---

## 10 反馈与许可

- 仓库 https://github.com/openakita/openakita
- License Apache-2.0
- 设计文档 `~/.cursor/plans/trend-radar_plugin_3881ed71.plan.md` v3
