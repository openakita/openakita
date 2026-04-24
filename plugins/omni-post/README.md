# Omni Post (`omni-post`)

> 全媒发布 · 一次创作，多平台多账号落地。一线插件、零 SDK contrib 依赖、
> 零 host UI 资源挂载。

| | |
|---|---|
| **版本** | 0.1.0 (Sprint 1 / S1 骨架) |
| **SDK 范围** | `>=0.7.0,<0.8.0` |
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

omni-post 跟随 OpenAkita 主仓发布。额外系统级依赖：

- **Playwright 浏览器二进制**（自研引擎必需）：
  ```bash
  python -m playwright install chromium
  ```
- **ffmpeg / ffprobe**（强烈建议，用于素材 probe 与缩略图，缺失则优雅降级）：
  ```bash
  # Windows (chocolatey)
  choco install ffmpeg
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
| `channel.push` | 任务状态 SSE 推送 |
| `brain.access` | LLM 差异化文案与定时推荐 |

---

## 5 · 目录结构（当前 S1）

```
plugins/omni-post/
├── plugin.json                       # manifest，14 tool + 12 permission
├── plugin.py                         # PluginBase 入口 + 路由 + 工具
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
├── requirements.txt                  # 仅 cryptography（Fernet），Playwright 复用 host
└── ui/dist/
    ├── index.html                    # React 18 + Babel 单文件 UI（6 Tab）
    └── _assets/                      # 与 avatar-studio 1:1 的 UI Kit
```

S2–S4 阶段会追加 `omni_post_scheduler.py` / `omni_post_engine_mp.py` /
`omni_post_mdrm.py` / 更多选择器与 Tab。

---

## 6 · 已知限制

- S1 仅开放 3 个平台（抖音 / 小红书 / B 站）的选择器，其余 7 个平台将于
  S2 补齐，但 `PlatformAdapter` 抽象已就位，追加一个平台只需新增一个
  `omni_post_selectors/*.json` 即可在 `GenericJsonAdapter` 下跑通。
- Cookie 池在 S1 只做加密存储与手动导入；懒加载健康探针、自动 refresh
  与失败重投在 S2 落地。
- 定时发布、时区错峰、矩阵模式在 S3 落地；MultiPost Compat 引擎与
  MDRM 记忆写入在 S4 落地。
- 不处理 "跨平台帐号实名切换"，这是平台安全策略，不应由第三方工具代劳。

---

## 7 · 兼容性

- 零 `openakita_plugin_sdk.contrib` import。
- 零 `/api/plugins/_sdk/*` host-mount 引用。
- 零 `from _shared import ...`。
- `requires.sdk` 锚定 `>=0.7.0,<0.8.0`，与所有现役一线插件一致。
- UI Kit (`ui/dist/_assets/*`) 与 `avatar-studio` 1:1 复用，保持同款
  主题令牌、暗色模式、i18n 接口。
