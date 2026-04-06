# 提交 7cab0d17 中 core/agent.py 变更审查报告

## 完整 diff
（已通过 `git show 7cab0d17 -- src/openakita/core/agent.py` 获取，共 216 行变更）

## 逐段分析上下文注入逻辑的变更

### 1. 系统提示词构建方法的增强
**变更位置**: `_build_system_prompt_compiled` 方法

**新增功能**:
- **添加 `session` 参数**: 允许传入当前 Session 实例，用于提取元数据。
- **提取模型显示名称**: 
  ```python
  model_display = ""
  try:
      conv_id = session.id if session else None
      model_info = self.brain.get_current_model_info(conversation_id=conv_id)
      if isinstance(model_info, dict) and "model" in model_info:
          model_display = model_info["model"]
  except Exception:
      pass
  ```
- **提取会话上下文信息**:
  ```python
  session_context = None
  if session:
      try:
          sub_records = getattr(session.context, "sub_agent_records", None) or []
          session_context = {
              "session_id": session.id,
              "channel": getattr(session, "channel", "unknown"),
              "chat_type": getattr(session, "chat_type", "private"),
              "message_count": len(session.context.messages) if session.context else 0,
              "has_sub_agents": bool(sub_records),
              "sub_agent_count": len(sub_records),
          }
      except Exception:
          pass
  ```
- **传递新参数**: 将 `model_display_name` 和 `session_context` 传递给 `prompt_assembler.build_system_prompt_compiled`。

**分析**: 
- 这个变更是为了在系统提示词中注入更多会话元数据，让 LLM 了解当前会话的状态。
- 使用 try-except 包装，确保即使提取失败也不会影响核心功能。

### 2. 历史消息处理的重构
**变更位置**: 历史消息处理循环

**主要变更**:
1. **改进截断逻辑**: 
   ```python
   # 旧逻辑: 简单截断
   if _marker in content:
       content = content[:content.index(_marker)]
   
   # 新逻辑: 智能截断，保留后续章节
   while _marker in content:
       idx = content.index(_marker)
       before = content[:idx]
       after = content[idx + len(_marker):]
       next_section = -1
       for sep in ("\n\n[", "\n\n##", "\n\n---"):
           pos = after.find(sep)
           if pos != -1 and (next_section == -1 or pos < next_section):
               next_section = pos
       if next_section != -1:
           content = before + after[next_section:]
       else:
           content = before
   ```
   **分析**: 新逻辑会查找标记后的内容，如果找到下一个章节（以 `\n\n[`、`\n\n##` 或 `\n\n---` 开头），则保留该章节，否则完全截断。这避免了误删有效内容。

2. **添加时间戳前缀**:
   ```python
   ts = msg.get("timestamp", "")
   # ...
   if ts and isinstance(content, str):
       try:
           t = datetime.fromisoformat(ts)
           time_prefix = f"[{t.strftime('%H:%M')}] "
           if not _RE_TIME_PREFIX.match(content):
               content = time_prefix + content
       except Exception:
           pass
   ```
   **分析**: 从消息的 `timestamp` 字段提取时间，格式化为 `[HH:MM]` 前缀，添加到消息内容前。使用正则表达式 `_RE_TIME_PREFIX` 避免重复添加。

3. **注入子代理委派结果摘要**:
   ```python
   # 10.5 注入子 Agent 委派结果摘要到最后一条 assistant 消息
   if session and hasattr(session, "context"):
       sub_records = getattr(session.context, "sub_agent_records", None)
       if sub_records and messages:
           summary_parts = []
           for r in sub_records:
               name = r.get("agent_name", "unknown")
               preview = r.get("result_preview", "")
               if preview:
                   summary_parts.append(f"- {name}: {preview[:500]}")
           if summary_parts:
               delegation_summary = (
                   "\n\n[委派任务执行记录]\n" + "\n".join(summary_parts)
               )
               for i in range(len(messages) - 1, -1, -1):
                   if messages[i]["role"] == "assistant":
                       messages[i]["content"] += delegation_summary
                       break
   ```
   **分析**: 将子代理的执行结果摘要注入到最后一条助手消息中，使用 `[委派任务执行记录]` 标记。这有助于 LLM 了解之前的委派任务执行情况。

### 3. 当前用户消息标记的改进
**变更位置**: 当前用户消息处理

**变更内容**:
```python
# 旧逻辑
if _has_history and compiled_message:
    compiled_message = (
        "[以上是之前的对话历史，仅供参考。"
        "请直接回应我的最新消息，不要重复或重新执行历史中已完成的操作：]\n"
        + compiled_message
    )

# 新逻辑
if _has_history and compiled_message and isinstance(compiled_message, str):
    compiled_message = f"[最新消息]\n{compiled_message}"
```

**分析**:
- **旧逻辑**: 使用较长的提示词，包含“仅供参考”和“不要重复”等指令。
- **新逻辑**: 简化为 `[最新消息]` 标记，更简洁，避免误导 LLM。
- 这是为了解决提交信息中提到的“‘仅供参考’前缀误导 LLM”的问题。

### 4. 会话上下文传递的统一化
**变更位置**: 多处调用 `_build_system_prompt_compiled` 的地方

**变更内容**: 在多个调用中添加了 `session=session` 参数：
- 在 `process_message` 方法中
- 在 CHAT 路径中
- 在轻量级空回复处理中
- 在其他会话处理中

**分析**: 确保所有构建系统提示词的地方都能获取到会话上下文信息。

## 新逻辑的工作原理

### 会话元数据和时间戳标记
1. **会话元数据提取**:
   - `session_id`: 会话唯一标识
   - `channel`: 消息通道（如 Telegram、Feishu 等）
   - `chat_type`: 聊天类型（如 private、group）
   - `message_count`: 当前会话消息数量
   - `has_sub_agents` 和 `sub_agent_count`: 子代理信息

2. **时间戳标记**:
   - 从每条消息的 `timestamp` 字段提取时间
   - 格式化为 `[HH:MM]` 前缀（如 `[23:08]`）
   - 使用正则表达式 `r"^\[\d{1,2}:\d{2}\]\s"` 检测是否已存在时间戳，避免重复添加

### 记忆系统优先级策略
- **注意**: 提交信息中提到“重写 _MEMORY_SYSTEM_GUIDE 建立三级信息优先级”，但本次 diff 中未显示相关变更。这可能在其他文件中（如 `prompt/builder.py` 或 `memory/` 目录下的文件）。
- **推断**: 根据提交信息，新的优先级策略可能是：
  1. **对话历史**（最高优先级）：当前会话的对话内容
  2. **注入记忆**（中等优先级）：从记忆系统注入的相关信息
  3. **搜索工具**（最低优先级）：通过搜索工具获取的信息

### 子代理隔离机制
- **注意**: 提交信息中提到“并行子 Agent 浏览器隔离（_IsolatedBrowserContext）”，但本次 diff 中未显示相关变更。这可能在其他文件中。
- **推断**: 可能是为并行执行的子代理创建独立的浏览器上下文，避免相互干扰。

## 潜在问题和改进建议

### 潜在问题
1. **时间戳格式兼容性**:
   - 使用 `datetime.fromisoformat(ts)` 解析时间戳，这要求时间戳是 ISO 格式。如果消息中的时间戳格式不一致，可能导致解析失败。
   - **建议**: 添加格式检查或使用更健壮的时间解析库。

2. **子代理结果摘要截断**:
   - 使用 `preview[:500]` 截断结果预览，可能导致重要信息丢失。
   - **建议**: 考虑使用更智能的截断策略，如保留开头和结尾。

3. **会话上下文提取的异常处理**:
   - 使用 `try-except Exception` 捕获所有异常，可能掩盖具体问题。
   - **建议**: 记录异常日志，便于调试。

4. **内存使用**:
   - 注入大量会话元数据和子代理结果摘要可能增加内存使用。
   - **建议**: 监控内存使用情况，必要时添加限制。

### 改进建议
1. **添加配置选项**: 允许通过配置启用/禁用时间戳标记和子代理结果注入。
2. **优化时间戳格式**: 支持更多时间戳格式，或允许自定义格式。
3. **增强子代理摘要**: 包含更多信息，如执行时间、状态等。
4. **添加测试**: 为新的上下文注入逻辑添加单元测试。

## 总结
本次提交主要解决了上下文记忆混乱问题，通过以下方式改进：
1. **增强系统提示词**: 注入会话元数据和模型信息
2. **改进历史消息处理**: 添加时间戳、改进截断逻辑、注入子代理结果
3. **简化用户消息标记**: 将“仅供参考”改为更简洁的“[最新消息]”
4. **统一会话上下文传递**: 确保所有构建提示词的地方都能获取会话信息

这些变更旨在提供更清晰的上下文信息，帮助 LLM 更好地理解会话状态和历史，从而减少记忆混乱问题。然而，部分变更（如记忆系统优先级和子代理隔离）未在本次 diff 中体现，可能需要在其他文件中查看。