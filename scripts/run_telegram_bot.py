#!/usr/bin/env python
"""
Telegram Bot 服务

使用 channels 框架组件，但采用更简单的启动方式
"""

import asyncio
import logging
import sys
import re
from pathlib import Path
from datetime import datetime

# 添加项目路径 (脚本在 scripts/ 目录下)
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from telegram import Bot, Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters

from openakita.config import settings
from openakita.channels.types import UnifiedMessage, MessageContent, MediaFile
from openakita.sessions import SessionManager, Session

# 配置 - 从环境变量或 settings 读取
import os
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
if not BOT_TOKEN:
    raise ValueError("请设置 TELEGRAM_BOT_TOKEN 环境变量或在 .env 中配置")

# 日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 全局组件
agent = None
session_manager = None


async def init_components():
    """初始化所有组件"""
    global agent, session_manager
    
    # 1. 初始化 Agent
    if agent is None:
        logger.info("正在初始化 Agent...")
        from openakita.core.agent import Agent
        agent = Agent()
        await agent.initialize()
        logger.info(f"Agent 初始化完成 (技能: {agent.skill_registry.count})")
    
    # 2. 初始化 SessionManager
    if session_manager is None:
        logger.info("正在初始化 SessionManager...")
        session_manager = SessionManager(
            storage_path=settings.project_root / "data" / "sessions",
        )
        await session_manager.start()
        logger.info("SessionManager 启动")
    
    return agent, session_manager


def get_session(channel: str, chat_id: str, user_id: str) -> Session:
    """获取或创建会话"""
    return session_manager.get_session(channel, chat_id, user_id)


def convert_markdown_for_telegram(text: str) -> str:
    """
    将标准 Markdown 转换为 Telegram 兼容格式
    
    Telegram Markdown 模式支持：
    - *bold* 或 **bold** → 粗体
    - _italic_ → 斜体
    - `code` → 代码
    - ```code block``` → 代码块
    - [link](url) → 链接
    
    不支持（需要转换或移除）：
    - 表格 (| xxx | xxx |) → 转为纯文本列表
    - 标题 (# xxx) → 移除 # 符号
    - 水平线 (---) → 转为分隔符
    """
    if not text:
        return text
    
    # 1. 移除标题符号（# → 保留文字）
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    
    # 2. 将表格转换为简单格式
    lines = text.split('\n')
    new_lines = []
    in_table = False
    table_rows = []
    
    for line in lines:
        stripped = line.strip()
        
        # 检测表格行
        if re.match(r'^\|.*\|$', stripped):
            # 跳过分隔行 (|---|---|)
            if re.match(r'^\|[-:\s|]+\|$', stripped):
                continue
            
            # 提取单元格内容
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            
            if not in_table:
                in_table = True
                # 第一行是表头，用粗体
                header = ' | '.join(f"*{c}*" for c in cells if c)
                table_rows.append(header)
            else:
                # 数据行
                row = ' | '.join(cells)
                table_rows.append(row)
        else:
            # 非表格行
            if in_table:
                # 表格结束，添加表格内容
                new_lines.extend(table_rows)
                table_rows = []
                in_table = False
            new_lines.append(line)
    
    # 处理文件末尾的表格
    if table_rows:
        new_lines.extend(table_rows)
    
    text = '\n'.join(new_lines)
    
    # 3. 将水平线转换为分隔符
    text = re.sub(r'^---+$', '─' * 20, text, flags=re.MULTILINE)
    
    # 4. 转义 Telegram Markdown 特殊字符（在非格式化区域）
    # 注意：不要转义已经是 Markdown 格式的部分
    # Telegram Markdown 模式对特殊字符比较宽容，通常不需要转义
    
    return text


async def handle_start(update: Update, context):
    """处理 /start 命令"""
    user = update.effective_user
    
    welcome_text = f"""👋 你好 {user.first_name}！

我是 **OpenAkita**，一个全能 AI 助手。

🔧 **功能：**
- 智能对话
- 执行任务
- 定时任务
- 更多...

直接发消息开始对话！
"""
    await update.message.reply_text(welcome_text, parse_mode="Markdown")


async def handle_status(update: Update, context):
    """处理 /status 命令"""
    status = "📊 **Agent 状态**\n\n"
    
    if agent and agent._initialized:
        status += f"✅ Agent: 已初始化\n"
        status += f"🧠 模型: {agent.brain.model}\n"
        status += f"📚 技能: {agent.skill_registry.count}\n"
        
        if session_manager:
            stats = session_manager.get_session_count()
            status += f"💬 会话: {stats['total']}\n"
    else:
        status += "⏳ Agent: 未初始化\n"
    
    status += f"\n🕐 时间: {datetime.now().strftime('%H:%M:%S')}"
    
    await update.message.reply_text(status, parse_mode="Markdown")


async def handle_message(update: Update, context):
    """处理用户消息"""
    message = update.message
    user = update.effective_user
    text = message.text or ""
    
    logger.info(f"收到消息 from @{user.username}: {text[:50]}...")
    
    # 发送"正在输入"状态
    await context.bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    try:
        # 确保组件已初始化
        await init_components()
        
        # 获取会话
        session = get_session(
            channel="telegram",
            chat_id=str(message.chat.id),
            user_id=f"tg_{user.id}",
        )
        
        # 记录用户消息到会话
        session.add_message("user", text)
        
        # 调用 Agent 处理
        response = await agent.chat(text)
        
        # 记录助手回复到会话
        session.add_message("assistant", response)
        
        # 转换 Markdown 为 Telegram 兼容格式
        telegram_text = convert_markdown_for_telegram(response)
        
        # 发送回复（处理长消息）
        async def safe_send(text_to_send: str):
            """安全发送消息，Markdown 解析失败时回退到纯文本"""
            try:
                await message.reply_text(text_to_send, parse_mode="Markdown")
            except Exception as e:
                if "Can't parse entities" in str(e) or "can't parse" in str(e).lower():
                    logger.warning(f"Markdown 解析失败，使用纯文本: {e}")
                    await message.reply_text(response)  # 使用原始文本，无格式
                else:
                    raise
        
        if len(telegram_text) > 4000:
            # 长消息分段发送
            parts = []
            current_part = ""
            for line in telegram_text.split('\n'):
                if len(current_part) + len(line) + 1 > 4000:
                    if current_part:
                        parts.append(current_part)
                    current_part = line
                else:
                    current_part = current_part + '\n' + line if current_part else line
            if current_part:
                parts.append(current_part)
            
            for part in parts:
                await safe_send(part)
        else:
            await safe_send(telegram_text)
        
        logger.info(f"回复发送成功 (会话: {session.id})")
        
    except Exception as e:
        logger.error(f"处理消息出错: {e}", exc_info=True)
        await message.reply_text(f"❌ 处理出错: {str(e)[:200]}")


async def post_init(application):
    """Application 初始化后的回调"""
    await init_components()
    
    print("=" * 50)
    print("🚀 OpenAkita Telegram Bot 已启动!")
    print(f"   Bot: @Jarvisuen_bot")
    print(f"   Agent 技能: {agent.skill_registry.count}")
    print("   按 Ctrl+C 停止")
    print("=" * 50)


def main():
    """主函数"""
    print("=" * 50)
    print("OpenAkita Telegram Bot")
    print("=" * 50)
    
    # 创建 Application
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # 添加处理器
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("status", handle_status))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 运行 (使用 run_polling，这是最简单可靠的方式)
    application.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message"],
    )


if __name__ == "__main__":
    main()

