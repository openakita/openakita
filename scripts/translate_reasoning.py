#!/usr/bin/env python3
"""Translate user-visible progress strings in reasoning_engine.py."""

path = "src/openakita/core/reasoning_engine.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

original = content

def r(old, new):
    global content
    content = content.replace(old, new)

# ── Progress strings sent to user via _emit_progress ──────────────────────
r('await _emit_progress(f"💭 **思考中**\\n{_think_preview}")',
  'await _emit_progress(f"💭 **Thinking**\\n{_think_preview}")')

r('await _emit_progress(\n                        f"AI 服务响应异常，正在重试"\n                    )',
  'await _emit_progress(\n                        f"AI service error, retrying…"\n                    )')
r('await _emit_progress(\n                        "当前模型不可用，正在切换到备用模型..."\n                    )',
  'await _emit_progress(\n                        "Current model unavailable, switching to fallback…"\n                    )')
r('await _emit_progress("🔄 任务尚未完成，继续处理...")',
  'await _emit_progress("🔄 Task not yet complete, continuing…")')
r('await _emit_progress(\n                        f"📦 上下文压缩: {_before_tokens // 1000}k → {_after_tokens // 1000}k tokens"\n                    )',
  'await _emit_progress(\n                        f"📦 Context compressed: {_before_tokens // 1000}k → {_after_tokens // 1000}k tokens"\n                    )')

# ── Stream chain_text ────────────────────────────────────────────────────
r('yield {"type": "chain_text", "content": "任务尚未完成，继续处理..."}',
  'yield {"type": "chain_text", "content": "Task not yet complete, continuing…"}')

# ── Ask-user reminder ────────────────────────────────────────────────────
r('reminder = "⏰ 我在等你回复上面的问题哦，看到的话回复一下~"',
  'reminder = "⏰ Waiting for your reply to the question above — please respond when you\'re ready."')

# ── Budget exhausted ─────────────────────────────────────────────────────
r(
    '                    f"⚠️ 任务资源预算已用尽（{budget_status.dimension}: "\n'
    '                    f"{budget_status.usage_ratio:.0%}），任务暂停。\\n"\n'
    '                    f"已完成的工作进度已保存，请调整预算后继续。"',
    '                    f"⚠️ Task resource budget exhausted ({budget_status.dimension}: "\n'
    '                    f"{budget_status.usage_ratio:.0%}). Task paused.\\n"\n'
    '                    f"Progress saved — adjust the budget and continue."',
)
r(
    '                        f"⚠️ 任务资源预算已用尽（{budget_status.dimension}: "\n'
    '                        f"{budget_status.usage_ratio:.0%}），任务暂停。\\n"\n'
    '                        f"已完成的工作进度已保存，请调整预算后继续。"',
    '                        f"⚠️ Task resource budget exhausted ({budget_status.dimension}: "\n'
    '                        f"{budget_status.usage_ratio:.0%}). Task paused.\\n"\n'
    '                        f"Progress saved — adjust the budget and continue."',
)

# ── Streaming model switch content ───────────────────────────────────────
r('"content": "当前模型不可用，正在切换到备用模型..."',
  '"content": "Current model unavailable, switching to fallback…"')

# ── Content truncation recovery ──────────────────────────────────────────
r('"content": "你的回答被截断了。请直接从断点处继续输出，不要重复已说过的内容，不要道歉。"',
  '"content": "Your response was cut off. Please continue directly from where you left off — do not repeat yourself or apologize."')

# ── AI service error in streaming path ──────────────────────────────────
r('f"AI 服务响应异常，正在重试"', 'f"AI service error, retrying…"')

if content == original:
    print("⚠️  No changes made — check source strings")
else:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ Done. {len(content) - len(original):+d} bytes")
