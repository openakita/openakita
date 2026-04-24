# Echo LLM Plugin

将用户消息原样回传的 LLM 提供商，用于测试 LLM 插件注册全链路。

## 功能

- `EchoProvider`: 实现 `chat()` 和 `chat_stream()`，echo 用户最后一条消息
- `EchoRegistry`: 返回固定模型列表 (`echo-default`, `echo-verbose`)
- 零外部依赖，无需 API Key

## 用途

验证 `register_llm_provider` + `register_llm_registry` 的注册、调用、清理全流程。

