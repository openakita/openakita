# Sprint 18 — Cleanup & Migration Assessments

> Sprint 18 收尾 4 件评估 + 文档收尾。本文档汇总 A1+ tongyi / A1+ seedance / B8
> prompt_optimizer / SkillManifest loader 四项迁移评估的结论与建议；具体实施延后到
> 各插件下一次例行加固 PR 中。
>
> **决策原则**：测试已经满覆盖（tongyi-image 107 / seedance-video 58 / SDK 367），任
> 何机械替换都需要先满足 "回归测试零变化" 这条铁律。

---

## A1+ tongyi-image schema 迁移评估

**目标**：把 `plugins/tongyi-image/tongyi_task_manager.py` 与 `tongyi_dashscope_client.py`
分别迁到 SDK 的 `BaseTaskManager` 与 `BaseVendorClient` 子类化版本。

### 现状

| 维度 | tongyi-image 现状 | SDK 提供 |
|------|-------------------|----------|
| 任务表 | 自管 SQL，单表 `tasks` (264 行) | `BaseTaskManager` + `extra_task_columns()` |
| 配置表 | `_seed_config` + `get_config / set_config` | `BaseTaskManager.get_config / set_config` |
| HTTP 客户端 | `httpx.AsyncClient` 直接持有，自定义 retry | `BaseVendorClient` 含 retry / moderation / VendorError 三段 |
| 错误模型 | `DashScopeError(code, message, status_code)` | `VendorError(status, body, retryable, kind)` |
| 测试覆盖 | 107 passed | 含 BaseTaskManager / BaseVendorClient 测试 ≥ 40 |

### 可迁移项 ✅

1. **基础 CRUD** — `create_task / get_task / list_tasks / update_task / delete_task` 与
   `BaseTaskManager` 完全同语义。可改为子类化，把现有列声明在 `extra_task_columns()`：

   ```python
   class TongyiTaskManager(BaseTaskManager):
       def extra_task_columns(self):
           return [
               ("api_task_id", "TEXT"),
               ("prompt", "TEXT"),
               ("negative_prompt", "TEXT"),
               ("model", "TEXT"),
               ("mode", "TEXT"),
               ("image_urls", "TEXT"),                # JSON 编码字段
               ("local_image_paths", "TEXT"),         # JSON 编码字段
               ("usage_json", "TEXT NOT NULL DEFAULT '{}'"),
           ]
   ```

2. **配置层** — `DEFAULT_CONFIG` 直接挪到 `default_config()`。

3. **客户端 retry/timeout** — `BaseVendorClient` 已经覆盖 tongyi 现有的所有 retry 路径
   （含 5xx / 429 / 网络错误指数退避），可直接继承：

   ```python
   class DashScopeClient(BaseVendorClient):
       name = "dashscope"
   ```

### 障碍 / 不能直接套 ❗

1. **特殊端点路径** — DashScope 5 个端点（`EP_MULTIMODAL`、`EP_IMAGE_GEN`、
   `EP_BG_GEN`、`EP_OUTPAINT`、`EP_IMAGE_SYNTH`），每个都有自己的请求/响应 schema 与
   `X-DashScope-Async: enable` header。需要在 `_post / _get` 之上保留 vendor-specific
   方法层（5 个 `generate_*` 方法），不能直接复用 SDK 通用 `post_json`。

2. **错误码映射** — `DashScopeError(code='InvalidParameter', message='...', status_code=400)`
   是 tongyi 业务码，不是 HTTP 码。`VendorError.kind` 需要新增映射表
   （`InvalidParameter → "client"`、`Throttling → "rate_limit"` 等）。这是必要的工
   作但属于增量补丁。

3. **`X-DashScope-Async` 头** — 部分异步端点在 POST 时需要附加 header，`BaseVendorClient`
   现有签名 `post_json(path, body)` 不支持额外 header。需要给 SDK 加一个
   `extra_headers=` 参数，或在子类里直接调用底层 `httpx`。

4. **107 个测试需重新跑** — `tongyi-image/tests/test_tongyi_task_manager.py` 与
   `test_tongyi_dashscope_client.py` 直接 mock 的是当前类的内部方法（如 `_post`、
   `_seed_config`）。重构后这些测试需要重写为子类化版本。

### 收益评估

| 项目 | 收益 |
|------|------|
| 代码重复消除 | tongyi 的 264 行 task_manager → 约 80 行子类（节省 70%） |
| 新增能力 | 自动获得 SDK 后续的 `extra` 字段过滤、JSON 编解码统一、TaskRecord 协议 |
| 一致性 | 与 seedance / local-sd-flux / dub-it / shorts-batch 等 8 个新插件全部保持子类化模式 |

### 风险评估

| 风险 | 等级 | 说明 |
|------|------|------|
| 107 测试改动量 | **中** | 主要是 mock 路径迁移，逻辑不变 |
| `X-DashScope-Async` header 需 SDK 配合改 | **低** | 给 `BaseVendorClient.post_json` 加 `headers=` 参数即可，不破坏现有 6 个消费者 |
| 错误码翻译表维护负担 | **低** | DashScope 错误码列表稳定（≤ 20 条），一次性映射即可 |

### 建议

> **🟢 推荐迁移，但不在 Sprint 18 内执行。**
> 拆成下一个独立 PR：
>
> 1. 先给 SDK 的 `BaseVendorClient.post_json` / `get_json` 加 `headers=` 参数（向后兼容）。
> 2. 写 `DashScopeClient(BaseVendorClient)` 子类，把 5 个端点方法保留为 thin wrapper。
> 3. 写 `TongyiTaskManager(BaseTaskManager)` 子类。
> 4. 重写 mock 路径，跑 107 测试全绿。
>
> 工作量预估：1 个工程日（含测试改写）。可独立 review，不阻塞其他 sprint。

---

## A1+ seedance-video schema 迁移评估

**目标**：把 `plugins/seedance-video/task_manager.py` 与 `ark_client.py`
迁到 `BaseTaskManager` / `BaseVendorClient`。

### 现状

| 维度 | seedance-video 现状 | SDK 提供 |
|------|---------------------|----------|
| 任务表 | 自管 SQL，**双表** `tasks` + `assets` (366 行) | `BaseTaskManager` 仅支持单表（任务） |
| 配置表 | 12 个 key 默认配置 | `default_config()` |
| HTTP 客户端 | `httpx.AsyncClient` 直接持有，**128 行** | `BaseVendorClient` |
| 错误模型 | 直接抛 `httpx.HTTPStatusError` | `VendorError` 含 `retryable / kind` |
| 测试覆盖 | 58 passed (含 `test_task_manager.py`) | — |

### 可迁移项 ✅

1. **`tasks` 表** — 同 tongyi 评估，可子类化 `BaseTaskManager` + `extra_task_columns()`。

2. **`ark_client.py` 全文** — 只有 128 行，5 个方法（`create_task / get_task / list_tasks /
   delete_task / validate_key`）。所有调用都经 `httpx.AsyncClient` POST/GET，没有特殊
   header 或异步 header 依赖。**直接套 `BaseVendorClient`，零改造**：

   ```python
   class ArkClient(BaseVendorClient):
       name = "ark"

       async def create_task(self, model, content, **kwargs):
           body = {"model": model, "content": content, **kwargs}
           return await self.post_json("/contents/generations/tasks", body)
   ```

### 障碍 / 不能直接套 ❗

1. **`assets` 表** — seedance 有第二张 `assets` 表（128 行 SQL + CRUD），用于跟踪用户
   上传的图片 / 视频素材。`BaseTaskManager` 是**单任务表**模型，没有 "副表" 抽象。

   **解决方案 A**：保留 `assets` 表自管，仅迁 `tasks` 表。`seedance_task_manager.py`
   从 366 行降到约 200 行（独立的 `assets` CRUD）。

   **解决方案 B**：给 SDK 加一个 `BaseAssetManager`（与 `BaseTaskManager` 同构，但
   外键到 `tasks.id`）。这是个**通用价值很大但需要新设计**的工作 — 可惜 SDK 现在的
   8 个插件里只有 seedance 用到 assets 表，**单点抽象不值得**。

   **结论**：选 A。

2. **`is_draft / draft_parent_id` 草稿父子关系** — seedance 任务可以是另一个任务的 "草
   稿"，`tasks` 表上有 `draft_parent_id` 自引用列。这层逻辑现在在 task_manager 之外
   的 `plugin.py` 里维护。迁移不影响。

3. **58 测试需要重新跑** — 同 tongyi。

### 收益评估

| 项目 | 收益 |
|------|------|
| 代码重复消除 | task_manager 366 → ~200 行；ark_client 128 → ~60 行 |
| 一致性 | 与 dub-it / local-sd-flux / shorts-batch / ppt-to-video 等保持子类化 |

### 建议

> **🟢 推荐迁移，工作量比 tongyi 更小，但仍不在 Sprint 18 内执行。**
> 拆成下一个独立 PR：
>
> 1. 写 `ArkClient(BaseVendorClient)` 子类，保留全部公开方法签名向后兼容。
> 2. 写 `SeedanceTaskManager(BaseTaskManager)` — 仅迁 `tasks` 表，`assets` 表保留
>    在 `seedance_asset_manager.py` 独立模块。
> 3. 跑 58 测试 + 长视频流水线 e2e 烟测。
>
> 工作量预估：0.5 个工程日。

---

## B8 prompt_optimizer 迁移评估

**目标**：决定 `plugins/tongyi-image/tongyi_prompt_optimizer.py` (810 行) 与
`plugins/seedance-video/prompt_optimizer.py` (298 行) 是否合并到 SDK
`contrib.prompt_optimizer.PromptOptimizer`。

### 现状

| 来源 | 行数 | 调用方 | 是否在用 |
|------|------|-------|----------|
| `plugins/tongyi-image/tongyi_prompt_optimizer.py` | 810 | `plugins/tongyi-image/plugin.py` | ✅ 在用 |
| `plugins/seedance-video/prompt_optimizer.py` | 298 | _无人引用_ | ❌ **孤儿文件** |
| `openakita-plugin-sdk/.../contrib/prompt_optimizer.py` | 159 | _SDK 自己_ | 🅿️ 等消费者 |

### 三者职责对比

|  | SDK `PromptOptimizer` | tongyi `optimize_prompt` | seedance `optimize_prompt` |
|---|----------------------|--------------------------|----------------------------|
| 输入 | `original_prompt + level + extra_context` | `user_prompt + model + size + style + level` | `user_prompt + mode + duration + ratio + asset_summary + level` |
| 系统 prompt | 可注入（generic） | 硬编码（万相专用，含公式 / 关键词 / 风格库） | 硬编码（Seedance 专用，含时间轴格式 / 镜头语言） |
| Level 集合 | `basic / professional / creative` | `light / professional / creative` | `light / professional / storyboard` |
| 返回 | `OptimizedPrompt(optimized, original, level, rationale)` | `str` | `str` |
| 静态资料 | 无 | **+ 600 行**「关键词库 / 模板 / 电商 prompt」（`generate_ecommerce_prompts`、`get_prompt_guide_data`） | + 200 行「7 种内置模板」 |

### 关键发现

1. **SDK 版本是从 seedance 抽出的通用版**，但 seedance 自己**已经不再用** SDK 版本，
   `plugin.py` 里没有 `prompt_optimizer` 任何 import — 文件孤立存在。

2. **tongyi 的 810 行里只有约 200 行是 LLM 调用**，剩下 600 行是 UI guide 数据
   （静态字典 / 关键词库 / 电商 prompt 生成器）。这部分**与 prompt 优化无关**，只是
   "顺便放在同一个文件里"。

3. SDK `PromptOptimizer` 的 `levels` 是 `(basic, professional, creative)`；两个插件
   都用了 `light` 而不是 `basic`。需要小幅命名校准。

### 可执行动作

#### A. seedance 孤儿文件 → 直接删除（零风险）

`plugins/seedance-video/prompt_optimizer.py` 没人调用、没有测试、SDK 已经有泛化版本。
**Sprint 18 内可立即删除**，作为 cleanup 的一部分。但 `models.py` / `long_video.py`
里 `optimize_prompt` 路径需先 grep 确认无引用。

> 检查结果：`grep "optimize_prompt|prompt_optimizer" plugins/seedance-video` → **零引用**。可安全删除。

> **⚠ 2026-04-21 修订**：上述 grep 结论是错误的。复核 `plugins/seedance-video/plugin.py`
> 第 39–46 行明确 `from prompt_optimizer import (ATMOSPHERE_KEYWORDS, CAMERA_KEYWORDS,`
> `MODE_FORMULAS, PROMPT_TEMPLATES, PromptOptimizeError, optimize_prompt)`，并在
> `/prompt-guide`、`/prompt-templates`、`/prompt-formulas`、`/prompt-optimize` 4 个
> REST 端点中实际使用。SDK 的 `PromptOptimizer` 类是另一套泛化 API（无 Seedance 静态
> 字典、签名不同），不能直接替换。已从 commit `f04787f9^` 还原 `prompt_optimizer.py`
> 原 291 行版本。结论：**本节 A 的"立即删除"动作不应执行**，未来 grep 类似结论前
> 务必再 `rg --pcre2 -nP "from\s+prompt_optimizer|import\s+prompt_optimizer"` 双重确认。

#### B. tongyi prompt_optimizer 拆分

把 810 行拆成两块：

* **`tongyi_prompt_guide.py`**（保留 600 行静态数据 + UI 助手） — 仍然在 plugin 内。
* **`tongyi_prompt_optimizer.py`**（保留约 200 行 LLM 调用部分） — 改为基于 SDK
  `PromptOptimizer` 的 thin wrapper：

  ```python
  from openakita_plugin_sdk.contrib import PromptOptimizer

  _OPT = PromptOptimizer(
      system_prompt=OPTIMIZE_SYSTEM_PROMPT,
      formatter=lambda **kw: OPTIMIZE_USER_TEMPLATE.format(**kw),
      levels=("light", "professional", "creative"),  # 保留现有 level 命名
  )

  async def optimize_prompt(brain, user_prompt, **kwargs):
      result = await _OPT.optimize(
          original_prompt=user_prompt,
          level=kwargs.get("level", "professional"),
          extra_context=kwargs,
          llm_call=_brain_to_llm_call(brain),
      )
      return result.optimized
  ```

  约 60 行。

工作量：约 0.5 个工程日 + 重新跑 tongyi 现有测试。

### 建议

> **🟢 立即执行 A（删除 seedance 孤儿）；🟡 推荐执行 B 但延后到下一次 tongyi 加固 PR。**
>
> 立即：
> - `git rm plugins/seedance-video/prompt_optimizer.py`
> - 跑 seedance 58 测试确认零回归
>
> 下次 PR：tongyi 拆分 + 接入 SDK。

---

## SkillManifest loader 接入主 repo 评估

**目标**：把 SDK 的 `openakita_plugin_sdk.skill_loader.load_skill()` 接到主 repo
`src/openakita/plugins/manager.py` 的 `_load_skill_plugin / _try_load_plugin_skill`
两处。

### 现状

`src/openakita/plugins/manager.py` 已经支持注入外部 skill_loader，调用约定是：

```python
skill_loader.load_skill(skill_path.parent, plugin_source=f"plugin:{manifest.id}")
# 或 fallback
skill_loader.load_from_directory(skill_path.parent)
```

SDK 的 `skill_loader` 模块对外暴露：

```python
def load_skill(path: str | Path) -> ParsedSkill: ...
```

### 关键不兼容 ❗

| 维度 | 主 repo 期望 | SDK 现状 |
|------|--------------|----------|
| 输入 | **目录** (`skill_path.parent`) + `plugin_source` 关键字 | 单个**文件路径**，无 `plugin_source` |
| 返回 | _副作用_ — 注册到 `skill_loader.registry` | 纯函数 — 返回 `ParsedSkill` |
| 注册表 | `skill_loader.registry` 字典属性 | _无注册表_ — 设计上是 stateless parser |
| `unload_skill` | 主 repo 期望存在 | _不存在_ |

> 结论：SDK 的 `skill_loader` 是**纯解析器**（parse SKILL.md frontmatter → ParsedSkill），
> 而主 repo 需要的是一个**带注册表的 manager**（load → register → unload → 检索）。
> 二者不是同一种抽象。

### 三种合理路径

1. **❌ 直接替换 host 的 skill_loader**：不可行，注册表语义缺失。

2. **🟡 在主 repo 写 adapter**：
   ```python
   class SDKSkillLoaderAdapter:
       def __init__(self):
           self.registry = {}

       def load_skill(self, dir_path, plugin_source=""):
           parsed = sdk_loader.load_skill(dir_path / "SKILL.md")
           self.registry[parsed.manifest.name] = parsed
           parsed.plugin_source = plugin_source

       def unload_skill(self, sid):
           self.registry.pop(sid, None)
   ```
   收益：主 repo 可以直接用 SDK 的 frontmatter 解析，少维护一份。
   成本：约 40 行 adapter + 该 adapter 自己的 5-10 测试。

3. **🟢 仅在 plugin SDK consumers 内部使用**：保持现状 — 主 repo 用自己的 host
   skill_loader，SDK 的 `load_skill` 仅服务于插件内部需要解析 SKILL.md 的场景
   （目前 0 个用户）。这是当前默认状态。

### 建议

> **🟡 不立即接入，但记录为 known gap。**
>
> 现在主 repo 的 host skill_loader 工作良好（已经被 8 个插件的 `provides.skill`
> 路径 + 独立 skill 类型双轨使用）。SDK 的 parser 设计上更克制（无注册表），如果以
> 后主 repo 想统一前端 UI 渲染（"哪些 skill 来自哪个插件 / 触发词是什么"），可以走
> path 2 的 adapter，但当前没有强需求。
>
> **行动**：在 `docs/skill-loading-architecture.md` 末尾追加一条 known gap 注释，指
> 向本评估。下一次有人想统一 skill UI 时再上 adapter。

---

## 文档收尾清单

* [x] 本文件 `docs/sprint18-cleanup-assessment.md`
* [x] `CHANGELOG.md` 追加 1.3.0 段落（Sprint 7-17 全部条目 + Sprint 18 评估）
* [x] 主 README 插件矩阵刷新（新增 6 个插件 + 既有 14 个汇总到 §Plugin System）
* [x] ~~删除 `plugins/seedance-video/prompt_optimizer.py`（孤儿，无引用）~~ — **2026-04-21 已撤销**：grep 结论错误，实为 `plugin.py` 重度依赖；已从 `f04787f9^` 还原。

---

## 总结表

| ID | 评估 | 结论 | 立即执行？ |
|----|------|------|-----------|
| A1+ tongyi | task_manager + dashscope_client → SDK 子类 | 🟢 推荐 | ❌ 下一次 PR |
| A1+ seedance | task_manager + ark_client → SDK 子类 | 🟢 推荐（更简单） | ❌ 下一次 PR |
| B8 seedance prompt_optimizer | ~~删除孤儿文件~~ → 误删,已还原 | 🔴 撤销 | ❌ 2026-04-21 还原 |
| B8 tongyi prompt_optimizer | 拆 600 行静态资料 / 200 行 LLM → 接 SDK | 🟡 推荐 | ❌ 下一次 PR |
| SkillManifest loader 接入 host | adapter 方案可行但当前无强需求 | 🟡 推迟 | ❌ 待消费者出现 |
| 文档收尾 | CHANGELOG + README + 本评估 | 🟢 立即 | ✅ 本次 |
