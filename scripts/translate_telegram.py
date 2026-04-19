#!/usr/bin/env python3
"""Translate all user-facing Chinese strings in telegram.py to English."""

path = "src/openakita/channels/adapters/telegram.py"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

original = content

def r(old, new):
    global content
    content = content.replace(old, new)

# ── Thinking/typing status labels ──────────────────────────────────────────
r('"💭 思考中..."', '"💭 Thinking..."')
r("self._typing_status[sk] = \"思考中\"", "self._typing_status[sk] = \"thinking\"")
r("self._typing_status[sk] = \"深度思考\"", "self._typing_status[sk] = \"deep thinking\"")
r("self._typing_status[sk] = \"调用工具\"", "self._typing_status[sk] = \"running tools\"")
r("self._typing_status[sk] = \"生成回复\"", "self._typing_status[sk] = \"generating\"")

# ── Thinking progress display ───────────────────────────────────────────────
r('parts.append("💭 思考中...")', 'parts.append("💭 Thinking...")')
r('parts.append(f"💭 思考过程{dur_str}\\n> " + preview.replace("\\n", "\\n> "))',
  'parts.append(f"💭 Thinking{dur_str}\\n> " + preview.replace("\\n", "\\n> "))')
r('header = f"💭 思考过程{dur_str}"', 'header = f"💭 Thinking{dur_str}"')
r('inner = "\\n\\n".join(parts) if parts else "💭 思考完成"',
  'inner = "\\n\\n".join(parts) if parts else "💭 Thinking complete"')
r('elapsed_suffix = f"\\n\\n⏱ 完成 ({time.time() - start:.1f}s)"',
  'elapsed_suffix = f"\\n\\n⏱ Done ({time.time() - start:.1f}s)"')
r('html += f"\\n⏱ 完成 ({elapsed:.1f}s)"',
  'html += f"\\n⏱ Done ({elapsed:.1f}s)"')

# ── Connection/startup errors ───────────────────────────────────────────────
r(
    '                     "Telegram API (api.telegram.org) 无法连接。"\n'
    '                     "如果你在中国大陆，需要配置代理才能使用 Telegram Bot。\\n"\n'
    '                     "配置方式（任选其一）：\\n"\n'
    '                     "  1. 在 IM 通道配置中添加 proxy 字段，如 socks5://127.0.0.1:7890\\n"\n'
    '                     "  2. 设置环境变量 TELEGRAM_PROXY=socks5://127.0.0.1:7890\\n"\n'
    '                     "  3. 使用支持 TUN 模式的代理工具（如 Clash TUN）"',
    '                     "Cannot connect to Telegram API (api.telegram.org).\\n"\n'
    '                     "If you are behind a firewall, configure a proxy.\\n"\n'
    '                     "Options:\\n"\n'
    '                     "  1. Add a proxy field to the IM channel config, e.g. socks5://127.0.0.1:7890\\n"\n'
    '                     "  2. Set env var TELEGRAM_PROXY=socks5://127.0.0.1:7890\\n"\n'
    '                     "  3. Use a TUN-mode proxy tool (e.g. Clash TUN)"',
)
r(
    '                     "Telegram Bot Token 无效或已过期，请在 @BotFather 检查 Token 是否正确。"',
    '                     "Telegram Bot Token is invalid or expired. Check your token at @BotFather."',
)

# ── Bot command menu descriptions ──────────────────────────────────────────
r('BotCommand("start", "开始使用 / 配对验证")', 'BotCommand("start", "Start / pairing verification")')
r('BotCommand("status", "查看配对状态")', 'BotCommand("status", "Check pairing status")')
r('BotCommand("unpair", "取消配对")', 'BotCommand("unpair", "Unpair this chat")')
r('BotCommand("model", "查看当前模型")', 'BotCommand("model", "Show current model")')
r('BotCommand("switch", "临时切换模型")', 'BotCommand("switch", "Temporarily switch model")')
r('BotCommand("priority", "调整模型优先级")', 'BotCommand("priority", "Adjust model priority")')
r('BotCommand("restore", "恢复默认模型")', 'BotCommand("restore", "Restore default model")')
r('BotCommand("thinking", "深度思考模式 (on/off/auto)")', 'BotCommand("thinking", "Deep thinking mode (on/off/auto)")')
r('BotCommand("thinking_depth", "思考深度 (low/medium/high)")', 'BotCommand("thinking_depth", "Thinking depth (low/medium/high)")')
r('BotCommand("chain", "思维链进度推送 (on/off)")', 'BotCommand("chain", "Reasoning chain push (on/off)")')
r('BotCommand("cancel", "取消当前操作")', 'BotCommand("cancel", "Cancel current operation")')
r('BotCommand("restart", "终极重启服务")', 'BotCommand("restart", "Restart the service")')
r('BotCommand("cancel_restart", "取消重启")', 'BotCommand("cancel_restart", "Cancel restart")')

# ── /start welcome/pairing flow ────────────────────────────────────────────
r(
    '                "🔐 欢迎使用 OpenAkita！\\n\\n"\n'
    '                "为了安全，首次使用需要配对验证。\\n"\n'
    '                "请输入 **配对码** 完成验证：\\n\\n"\n'
    '                f"📁 配对码文件：\\n`{code_file}`"',
    '                "🔐 Welcome to OpenAkita!\\n\\n"\n'
    '                "For security, first-time use requires pairing.\\n"\n'
    '                "Please enter the **pairing code** to verify:\\n\\n"\n'
    '                f"📁 Pairing code file:\\n`{code_file}`"',
)
r(
    '            "👋 你好！我是 OpenAkita，一个全能AI助手。\\n\\n"\n'
    '            "发送消息开始对话，我可以帮你：\\n"\n'
    '            "- 回答问题\\n"\n'
    '            "- 执行任务\\n"\n'
    '            "- 设置提醒\\n"\n'
    '            "- 处理文件\\n"\n'
    '            "- 更多功能...\\n\\n"\n'
    '            "有什么可以帮你的？"',
    '            "👋 Hi! I\'m OpenAkita, your all-in-one AI assistant.\\n\\n"\n'
    '            "Send a message to start. I can help you:\\n"\n'
    '            "- Answer questions\\n"\n'
    '            "- Execute tasks\\n"\n'
    '            "- Set reminders\\n"\n'
    '            "- Handle files\\n"\n'
    '            "- And much more...\\n\\n"\n'
    '            "How can I help you?"',
)
r(
    '                "🔓 已取消配对。\\n\\n如需重新使用，请发送 /start 并输入配对码。"',
    '                "🔓 Unpaired successfully.\\n\\nSend /start and enter the pairing code to use again."',
)
r('await message.reply_text("当前聊天未配对。")', 'await message.reply_text("This chat is not paired.")')
r('paired_at = info.get("paired_at", "未知")', 'paired_at = info.get("paired_at", "unknown")')
r(
    '                f"✅ 配对状态：已配对\\n📅 配对时间：{paired_at}\\n\\n发送 /unpair 可取消配对"',
    '                f"✅ Paired\\n📅 Paired at: {paired_at}\\n\\nSend /unpair to unpair"',
)
r(
    '            await message.reply_text("❌ 配对状态：未配对\\n\\n发送 /start 开始配对")',
    '            await message.reply_text("❌ Not paired\\n\\nSend /start to begin pairing")',
)
r(
    '                                "✅ 配对成功！\\n\\n"\n'
    '                                "现在你可以开始使用 OpenAkita 了。\\n"\n'
    '                                "发送消息开始对话，我可以帮你：\\n"\n'
    '                                "- 回答问题\\n"\n'
    '                                "- 执行任务\\n"\n'
    '                                "- 设置提醒\\n"\n'
    '                                "- 处理文件\\n"\n'
    '                                "- 更多功能..."',
    '                                "✅ Paired successfully!\\n\\n"\n'
    '                                "You can now use OpenAkita.\\n"\n'
    '                                "Send a message to start. I can help you:\\n"\n'
    '                                "- Answer questions\\n"\n'
    '                                "- Execute tasks\\n"\n'
    '                                "- Set reminders\\n"\n'
    '                                "- Handle files\\n"\n'
    '                                "- And much more..."',
)
r(
    '                                f"❌ 配对码错误，请重新输入。\\n\\n📁 配对码文件：\\n`{code_file}`"',
    '                                f"❌ Wrong pairing code. Please try again.\\n\\n📁 Pairing code file:\\n`{code_file}`"',
)
r(
    '                            "🔐 首次使用需要配对验证。\\n\\n"\n'
    '                            "请输入 **配对码** 完成验证：\\n\\n"\n'
    '                            f"📁 配对码文件：\\n`{code_file}`"',
    '                            "🔐 First-time use requires pairing.\\n\\n"\n'
    '                            "Please enter the **pairing code** to verify:\\n\\n"\n'
    '                            f"📁 Pairing code file:\\n`{code_file}`"',
)

assert content != original, "No changes made!"
with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print(f"✅ Done. {len(original) - len(content):+d} bytes")
