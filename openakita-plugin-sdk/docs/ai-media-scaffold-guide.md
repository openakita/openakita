# AI 媒体插件脚手架使用指南（给新 Agent 会话）

> **读者**：在 OpenAkita 仓库里要新建 / 改动 AI 媒体类（视频、图像、音频、TTS、配音、海报、字幕、翻译…）插件的开发 agent。
> **目的**：让你**第一时间知道用什么、改哪里、不能改什么**，避免踩"重复造轮子 / 改错位置 / 影响打包 CI / 老插件回归"。
>
> 配套阅读：
> - `openakita-plugin-sdk/docs/contrib.md` — 后端 contrib API 全表
> - `openakita-plugin-sdk/docs/plugin-ui.md` — 老的 Plugin UI 协议（仍生效）
> - `openakita-plugin-sdk/docs/getting-started.md` — 基础 PluginBase / PluginAPI

---

## 0. TL;DR — 30 秒版

- AI 媒体类插件 **不要从零写**：`from openakita_plugin_sdk.contrib import ...` 拿任务表/HTTP/错误教练/成本预估/质量门禁/FFmpeg 等
- 前端 **不要装框架**：`<script src="/api/plugins/_sdk/ui-kit/<x>.js">` 直接用 7 个零依赖小组件
- 插件目录固定 10 件套：`plugin.json` / `plugin.py` / `<engine>.py` / `task_manager.py` / `ui/dist/index.html` / `SKILL.md` / `docs/HOWTO.md` / `README.md` / `tests/conftest.py` / `tests/test_<plugin>_engine.py`
- 跨插件复用源码用 `importlib.util.spec_from_file_location` 起**唯一别名**，**禁止**直接 `from providers import ...`
- 改 UI 先判断："只我用 → 改插件；多个人用 → 改脚手架"
- 老插件 `seedance-video` / `tongyi-image` 不要动，新插件强制走脚手架

---

## 1. 仓库与脚手架地图

```
D:\OpenAkita\
├── openakita-plugin-sdk\src\openakita_plugin_sdk\
│   ├── core.py / api.py / base.py     ← 基础 SDK (PluginBase, PluginAPI) — 最小 / 不动
│   ├── contrib\                       ← 【AI 媒体后端脚手架】13 个模块
│   │   ├── __init__.py                ← 公共导出，import 这里就够
│   │   ├── task_manager.py            ← BaseTaskManager (SQLite tasks/assets/config)
│   │   ├── vendor_client.py           ← BaseVendorClient (httpx 重试/timeout/cancel)
│   │   ├── errors.py                  ← ErrorCoach (3 段式异常翻译)
│   │   ├── cost_estimator.py          ← CostEstimator (low/high/sample/confidence)
│   │   ├── intent_verifier.py         ← IntentVerifier (verify-not-guess)
│   │   ├── prompt_optimizer.py        ← PromptOptimizer
│   │   ├── quality_gates.py           ← QualityGates (G1/G2/G3 纯函数)
│   │   ├── render_pipeline.py         ← build_render_pipeline (FFmpeg 安全构造)
│   │   ├── env_any_loader.py          ← load_env_any (SKILL.md frontmatter)
│   │   ├── slideshow_risk.py          ← evaluate_slideshow_risk
│   │   ├── delivery_promise.py        ← validate_cuts
│   │   ├── provider_score.py          ← score_providers
│   │   ├── storage_stats.py           ← collect_storage_stats (异步分页)
│   │   └── ui_events.py               ← UIEventEmitter (修复命名空间)
│   └── web\ui-kit\                    ← 【前端脚手架】host 服务于 /api/plugins/_sdk/ui-kit/
│       ├── styles.css                 ← 主题感知，统一视觉
│       ├── event-helpers.js           ← OpenAkita.onEvent (自动剥前缀)
│       ├── task-panel.js              ← TaskPanel 组件
│       ├── cost-preview.js            ← CostPreview.{render,mount}
│       ├── error-coach.js             ← ErrorCoach.{render,mount}
│       ├── onboard-wizard.js          ← OnboardWizard.askOnce
│       └── first-success-celebrate.js ← FirstSuccessCelebrate.maybeFire
└── plugins\
    ├── seedance-video\                ← 老插件 (生产，不动)
    ├── tongyi-image\                  ← 老插件 (生产，不动)
    ├── highlight-cutter\              ← P1 新插件
    ├── image-edit\
    ├── storyboard\
    ├── avatar-speaker\
    ├── subtitle-maker\                ← P2 新插件
    ├── tts-studio\
    ├── poster-maker\
    └── video-translator\
```

---

## 2. 后端 13 个 contrib 模块——能力清单

按"用来干嘛"分四类。**不要在插件里复写这些功能**，永远 import。

### 2.1 业务骨架

| 模块 | 你用它做什么 | 关键 API |
|---|---|---|
| `task_manager.BaseTaskManager` | 任务持久化 + cancel | `extra_task_columns()` / `default_config()` / `create_task()` / `update_task()` / `get_task()` / `list_tasks()` / `cancel_task()` / `get_config()` / `set_config()` |
| `vendor_client.BaseVendorClient` | 调外部 API（OpenAI / DashScope / Ark…）的安全 HTTP 基类 | `auth_headers()` / `cancel_task()` 子类 override；自带 429/5xx 重试分类 + 强制 timeout |
| `ui_events.UIEventEmitter` | 给前端推事件 | `emit(event_type, payload)` 自动加 `plugin:<id>:` 前缀；前端 `OpenAkita.onEvent` 自动剥 |

### 2.2 小白用户 UX（"清晰指导、异常说明明了"）

| 模块 | 你用它做什么 | 关键 API |
|---|---|---|
| `errors.ErrorCoach` | 把异常 / HTTP 状态码 → 「为什么 / 怎么办 / 提示」3 段式 | `coach.render(exc, raw_message=, status=)` → `RenderedError.to_dict()` |
| `cost_estimator.CostEstimator` | 费用预估 + retry 余量 + "≈ 1 块奶茶钱"翻译 | `estimator.add_line(...)` / `estimator.preview()` → `{low, high, sample_cost, confidence}` |
| `intent_verifier.IntentVerifier` | LLM 烧钱前先回放意图 + 提澄清问题 | `verifier.verify(prompt)` → `IntentSummary` (含 `clarifying_questions`) |
| `prompt_optimizer.PromptOptimizer` | 三档 prompt 优化（vendor 无关） | `optimizer.optimize(prompt, level=...)` |

### 2.3 AI 媒体专用底层

| 模块 | 你用它做什么 | 关键 API |
|---|---|---|
| `render_pipeline.build_render_pipeline` | **拼 FFmpeg 命令**，固化 yuv420p / 24fps / libx264 / 30ms 淡入淡出 / PTS 校正 / loudness norm | `pipeline = build_render_pipeline(...)` → `pipeline.run()` (内部带 `timeout` + `shutil.which()`) |
| `slideshow_risk.evaluate_slideshow_risk` | 6 维启发式：输出会不会沦为静态图集 | `evaluate_slideshow_risk(scenes)` → `SlideshowRisk` |
| `delivery_promise.validate_cuts` | 检查"实际剪掉的运动量 ≥ 承诺值" | `validate_cuts(cuts, promise=...)` |
| `provider_score.score_providers` | 7 维加权选 vendor | `score_providers(candidates, weights=...)` |

### 2.4 开发体验 & 自检

| 模块 | 你用它做什么 | 关键 API |
|---|---|---|
| `quality_gates.QualityGates` | G1 输入 / G2 输出 / G3 错误可读性纯函数；CI + SKILL.md 双轨 | `QualityGates.check_input_integrity(body, required=, non_empty_strings=)` → `GateResult.blocking` |
| `env_any_loader.load_env_any` | 解析 `SKILL.md` frontmatter `env_any: [...]` | `load_env_any(skill_md_text)` |
| `storage_stats.collect_storage_stats` | 大目录扫盘不卡 UI（asyncio.to_thread + 分页） | `await collect_storage_stats(roots, max_files=)` |

---

## 3. 前端 7 个 UI Kit 组件

host 自动挂在 `/api/plugins/_sdk/ui-kit/` 下，**插件 HTML 直接 `<script src=...>`**，不需要打包工具、不需要 npm。

| 文件 | 全局对象 / 类 | 典型用法 |
|---|---|---|
| `styles.css` | （css）`.oa-card` / `.oa-btn` / `.oa-input` / `.oa-textarea` / `.oa-label` / `.oa-row` / `.oa-grid.cols-2` / `.oa-pill` | 用脚手架 class，不要自己写颜色 |
| `event-helpers.js` | `OpenAkita.onEvent(eventType, cb)` | 监听后端 `UIEventEmitter.emit('task_updated', ...)` |
| `task-panel.js` | `new TaskPanel({ root, apiBase, tasksPath, cancelPath })` | 任务列表 + 取消按钮 + 状态徽章 |
| `cost-preview.js` | `CostPreview.mount(selector, preview)` | 显示成本卡（含人话翻译） |
| `error-coach.js` | `ErrorCoach.mount(selector, renderedError)` | 显示 3 段式错误卡 |
| `onboard-wizard.js` | `OnboardWizard.askOnce({ storageKey, question, options })` | 首次打开做"用户画像"小问答 |
| `first-success-celebrate.js` | `FirstSuccessCelebrate.maybeFire({ storageKey, title, recommendations, onRecommend })` | 第一次成功后弹庆祝 + 推荐相关插件 |

CSS 主题：自动跟随 `[data-theme="dark"]`，颜色全用 `var(--oa-*)`。

---

## 4. 新建插件——10 件套模板

新建 `plugins/<your-plugin>/`，照下面 10 个文件来：

```
plugins/<your-plugin>/
├── plugin.json                       ← 元数据 (id/name/version/permissions/...)
├── plugin.py                         ← 主入口 (PluginBase 子类)
├── <your_engine>.py                  ← 业务核心，纯函数为主，便于测试
├── task_manager.py                   ← class XxxTaskManager(BaseTaskManager)
├── ui\dist\index.html                ← 单文件 HTML，引 ui-kit
├── SKILL.md                          ← 给 host agent 看：what/when/quality gates
├── docs\HOWTO.md                     ← 给其他 agent 看：API 调用示例
├── README.md                         ← 给人看：用法 + 测试
└── tests\
    ├── conftest.py                   ← 隔离 sys.path / sys.modules（必须！）
    └── test_<plugin>_engine.py       ← 文件名必须**全仓库唯一**
```

### 4.1 `plugin.json` 模板（关键字段）

```json
{
  "id": "your-plugin",
  "name": "中文名",
  "name_i18n": { "en": "English Name" },
  "version": "1.0.0",
  "type": "python",
  "entry": "plugin.py",
  "description": "一句话描述",
  "category": "creative",
  "tags": ["tag1", "tag2"],
  "depends": ["other-plugin"],          // 跨插件依赖才写
  "permissions": [
    "tools.register", "routes.register", "hooks.basic",
    "config.read", "config.write", "data.own", "brain.access"
  ],
  "requires": {
    "openakita": ">=1.27.0",
    "plugin_api": "~2",
    "plugin_ui_api": "~1",
    "sdk": ">=0.4.0"
  },
  "provides": { "tools": ["..."], "routes": true },
  "ui": {
    "entry": "ui/dist/index.html",
    "title": "中文名",
    "sidebar_group": "apps",
    "permissions": ["upload", "download", "notifications", "theme"]
  }
}
```

### 4.2 `plugin.py` 主入口骨架

```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openakita.plugins.api import PluginAPI, PluginBase
from openakita_plugin_sdk.contrib import (
    ErrorCoach, QualityGates, TaskStatus, UIEventEmitter,
)

from your_engine import do_the_thing
from task_manager import YourTaskManager


class CreateBody(BaseModel):
    prompt: str = Field(..., min_length=1)


class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        self._api = api
        self._tm = YourTaskManager(api.get_data_dir() / "your.db")
        self._coach = ErrorCoach()
        self._events = UIEventEmitter(api)
        self._workers: dict[str, asyncio.Task] = {}

        router = APIRouter()
        self._register_routes(router)
        api.register_api_routes(router)

        api.register_tools([...], self._handle_tool_call)

    def on_unload(self) -> None:
        for t in list(self._workers.values()):
            try: t.cancel()
            except Exception: pass

    def _register_routes(self, router: APIRouter) -> None:
        @router.post("/tasks")
        async def create_task(body: CreateBody):
            gate = QualityGates.check_input_integrity(
                body.model_dump(), required=["prompt"], non_empty_strings=["prompt"],
            )
            if gate.blocking:
                rendered = self._coach.render(ValueError(gate.message), raw_message=gate.message)
                raise HTTPException(status_code=400, detail=rendered.to_dict())
            tid = await self._create(body)
            return {"task_id": tid, "status": "queued"}
        # ... GET /tasks, /tasks/{id}, /tasks/{id}/cancel ...
```

### 4.3 `task_manager.py` 子类 30 行搞定

```python
from openakita_plugin_sdk.contrib import BaseTaskManager

class YourTaskManager(BaseTaskManager):
    def extra_task_columns(self):
        return [("output_path", "TEXT"), ("source_path", "TEXT NOT NULL DEFAULT ''")]

    def default_config(self):
        return {"preferred_provider": "auto"}
```

### 4.4 UI HTML 必引 ui-kit

```html
<link rel="stylesheet" href="/api/plugins/_sdk/ui-kit/styles.css">
<script src="/api/plugins/_sdk/bootstrap.js"></script>
<script src="/api/plugins/_sdk/ui-kit/event-helpers.js"></script>
<script src="/api/plugins/_sdk/ui-kit/error-coach.js"></script>
<script src="/api/plugins/_sdk/ui-kit/task-panel.js"></script>
<script src="/api/plugins/_sdk/ui-kit/onboard-wizard.js"></script>
<script src="/api/plugins/_sdk/ui-kit/first-success-celebrate.js"></script>
```

### 4.5 `SKILL.md` 必带 frontmatter + Quality Gates 表

```md
---
name: your-plugin
description: 一句话给 host agent 看的功能描述
env_any: [DASHSCOPE_API_KEY, OPENAI_API_KEY]   # 任一存在即可
---

# 是什么 / 何时用 / 工具 / 流水线 / Quality Gates / 已知坑
```

### 4.6 `tests/conftest.py` —— **不能漏，否则跨插件测试相互污染**

```python
import sys
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

# 清掉兄弟插件可能已经缓存的同名模块（避免 sys.modules 污染）
for _m in ("providers", "highlight_engine", "subtitle_engine", "studio_engine",
          "poster_engine", "translator_engine", "templates", "task_manager"):
    sys.modules.pop(_m, None)
```

### 4.7 测试文件名必须**全仓库唯一**

❌ `tests/test_engine.py`（多个插件重名 → pytest 收集失败）
✅ `tests/test_<plugin>_engine.py`，例如 `test_highlight_engine.py`

---

## 5. 跨插件复用代码——**必须**用唯一别名 importlib

某个 engine 想复用兄弟插件代码（如 `subtitle-maker` 用 `highlight-cutter` 的 ASR），**禁止**直接 `sys.path.insert + from foo import bar`，因为两个插件可能有同名 `providers.py` / `engine.py`，会被 `sys.modules` 缓存死。

### ✅ 正确写法（在 4 个跨插件 engine 里已落地）

```python
import importlib.util
import sys
from pathlib import Path


def _load_sibling(plugin_dir_name: str, module_name: str, alias: str):
    src = Path(__file__).resolve().parent.parent / plugin_dir_name / f"{module_name}.py"
    if alias in sys.modules:
        return sys.modules[alias]
    spec = importlib.util.spec_from_file_location(alias, src)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {src}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[alias] = mod
    spec.loader.exec_module(mod)
    return mod


_hc = _load_sibling("highlight-cutter", "highlight_engine", "_oa_hc_engine")
TranscriptChunk = _hc.TranscriptChunk
whisper_cpp_transcribe = _hc.whisper_cpp_transcribe
```

**别名格式约定**：`_oa_<short>_<module>`，例：
- `_oa_hc_engine` → highlight-cutter / highlight_engine
- `_oa_sm_engine` → subtitle-maker / subtitle_engine
- `_oa_ts_engine` → tts-studio / studio_engine
- `_oa_avatar_providers` → avatar-speaker / providers
- `_oa_image_providers` → image-edit / providers

---

## 6. UI 修改决策树（最常见的问题）

> 看完这棵树再决定改哪：

```
你要改的 UI 内容是 …
├─ 只跟 1 个插件的具体业务/文案有关？
│   → 改 plugins/<id>/ui/dist/index.html
├─ 多个插件都需要 / 都该有的能力（按钮样式、任务卡片布局、错误卡渲染、成本预览、引导问答…）？
│   → 改 openakita-plugin-sdk/.../web/ui-kit/<x>.{js,css}
└─ 介于中间，先在 1 个插件里跑通，将来可能其它人也想要？
    → 先改插件 → 验证 OK → 再"上提"到脚手架（refactor up）
```

### 改动类型 → 落点速查

| 改什么 | 改哪 |
|---|---|
| 某插件的标题、说明、占位符、字段名 | **插件** index.html |
| 某插件特有控件 / 业务逻辑（画 mask、模板选择…） | **插件** index.html |
| 颜色 / 字体 / 间距 / 圆角等基础视觉 | **脚手架** styles.css |
| 通用 `.oa-btn` / `.oa-input` / `.oa-card` 外观 | **脚手架** styles.css |
| 任务列表卡片布局 / 状态徽章 | **脚手架** task-panel.js |
| 错误卡 3 段式排版 | **脚手架** error-coach.js |
| 成本预览卡格式 | **脚手架** cost-preview.js |
| 引导问答 / 庆祝弹窗 | **脚手架** onboard-wizard.js / first-success-celebrate.js |
| 事件订阅机制 | **脚手架** event-helpers.js |

**口诀**：**带 oa- 前缀的都是脚手架的；没有的都是插件自己的**。

### 流程

**改插件**：
1. 改 `plugins/<id>/ui/dist/index.html`
2. 插件 reload 即生效（无需重打 wheel）
3. 风险只限本插件

**改脚手架**：
1. 改 `web/ui-kit/<x>.{js,css}`
2. host dev server 自动重载（路由是动态的）
3. 影响**所有引用它的插件** → 必须在至少 2 个插件里走一遍 UI 验证
4. 跑 `pytest plugins/ openakita-plugin-sdk/tests` 全量
5. **不要**同时改插件里的 `<script src=...>` 路径——脚手架地址是稳定契约

**上提（refactor up）**：
1. 把组件代码挪到 `web/ui-kit/<new-name>.js`，按 `window.<X> = { mount, render }` 模式封装
2. 各插件 `<script src="/api/plugins/_sdk/ui-kit/<new-name>.js">`
3. 删插件里的本地副本
4. 跑全量回归

---

## 7. 后端能力修改决策树

```
你要改的能力是 …
├─ 只跟 1 个插件的业务有关（如 image-edit 的 mask 处理）？
│   → 改 plugins/<id>/<engine>.py
├─ 多个 AI 媒体插件都会用（如 FFmpeg 加新滤镜、错误模式新增、provider 评分维度调整）？
│   → 改 openakita-plugin-sdk/.../contrib/<x>.py
└─ host API 想加方法（如 PluginAPI 加新方法）？
    → ⚠ 慎重！老插件可能受影响。先看是否能在 contrib 里"包一层"绕过
```

**SDK contrib 修改清单**（每次改完都要做）：
1. `ruff check openakita-plugin-sdk/src` → All checks passed
2. `pytest openakita-plugin-sdk/tests` → 全过
3. `pytest plugins/` → 全过（128 条基线）
4. `python -m build --wheel openakita-plugin-sdk/` → wheel 能构建
5. 至少在 2 个引用了改动模块的插件里跑一遍真实流程

---

## 8. 红线 / 反模式（**不要做**）

| ❌ 反模式 | 为什么 | ✅ 正确做法 |
|---|---|---|
| 在脚手架里加只服务于 1 个插件的逻辑 | 脚手架变重，影响其它插件 | 插件本地解决，确实通用再上提 |
| N 个插件复制粘贴同一段代码 | 改 1 处忘改 N-1 处 | 抽到 contrib 或 ui-kit |
| 直接改 `seedance-video` / `tongyi-image` | 已在生产用，回归风险大 | 新功能放新插件；老插件除非用户明确说，否则不动 |
| 跨插件 `from providers import ...` | sys.modules 缓存导致 import 错插件的代码 | `_load_sibling()` 唯一别名 |
| `tests/test_engine.py` 重名 | pytest 收集失败 | `test_<plugin>_engine.py` |
| 漏 `tests/conftest.py` | 跨测试用例 sys.path 污染 | 必须按模板带上 |
| `subprocess.run(...)` 不带 `timeout` | 卡死 | 一律 `timeout=`，FFmpeg 用 `RenderPipeline` |
| `httpx` / `aiosqlite` 在模块顶层 import | 启动变慢、可选依赖变必选 | contrib 里都是**方法内 lazy import**，跟着学 |
| 改 `pyproject.toml` 的 `testpaths` | 现在 `["tests"]`，CI 不收 plugins/ tests | 不要动；插件测试是 opt-in |
| 改 host 的 `broadcast_ui_event` 签名 | 破坏老插件 | 在 SDK 的 `UIEventEmitter` 里加封装 |
| 在 `plugin.py` 里直接拼 SQL | 重复 BaseTaskManager 的工作 | `extra_task_columns()` 加列即可 |
| 错误直接 `raise HTTPException(500, str(e))` | 小白看不懂 | `coach.render(e)` → `to_dict()` 当 detail |

---

## 9. 不会被影响的边界（**不能动**）

| 区域 | 原因 |
|---|---|
| `pyproject.toml` 的 `[tool.pytest.ini_options]` | 改 testpaths 会让 plugins/ tests 被当成主测试，CI 时长爆炸 |
| `pyproject.toml` 的 `[tool.hatch.build.targets.wheel]` | 不要把 `plugins/` 加进 wheel，那是 runtime 加载的 |
| `apps/setup-center/src-tauri/` | Tauri 打包不应该感知 plugins/ |
| `.github/workflows/ci.yml` | `plugin_sdk` 这个 job 是 SDK lint+wheel，已自动覆盖 contrib/ui-kit；不要新增 plugin 专属 job 除非确实需要 |
| `src/openakita/main.py` | 是 `openakita = "openakita.main:app"` 的入口，CLI 命令依赖它 |
| `plugins/seedance-video/` `plugins/tongyi-image/` | 老插件，除非用户明确要求，否则只读 |

---

## 10. 验证清单（每次改完跑一遍）

### 最小回归（改插件）
```powershell
$env:PYTHONPATH = "D:\OpenAkita\openakita-plugin-sdk\src;D:\OpenAkita\src"
py -3.11 -m pytest plugins/<your-plugin>/tests --no-header -q
```

### 改了 contrib 或 ui-kit
```powershell
ruff check openakita-plugin-sdk/src
$env:PYTHONPATH = "D:\OpenAkita\openakita-plugin-sdk\src;D:\OpenAkita\src"
py -3.11 -m pytest openakita-plugin-sdk/tests plugins/ --no-header -q
# 期望: 128 passed (70 SDK + 58 plugins)
```

### 担心打包/CI 被破坏（大改时）
```powershell
py -3.11 -m build --wheel openakita-plugin-sdk/ --outdir _tmp_dist_sdk/
py -3.11 -m pip install --force-reinstall _tmp_dist_sdk/openakita_plugin_sdk-*.whl
py -3.11 -c "from openakita_plugin_sdk.contrib import BaseTaskManager, ErrorCoach; print('OK')"
Remove-Item -Recurse -Force _tmp_dist_sdk
```

---

## 11. 推荐工作流（新 agent 会话开局 5 步）

1. **读这份 + `contrib.md`** — 知道有什么、能用什么
2. **看 1-2 个最像的现成插件** — `highlight-cutter`（视频）/ `image-edit`（图像 + 多 provider）/ `tts-studio`（音频 + 跨插件复用）
3. **画 10 件套的清单** — `plugin.json` / engine / task_manager / plugin / UI / SKILL / HOWTO / README / conftest / test
4. **先把 engine 纯函数 + 单测写完** — 不依赖 host，跑 pytest 飞快
5. **再串 plugin.py 的路由 + UI** — 这时候才需要 PluginAPI 和 ui-kit

---

## 12. 关键参考代码位置（速查）

| 想看 | 文件 |
|---|---|
| BaseTaskManager 子类完整范例 | `plugins/highlight-cutter/task_manager.py` |
| 多 provider 路由 + fallback 范例 | `plugins/image-edit/providers.py` |
| LLM 调用 + 5 级 fallback parser | `plugins/storyboard/storyboard_engine.py` |
| 跨插件 importlib 复用范例 | `plugins/video-translator/translator_engine.py`（最复杂，复用 3 个插件） |
| FFmpeg 命令构造范例 | `plugins/video-translator/translator_engine.py` 里的 `build_*_cmd` |
| UI 完整范例（含 wizard + celebrate） | `plugins/poster-maker/ui/dist/index.html` |
| SKILL.md 完整范例（含 Quality Gates 表） | `plugins/video-translator/SKILL.md` |

---

## 附录 A：现成插件清单

| 插件 | 干什么 | 关键依赖 |
|---|---|---|
| `seedance-video` | 文生视频 (Volcengine Ark Seedance) | DashScope/Ark API key |
| `tongyi-image` | 文生图 (Alibaba Tongyi) | DashScope API key |
| `highlight-cutter` | 长视频 → 精彩片段 | whisper.cpp + ffmpeg (本地) |
| `image-edit` | mask-based 图像编辑 | OpenAI gpt-image-1 / DashScope wanx |
| `storyboard` | 脚本 → 分镜表 | host LLM brain |
| `avatar-speaker` | 单段 TTS + 数字人占位 | Edge TTS / DashScope CosyVoice / OpenAI TTS |
| `subtitle-maker` | 视频 → SRT/VTT，可烧字幕 | whisper.cpp + ffmpeg；复用 highlight-cutter |
| `tts-studio` | 多角色对话稿 → 合成长音频 | 复用 avatar-speaker |
| `poster-maker` | 模板 + 文案 + 配图 → PNG 海报 | Pillow；可选复用 image-edit AI 润色 |
| `video-translator` | 视频 → 翻译字幕 + 新配音 | 复用 highlight-cutter + subtitle-maker + tts-studio + LLM |

## 附录 B：基线数据

- SDK contrib 模块：13 个
- UI Kit 组件：7 个
- 新插件：8 个
- 测试基线：**128 passed**（70 SDK + 58 plugins，Python 3.11）
- ruff lint：**All checks passed**
- SDK wheel：可构建，含 ui-kit 静态资源
- CI 影响：0（`testpaths=["tests"]` 不收 plugins/）
- Tauri 打包：0（plugins/ 不入 wheel）
- openakita-cli：0（`src/openakita/main.py` 未动）
