# 端点配置变更记录（RC-5 sprint S1 前置）

> 2026-05-29 ｜ 按 `_rc5_biz/sprint_plan/_prereq_apikey_403.md` 方案 A 执行。

## 改了什么

在 `data/llm_endpoints.json` 的 `endpoints[]` **新增一条**端点（不删、不改任何现有端点）：

```json
{
  "name": "dashscope-qwen3.5-plus-nothinking",
  "provider": "dashscope",
  "api_type": "openai",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_env": "DASHSCOPE_API_KEY",
  "model": "qwen3.5-plus",
  "priority": 30,
  "max_tokens": 0,
  "context_window": 200000,
  "timeout": 180,
  "rpm_limit": 0,
  "capabilities": ["text", "tools"],
  "extra_params": { "enable_thinking": false }
}
```

## 为什么这么配

- **关 thinking**：`extra_params.enable_thinking=false` + capabilities 故意**不含 `thinking`**
  （双保险：即使调用方传 `enable_thinking=true`，`_chat_impl` 也会因端点无 thinking 能力自动降级）。
  编排决策（progress_ledger 五元组）是结构化 JSON 输出，不需要推理链，关 thinking 省
  reasoning token + 降延迟 + 减少思维链污染 JSON 头导致的 parse 重试。
- **复用 DashScope key**：`api_key_env=DASHSCOPE_API_KEY`，与现有 `dashscope-deepseek-r1`
  端点同一个 key。**未新增、未读取、未打印任何 key 明文，未碰 `CUSTOM_API_KEY` / `.env`。**
- **model=qwen3.5-plus**：与 spike/Q2 锁定的工作模型一致（前置 A 澄清①已纠正"deepseek-r1"口误）。

## 零生产默认影响

- 新端点 `priority=30`，**低于**现有 `custom-qwen3.5-plus`(10) 与 `dashscope-deepseek-r1`(20)。
  默认 chat 路径仍按原顺序选端点（ctaigw→dashscope-deepseek-r1），新端点不会被默认选中。
- 编排路径**只在 live 复现 harness 内**通过 `LLMClient.switch_model(endpoint_name, policy="require")`
  显式锁定到这条 no-thinking 端点，**不影响任何默认/生产路径**。
- 未改 `orgs_supervisor_brain_mode`（仍默认 `passthrough`），未改 `command_service.py` submit 接线。
