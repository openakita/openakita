#!/usr/bin/env python
"""
Telegram Bot 服务

使用 channels 框架组件，但采用更简单的启动方式
"""

import asyncio
import logging
import sys
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
        
        # 发送回复（处理长消息）
        if len(response) > 4000:
            parts = [response[i:i+4000] for i in range(0, len(response), 4000)]
            for part in parts:
                await message.reply_text(part)
        else:
            await message.reply_text(response)
        
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
