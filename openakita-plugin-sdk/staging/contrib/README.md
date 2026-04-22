# openakita-plugin-sdk/staging/contrib/ — 0-消费者参考代码区

本目录存放 **曾经在 `openakita_plugin_sdk.contrib` 命名空间下、但没有任何首方插件实际消费**的模块。

## 性质

- **只是代码归档**。没有 `__init__.py`，**不是一个可导入的 Python 包**，不属于 `openakita-plugin-sdk` 公开 API。
- 不参与构建（`pyproject.toml` 不打包），不参与 CI（`pytest` 不发现，因为测试也已下架到这里）。
- 如果将来有插件想用某个能力，**鼓励**：
  1. 把对应文件 `cp` 进自己插件目录，按需裁剪 → vendor 进插件；
  2. 或者经由代码评审，将其升级为带 stable contract 的独立小包（如 `openakita-cost-tracker`），由 SDK 之外的项目维护。

不鼓励：把模块原样搬回 `openakita-plugin-sdk/src/openakita_plugin_sdk/contrib/`。SDK 已经主动把自己缩回到「最小插件壳子」的原始定位（参见 README "Plugin Types at a Glance"）。

## 模块清单（17 + 周边）

### 成本 / checkpoint 体系（4 个）

- `cost_estimator.py` — `CostEstimator` / `CostPreview` / `to_human_units`（"奶茶"翻译器）
- `cost_translation.py` — `translate_cost` / `CostTemplate` / 内置 10 个插件文案表
- `cost_tracker.py` — `CostTracker` / reserve/reconcile/refund + `requires_approval`
- `checkpoint.py` — `take_checkpoint` / `restore_from_snapshot`

> seedance-video 之前的 demo 桩调用已在 0.7.0 移除。

### 智能体辅助（4 个）

- `intent_verifier.py` — "verify, not guess" 意图回放
- `prompt_optimizer.py` — vendor-agnostic LLM prompt 三档优化
- `agent_loop_config.py` — agent loop 默认配置常量
- `prompts/` (data/) — `load_prompt` / `render_prompt` + 两份 protocol markdown

### 治理 / 守门（5 个）

- `quality_gates.py` — G1/G2/G3 输入完整性 / 输出 schema / 错误可读性
- `slideshow_risk.py` — 6 维幻灯片风险启发式
- `delivery_promise.py` — 实际 vs 承诺动效占比
- `provider_score.py` — 7 维加权供应商打分
- `source_review.py` — 视频/图片/音频源审查（最大单模块，506 行）

### 依赖 / 环境（3 个）

- `dep_gate.py` — `DependencyGate` 系统依赖检查 + 安装事件
- `dep_catalog.py` — 内置 ffmpeg / whisper.cpp / yt-dlp 描述
- `env_any_loader.py` — 解析 SKILL.md frontmatter `env_any:`

### 其它（3 个）

- `tool_result.py` — `ToolResult` 工具结果包装
- `parallel_executor.py` — `run_parallel` 受限并发
- `skill_loader.py` — SKILL.md 加载（曾在 SDK 顶层）

## 历史

这些模块在 2026-04-19 ~ 2026-04-22 的几个 sprint 里集中产出（Sprint 0 / 6 / 8 / 13.1 / 13.4），最初的设想是让 D5 (`ppt-to-video`) / D7 (`dub-it`) 等"未来插件"使用。但截至 0.7.0 整改时，这些路线图条目均未落地，对应的"占位 API"反而拖累了 SDK 的简洁性，因此整体下沉。

如需翻看具体来源 commit，参考：

```
3f1c154e | feat(plugin-sdk): contrib subpackage + ui-kit + render-ready handshake
a5aab8ab | Sprint 0 — five hardened helpers
5346ad5b | Sprint 6 — cost_tracker + parallel_executor + checkpoint
ed3edb04 | Sprint 8 + 13.1 — 6件套补齐
14a9fd70 | Phase 1 — contrib.tts + contrib.asr (0.3.0 → 0.6.0)
```
