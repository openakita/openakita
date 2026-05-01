# Omni Post (`omni-post`)

> 全媒发布 · 一次创作，多平台多账号落地。一线插件、零 SDK contrib 依赖、
> 零 host UI 资源挂载。

| | |
|---|---|
| **版本** | 0.2.0 (Sprint 1-4 完整发布) |
| **SDK 范围** | `>=0.7.0,<0.8.0` |
| **Plugin API** | `~2` / UI API `~1` |
| **入口** | `plugin.py` (`PluginBase`) + `ui/dist/index.html` |
| **形态** | 双引擎（Playwright / MultiPost Compat）· 10 平台 · 6 Tab · 14 工具 |

---

## 1 · 概览

omni-post 把一次内容创作（视频 / 图文 / 长文）在同一条时间线上分发到 N 个
平台 × M 个账号，让剪映 / 爱剪辑 / 夸克 / 即梦导出的素材 **在 60 秒内开始
真实发布**，任务状态、失败原因、重试截图一并回写到本机的 SQLite，
并把 "已发素材" 作为 `publish_receipt` 推上 Asset Bus 供下游插件消费
（例如 `idea-research` 统计同一主题在各平台的表现）。

两条引擎并存：

- **Playwright 自研引擎（默认）**：host 单进程起一个 Chromium，每个账号一套
  独立的 `user_data_dir`，通过外部 JSON `selectors_health` 驱动，可以在
  不升级插件代码的情况下追平平台 UI 变更。
- **MultiPost Compat（可选）**：当用户已经安装 MultiPost 浏览器扩展时，
  走 `window.postMessage` + 信任域握手，复用扩展本身维护的全平台登录态。
  插件内置 **MultiPostGuide** 安装/信任引导组件。

目标平台（S1–S2 渐进开放）：

| 平台 | 类型 | S1 | S2 |
|---|---|:-:|:-:|
| 抖音 Creator | 视频 | ✅ | |
| 小红书 | 图文/视频 | ✅ | |
| B 站 | 视频 | ✅ | |
| 微信视频号 | 视频（微前端） | | ✅ |
| 快手 | 视频 | | ✅ |
| YouTube | 视频 | | ✅ |
| TikTok | 视频 | | ✅ |
| 知乎 | 图文 | | ✅ |
| 微博 | 图文/视频 | | ✅ |
| 微信公众号 | 图文 | | ✅ |

---

## 2 · 6 Tab 一览

| Tab | 内容 | 关键交互 |
|---|---|---|
| **Publish** | 素材选择 + 文案 + 标签 + 平台矩阵 + 账号矩阵 + 立即/定时发布 | 一次作业扇出到 N × M 任务；`client_trace_id` 去重；发布前 quota 预检 |
| **Tasks** | 任务列表 + 过滤 + 详情抽屉（payload / error / 截图） | 失败可 "重投"、"半自动兜底"；截图自动 redact cookie 字段 |
| **Accounts** | 账号矩阵 + 每账号已发素材列表 + Cookie 健康探针 | S2 开放 |
| **Calendar** | 定时发布日历 + 时区错峰 + 矩阵模板 | S3 开放 |
| **Library** | 素材库 + 模板库 + 秒传归档 | S3 开放 |
| **Settings** | 引擎切换 · 代理 · 截图策略 · 日志保留 · MultiPost 引导 · 自愈告警 | S4 补齐 |

---

## 3 · 安装

omni-post 跟随 OpenAkita 主仓发布。插件依赖由插件自身兜底管理：

- **cryptography**：用于 Fernet Cookie 加密。插件入口会先通过
  `omni_post_dep_bootstrap.py` 检查 `cryptography.fernet`，缺失时安装到
  `~/.openakita/modules/omni-post/site-packages`，不会再依赖主程序的
  `requires.pip` 安装路径。
- **Playwright 浏览器二进制**（自研引擎必需）：可在 Settings → 依赖与运行环境
  一键安装，也可手动执行：
  ```bash
  python -m playwright install chromium
  ```
- **ffmpeg / ffprobe**（强烈建议，用于素材 probe 与缩略图，缺失则优雅降级）：
  Settings → 依赖与运行环境会展示检测状态、安装日志和手动命令。
  ```bash
  # Windows
  winget install --id Gyan.FFmpeg -e
  # macOS
  brew install ffmpeg
  # Linux
  sudo apt install ffmpeg
  ```
- **MultiPost Compat 引擎**（可选）：从
  [MultiPost-Extension](https://github.com/leaperone/MultiPost-Extension)
  的 Releases 下载浏览器扩展，插件 Settings Tab 会自动检测安装状态。

开发态：

```bash
cd plugins/omni-post
py -3.11 -m pytest tests -q          # 应输出 all passed
py -3.11 -m ruff check .             # 0 error
```

---

## 4 · 权限矩阵

12 类权限，均为 OpenAkita 标准声明（见 `plugin.json`）：

| 权限 | 用途 |
|---|---|
| `tools.register` | 暴露 14 个 LLM 可调用工具 |
| `routes.register` | 暴露 22+ FastAPI 路由 |
| `hooks.basic` | 启动/卸载钩子 |
| `config.read` / `config.write` | Settings Tab 的后端偏好与 Cookie 盐文件 |
| `data.own` | 独占 `$DATA_DIR/plugins/omni-post/` 下的 SQLite / uploads / thumbs |
| `assets.publish` / `assets.consume` | 产出 `publish_receipt`、消费上游素材 |
| `memory.read` / `memory.write` | MDRM 记录 "平台 × 账号 × 时段 × 成功率" |
| `brain.access` | LLM 差异化文案与定时推荐 |
| `vector.access` | MDRM 相似素材 / 同主题发布召回（可选） |

---

## 5 · 目录结构（当前 0.2.0）

```
plugins/omni-post/
├── plugin.json                       # manifest，14 tool + 12 permission
├── plugin.py                         # PluginBase 入口 + 路由 + 工具
├── omni_post_dep_bootstrap.py         # cryptography 插件私有自举与诊断
├── omni_post_system_deps.py           # FFmpeg / Playwright Chromium 检测与安装
├── omni_post_models.py               # 13 ErrorKind + PlatformSpec + Pydantic
├── omni_post_task_manager.py         # 7 张表的 aiosqlite CRUD
├── omni_post_cookies.py              # Fernet 加密 Cookie 池 + 懒加载探针
├── omni_post_assets.py               # 分片上传 + MD5 秒传 + ffprobe + 缩略图
├── omni_post_pipeline.py             # 发布编排 + 退避重试 + asset bus 回写
├── omni_post_engine_pw.py            # Playwright 引擎 + 反指纹 + GenericJsonAdapter
├── omni_post_adapters/
│   ├── __init__.py
│   └── base.py                       # PlatformAdapter 抽象 + bundle 校验
├── omni_post_selectors/              # 外置选择器 JSON（S1 三张：抖音/小红书/B 站）
│   ├── douyin.json
│   ├── rednote.json
│   └── bilibili.json
├── tests/                            # pytest 覆盖 models / task_manager / cookies / assets / selectors
├── requirements.txt                  # 仅记录 cryptography；实际由插件自举兜底
└── ui/dist/
    ├── index.html                    # React 18 + Babel 单文件 UI（6 Tab）
    └── _assets/                      # 与 avatar-studio 1:1 的 UI Kit
```

S2–S4 阶段追加的文件已全部就位（绿色表示已合入 main）：

```
plugins/omni-post/
├── omni_post_scheduler.py            # ✅ S3: ScheduleTicker + stagger_slots + fanout_matrix
├── omni_post_engine_mp.py            # ✅ S4: MultiPost compat choreographer
├── omni_post_selfheal.py             # ✅ S4: daily selector probe + IM alerts
├── omni_post_mdrm.py                 # ✅ S4: publish-memory adapter
├── omni_post_selectors/              # ✅ S1→S2: 3 → 10 平台 JSON bundles
└── ui/dist/index.html                # ✅ S1→S4: 6 个完整 Tab
```

---

## 6 · S4 关键能力

### 6.1 MultiPost 信任域握手

Settings Tab 顶部的 **MultiPostGuide** 做四件事：

1. 通过 `window.postMessage` 广播 `MULTIPOST_EXTENSION_CHECK_SERVICE_STATUS`，
   3 秒超时后回落到 "未检测到扩展"；
2. 比对版本号 ≥ `mp_extension_min_version`（默认 `1.3.8`）；
3. 检查扩展侧是否把当前 host 加入信任域；
4. 将探测结论 `POST /mp/status` 同步回后端，`pipeline._resolve_engine`
   就能按实际可用性在 Playwright / MultiPost 之间切换。

### 6.2 选择器自愈（每日）

`SelfHealTicker` 默认每 24 小时跑一次，对 10 张 `omni_post_selectors/*.json`
里的每个选择器询问 `probe_fn` 是否仍可解析。结果写 `selectors_health`：

| 字段 | 含义 |
|---|---|
| `hit_rate` | `(total - failed) / total` |
| `last_error` | 第一条失败样本的简要描述 |
| `last_alerted_at` | 最近一次 IM 告警时刻（`ALERT_COOLDOWN=24h` 内不再重发）|

低于 `ALERT_THRESHOLD=0.6` 时，后端广播 `selector_alert` UI 事件
（`{platform, hit_rate, failed, threshold}`），任一 IM 桥插件订阅转发到
微信 / 飞书 / Slack 即可。

### 6.3 MDRM 发布记忆

每次任务终态通过 `OmniPostMdrmAdapter` 写入宿主 `MemoryManager`，
记忆形状与 `idea-research` 对齐：

```python
SemanticMemory(
    type=MemoryType.EXPERIENCE,
    subject="omni-post:publish:{platform}:{account_id}",
    predicate="success" | "failure:{error_kind}",
    tags=["omni-post", "platform:…", "account:…", "hour:{0–23}",
          "weekday:{0–6}", "engine:pw|mp", "outcome:success|failure",
          "asset:video|image|…", "error:…"],
    content="[ISO 时间] account acc-7 published to douyin via pw: …",
)
```

后续 idea-research / fin-pulse 可以按标签聚合 "某账号历史上 21 点推文
的成功率" 进而驱动推荐，不再需要 omni-post 重复持久化。

### 6.4 已知限制

- 不处理 "跨平台账号实名切换" — 平台安全策略，不应由第三方工具代劳。
- 单 host 单 Chromium；需要更大并发请多实例部署。
- MDRM 写入优先级 `LONG_TERM`，成功 0.55 / 失败 0.7 importance；
  宿主可在 Memory 管理器里按需调整 TTL。

---

## 7 · 兼容性

- 零 `openakita_plugin_sdk.contrib` import。
- 零 `/api/plugins/_sdk/*` host-mount 引用。
- 零 `from _shared import ...`。
- `requires.sdk` 锚定 `>=0.7.0,<0.8.0`，与所有现役一线插件一致。
- UI Kit (`ui/dist/_assets/*`) 与 `avatar-studio` 1:1 复用，保持同款
  主题令牌、暗色模式、i18n 接口。
