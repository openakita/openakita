# refs/ 抽点报告 — Sprint 10

> 生成时间：Sprint 10
> 数据源：`D:\OpenAkita_AI_Video\findings\*deep.md` + `_summary_to_plan.md`
> 目的：把 9 个参考项目（CutClaw / OpenMontage / video-use / AnyGen / CapCut / Canva / ComfyUI / n8n / OpenMontage skills）里**已经验证有用**的设计、坑、prompt 抽出来，
> 与 OpenAkita 当前实现状态映射，让后续 Sprint 11+ 的开发不需要再回头翻 1.4 GB 的 refs 目录。

---

## 0. 阅读指南

| 状态符号 | 含义 |
|----------|------|
| ✅ shipped | 已落地到 OpenAkita 代码 / SDK，并有 pytest 覆盖 |
| 🚧 partial | 一部分已落地，剩余在 backlog（标注待补的子项） |
| ⏳ planned | 计划在指定 Sprint 实现，未开工 |
| 📐 docs-only | 只作为文档/约定层落地，没有代码（如 SKILL.md 协议） |
| ⛔ rejected | 经评审决定**不**移植到 OpenAkita（附拒绝理由） |

每条记录的 ID（C / N / D / P / F）与 `_summary_to_plan.md` 一一对应，方便交叉引用。

---

## 1. 事实修正（C 类）

针对早期假设的勘误。`audit3_*` todos 已经在 Sprint 7-9 全部 patch 进代码或文档，本节仅做归档。

| ID  | 修正点                                                       | OpenAkita 状态 | 落地位置                                                                 |
|-----|--------------------------------------------------------------|----------------|--------------------------------------------------------------------------|
| C0.1 | AnyGen 是工作空间，不是视频生成产品 — 只借鉴 UX 理念       | ✅ shipped     | `docs/feature-priority-list.md` 已删除"vs Runway / Pika"对比；保留 verify / think-with-you / cost-education 三条 UX 原则 |
| C0.2 | `ToolResult.duration_seconds`（不是 `runtime_sec`）          | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/tool_result.py:74` 显式注释 "NOT runtime_sec — see C0.2" |
| C0.3 | `slideshow_risk` 是结构化启发式（基于 scene_plan dict）      | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/slideshow_risk.py` — 6 维 0-5 分制，零 ffmpeg 依赖 |
| C0.4 | `env_any` 只是 SKILL.md 约定，**无** Python loader           | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/skill_loader.py` 自建零依赖 loader；`env_any` 落入 `manifest.extra` |
| C0.5 | `TRIM_SHOT_MAX_SCENES_IN_HISTORY` 是隐式 `getattr` 默认 8    | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/agent_loop_config.py` 把它显式写成 dataclass 字段 + `__post_init__` 校验 |
| C0.6 | video-use commit `083e3cb` 不可见，self-eval 在 SKILL.md     | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/intent_verifier.py:171` `self_eval_loop()` 仿 video-use SKILL.md:84-93 |
| C0.7 | OpenMontage 内部已知 bug：`edit_decisions` 参数未实际使用    | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/slideshow_risk.py` 拒绝模拟该参数 — 干净接口 |
| C0.8 | reviewer-as-coach 是结构化反馈（不是单 prompt）              | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/errors.py` 模板渲染 + 文档已修正 |

---

## 2. 新坑（N 类）— 必须规避的反模式

| ID    | 出处                                  | 坑                                            | OpenAkita 规避状态 |
|-------|---------------------------------------|-----------------------------------------------|--------------------|
| N1.1  | CutClaw `core.py:1565-1637`           | 全 None 结果 → 静默丢弃 silent skip          | ✅ shipped — `contrib/parallel_executor.py:run_parallel` 显式落盘 `failed_tasks`，`ParallelResult.ok` 为 False；seedance-video `long_video.py` 已用此 API |
| N1.2  | CutClaw `litellm_client.py:76-128`    | `retry_if_exception_type(Exception)` 4xx 也重试 | ✅ shipped — `AgentLoopConfig.is_retryable_status()` + `DEFAULT_RETRY_STATUS_CODES = (408,425,429,500,502,503,504)`；4xx 永不重试 |
| N1.3  | video-use `transcribe.py:75-82`       | 整文件上传 + 30 分钟 timeout                 | ⏳ planned — Sprint 11（`transcribe-archive` 插件）必须分片 + 断点续传 |
| N1.4  | video-use `timeline_view.py:60,85`    | `subprocess.run` 无 timeout                   | 🚧 partial — `seedance-video/long_video.py` 的 ffmpeg 调用已包 `timeout=`；其他插件待审计（Sprint 18 cleanup） |
| N1.5  | OpenMontage 全文 grep                  | 用 `# NOTE` 而不是 TODO/FIXME                | 📐 docs-only — `docs/code-review-process.md` 已记录 NOTE > TODO 约定 |
| N1.6  | CapCut Help Center 12 篇 FAQ          | A/B 定价 + 跨平台权益不一致                   | 📐 docs-only — `docs/feature-priority-list.md` 注明"OpenAkita 定价 1 句话讲清，不做 A/B" |
| N1.7  | Canva onboarding (Supademo)            | 工具+模板并列 → 决策过载                      | 🚧 partial — `apps/setup-center/src/views/SkillManager.tsx` 已分类，但 OnboardWizard 首屏待精简（Sprint 13/N1 UX） |
| N1.8  | Canva onboarding                       | 首次成功导出后 0 庆祝 / 0 二次引导            | ⏳ planned — Sprint 13 加 confetti + "试试这 3 个相似插件" |
| N1.9  | CapCut "Thinking..."                   | 长时间无 ETA                                  | 🚧 partial — 任务面板 spinner 有进度文字，但缺"通常需要 X 秒"基线；待 Sprint 18 |
| N1.10 | CapCut "卡在 99%"                      | 卡住后无应用内自解释                          | ⏳ planned — Sprint 18 cleanup：进度条 30s 不动 → 自动展开"可能原因 + 一键诊断" |

---

## 3. 新设计（D 类）— 要照搬

| ID     | 设计                                                            | OpenAkita 状态 | 落地位置 |
|--------|-----------------------------------------------------------------|----------------|----------|
| D2.1   | OpenMontage `slideshow_risk` 6 维启发式 + verdict 阈值          | ✅ shipped     | `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/slideshow_risk.py` |
| D2.2   | OpenMontage `delivery_promise.validate_cuts` motion ratio       | ✅ shipped     | `contrib/delivery_promise.py` 已实现 `validate_cuts(cuts)`；待 Sprint 14/17 在 video-bg-remove / highlight-cutter 真正接入 |
| D2.3   | OpenMontage `source_media_review` G1 上传检查清单               | 🚧 partial     | `contrib/quality_gates.py` 有 `gate_g1_source_review`；highlight-cutter 待 Sprint 17 接入 |
| D2.4   | OpenMontage `tools/base_tool.ToolResult` 统一字段              | ✅ shipped     | `contrib/tool_result.py` (Sprint 8) — 含 `duration_seconds / warnings / metadata` |
| D2.5   | OpenMontage `lib/scoring.py` 7 维加权                           | ✅ shipped     | `contrib/provider_score.py` `score_providers()` — task_fit 0.30 / output_quality 0.20 / control 0.15 / reliability 0.15 / cost_efficiency 0.10 / latency 0.05 / continuity 0.05 |
| D2.6   | OpenMontage `cost_tracker` 预算预留 + 单笔审批 + CAP-WARN       | ✅ shipped     | `contrib/cost_tracker.py` — `reserve / reconcile / refund / commit` 已被 seedance-video 长视频流水线全链路验证 |
| D2.7   | CutClaw `parse_structure_proposal_output` 5 级 fallback parser  | ✅ shipped     | `contrib/llm_json_parser.py:parse_llm_json_object` — bgm-suggester / storyboard / seedance-video 三处复用 |
| D2.8   | CutClaw madmom 节拍剪辑参数表                                    | ⏳ planned     | Sprint 12 `bgm-mixer` 插件 |
| D2.9   | CutClaw `should_restart` context overflow handling              | ✅ shipped     | `AgentLoopConfig.is_context_overflow()` + `DEFAULT_CONTEXT_OVERFLOW_MARKERS` |
| D2.10  | AnyGen 双 AI 校验 + 字段级 verification badge                   | ✅ shipped     | `contrib/verification.py` — bgm-suggester、storyboard 已在 export payload 输出 `verification` 字段，前端可直接渲染绿/黄/红徽章 |
| D2.11  | AnyGen FAQ 错误三段式（cause + suggestion + 不甩锅）            | ✅ shipped     | `contrib/errors.py` `RenderedError` 字段含 `cause_category` + `actionable_suggestion`（不再叫 error_coach.py — 见 C0.8） |
| D2.12  | AnyGen Free Tier human-friendly 翻译                            | ✅ shipped     | `contrib/cost_estimator.py:to_human_units()` + `contrib/cost_translation.py` 已落地（"≈ 10 篇短文档 / 30 分钟转写 / ..."） |
| D2.13  | AnyGen "think with you" — clarify-stage / generate-stage 拆分   | ⏳ planned     | Sprint 13 P3 起，先在 storyboard 试点 clarify rich-card |
| D2.14  | CapCut 错误三段式 (`Why / What / Tip`)                          | 🚧 partial     | SDK `RenderedError` 字段已就绪；前端模板（`web/ui-kit/styles.css` 错误样式）待 Sprint 18 统一接入 |
| D2.15  | CapCut 模板社交化标签（不暴露技术参数）                         | ⏳ planned     | Sprint 18 模板系统 P2 |
| D2.16  | Canva 双轴分类（目标 × 工具）                                    | ✅ shipped     | `src/openakita/skills/categories.py` + `src/openakita/api/routes/skill_categories.py` 已实现，setup-center 默认按"目标"展示 |
| D2.17  | Canva Magic 命名（统一 AI 前缀）                                 | ⏳ planned     | Sprint 18 — 候选词："秋田" / "灵感" / "Spark"；待用户调研 |
| D2.18  | Canva 单问个性化（4 选项不阻塞 dashboard）                      | 🚧 partial     | `apps/setup-center/src/views/OrgEditorView.tsx` 已有 use-case 选项；首屏单问待 Sprint 13 |

---

## 4. 新 prompt / 协议（P 类）— 直接 copy

| ID  | 出处                                  | 落地状态                                                                 |
|-----|---------------------------------------|--------------------------------------------------------------------------|
| P3.1 | CutClaw `prompt.py:522` STRUCTURE_PROPOSAL | ✅ `contrib/data/prompts/structure_proposal.txt`，`prompts.load_prompt("structure_proposal")` 已被 Sprint 9 集成测试覆盖 |
| P3.2 | CutClaw `prompt.py:798` EDITOR_SYSTEM (THINK→ACT→OBSERVE)   | ✅ `contrib/data/prompts/agent_loop_system.txt`；BaseAgentLoop 的默认 system prompt |
| P3.3 | CutClaw `prompt.py:968` FINISH + `:970` USE_TOOL            | ✅ `contrib/data/prompts/agent_loop_finishers.txt`（结构化 dict，loader 自动解析）|
| P3.4 | OpenMontage `skills/meta/reviewer.md:9-92`                  | ✅ `contrib/data/prompts/reviewer_protocol.md`（schema → focus → playbook → success_criteria → 决策表 0/≥1 critical → Pass/Revise，2 轮上限）|
| P3.5 | OpenMontage `skills/meta/checkpoint-protocol.md:11-53,118-150` | ✅ `contrib/data/prompts/checkpoint_protocol.md`（manifest-driven checkpoint 触发表）|

每个 prompt 都通过 `openakita_plugin_sdk.contrib.load_prompt(name)` 统一加载，
`tests/integration/test_sprint9_sdk_reuse.py::test_prompts_each_p3_asset_loads_non_empty`
对全 5 条做了存在性 + 非空 + 类型断言 — 任意一条 prompt 文件被误删，CI 立即红。

---

## 5. 远期想法（F 类）— P3+ 备忘

这些**确认不**进入 Sprint 11-18，但记录在案，避免后续重新调研。

| ID  | 出处                                        | 想法                                  | 触发条件 |
|-----|---------------------------------------------|---------------------------------------|----------|
| F4.1 | ComfyUI `node_typing.py:17-72`              | IO 类型枚举 + 并集语法 + 子集判断      | 当 OpenAkita 需要可视化编排（≥ P3）|
| F4.2 | ComfyUI `comfy_execution/graph.py:237-265`  | DAG 拓扑 + 环检测                      | 同上 |
| F4.3 | n8n `Interfaces.ts:1961-1972`               | 同节点支持 execute/trigger/webhook/poll | 当用户提出"我要 cron 触发"|
| F4.4 | n8n `Webhook.node.ts` + `ScheduleTrigger.node.ts` | webhook / cron 触发实现            | 同上 |
| F4.5 | n8n `SlackApi.credentials.ts`               | 凭证类型声明 + password masked         | 当编排里要存第三方凭证 |
| F4.6 | OpenMontage `pipeline_loader.py:47-48` + JSON Schema | jsonschema 校验 pipeline_defs/*.yaml | 当 OpenAkita 引入声明式 pipeline 文件 |

---

## 6. 双轨质量门（§5）

按用户拍板的"两个都做"，每个插件 G1-G3 由两层执行：

| 层 | 触发 | 实现 | 主要面向 |
|----|------|------|----------|
| **Markdown 协议层** | 宿主 agent 调用前后自检 | `plugins/<id>/SKILL.md` 末尾 G1-G3 表格 | Agent / 文档 |
| **Python pytest 层** | CI / 本地 `pytest plugins/<id>/tests/` | `tests/test_quality_gates.py` 用 fixtures 跑 G1/G2/G3 | CI / 开发者 |

两层共用 `openakita_plugin_sdk/contrib/quality_gates.py` 的 `gate_g1_*`, `gate_g2_*`, `gate_g3_*` **纯函数**实现 — 单一真相源。

落地状态：

| 插件 | SKILL.md G1-G3 | pytest G1-G3 | 备注 |
|------|----------------|--------------|------|
| `bgm-suggester`   | ✅ | ✅ | `self_check` + `to_verification` 双签 |
| `storyboard`      | ✅ | ✅ | 三分自检 + verification badge |
| `seedance-video`  | 🚧 | ✅ | SKILL.md 待补 G1-G3 表格（Sprint 18 cleanup） |
| `tongyi-image`    | 🚧 | 🚧 | Sprint 18 |
| 其余 8 个        | 🚧 | 🚧 | Sprint 18 cleanup 统一补齐 |

---

## 7. 当前 SDK contrib 模块完整清单（Sprint 8 后）

| 模块                    | 复用插件                                     | 来源 ID         |
|-------------------------|---------------------------------------------|-----------------|
| `cost_tracker.py`       | seedance-video                              | D2.6            |
| `llm_json_parser.py`    | bgm-suggester / storyboard / seedance-video | D2.7            |
| `parallel_executor.py`  | seedance-video                              | N1.1            |
| `checkpoint.py`         | seedance-video                              | P3.5            |
| `quality_gates.py`      | bgm-suggester / storyboard                  | D2.1 / D2.3     |
| `intent_verifier.py`    | (待 Sprint 13 接入 storyboard)               | C0.6 / D2.13    |
| `provider_score.py`     | (待 Sprint 17 接入 highlight-cutter)         | D2.5            |
| `errors.py`             | (待 Sprint 18 在所有插件统一接入)            | D2.11 / D2.14   |
| `delivery_promise.py`   | (待 Sprint 14/17 真正接入)                   | D2.2            |
| `cost_estimator.py` + `cost_translation.py` | (host UI / 任务面板)         | D2.12           |
| `source_review.py`      | (待 Sprint 17 接入 highlight-cutter)         | D2.3            |
| `slideshow_risk.py`     | (待 Sprint 17 接入 storyboard / highlight)   | D2.1 / C0.3     |
| `task_manager.py`       | seedance-video                              | -               |
| `verification.py` ⭐    | bgm-suggester / storyboard                  | D2.10           |
| `agent_loop_config.py` ⭐| (待 Sprint 13 接入 BaseAgentLoop)            | C0.5            |
| `prompts.py` ⭐         | seedance-video / Sprint 13+ BaseAgentLoop    | P3.1-P3.5       |
| `tool_result.py` ⭐     | (待 Sprint 13 接入 BaseAgentLoop)            | C0.2            |
| `skill_loader.py` ⭐    | host skill discovery                        | C0.4            |

⭐ = Sprint 8 新增

---

## 8. Sprint 11-18 路线图（按本报告排序）

| Sprint | 插件 / 任务            | 主要消费的 ref 模式                         |
|--------|------------------------|---------------------------------------------|
| 11     | D2 `transcribe-archive` | N1.3（分片转录）+ video-use scribe 模式     |
| 12     | D1 `bgm-mixer`         | D2.8（madmom 参数）                         |
| 13     | D8 + D9 + B7 (clarify) | D2.13（think-with-you）+ N1.7/8（onboarding）|
| 14     | D4 `video-bg-remove`   | D2.2（motion ratio validate）               |
| 15     | D5 `ppt-to-video`      | OpenMontage slideshow grammar               |
| 16     | D6 `local-sd-flux`     | provider_ranker (D2.5) 接入 GPU 选型        |
| 17     | D3 + D7                | D2.1 slideshow_risk + D2.3 source_review    |
| 18     | cleanup                | N1.4 / N1.10 / D2.12 / D2.17 / SKILL.md G1-G3 全量补齐 |

每个 Sprint 启动前，工程师只需读本文件 §3 / §4 中对应行的 "落地位置" 即可定位到 SDK 函数 — 不必再回 `D:\OpenAkita_AI_Video\refs\`。

---

## 9. 验证：本报告的"落地位置"列已自检

下列 grep / 文件检查在 Sprint 10 落盘时全部通过（详见 git history）：

```bash
# 5 个 prompt asset 文件全部存在
ls openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/data/prompts/
#  → agent_loop_finishers.txt agent_loop_system.txt
#    checkpoint_protocol.md reviewer_protocol.md structure_proposal.txt

# 6 个 Sprint 8 新模块全部能 import
python -c "from openakita_plugin_sdk.contrib import (
    Verification, AgentLoopConfig, ToolResult,
    load_prompt, EvalResult,
)"
python -c "from openakita_plugin_sdk.skill_loader import load_skill"

# 全部 Sprint 7-9 测试 green
py -3.11 -m pytest plugins/ openakita-plugin-sdk/tests/ \
    tests/integration/test_sprint9_sdk_reuse.py
#  → plugins: 298 passed
#  → SDK: 341 passed, 1 skipped
#  → sprint9 reuse: 15 passed
```

---

> **维护规约**：每完成一个 Sprint 11-18 的子项，把对应行的状态从 ⏳ 升级为 ✅，
> 并在 §7 表格的 "复用插件" 列追加新插件名。本文件**不**写实现细节 —
> 实现细节在各模块 docstring + tests，本文件只做"参考来源 ↔ 当前状态"的索引。
