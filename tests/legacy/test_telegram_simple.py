"""
Telegram 简单测试

测试 Telegram Bot API 底层接口
"""

import asyncio
import os
from pathlib import Path

import pytest

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# 这是一个“需要真实外部凭据”的集成测试模块：默认跳过，避免 CI/本地无凭据时报错。
if not BOT_TOKEN:
    pytest.skip("skip telegram integration tests (missing TELEGRAM_BOT_TOKEN)", allow_module_level=True)


@pytest.fixture
def chat_id() -> int:
    if not CHAT_ID:
        pytest.skip("missing TELEGRAM_CHAT_ID")
    return int(CHAT_ID)


@pytest.fixture
def bot():
    from telegram import Bot

    return Bot(token=BOT_TOKEN)


async def test_bot_info(bot):
    """测试获取 Bot 信息"""
    print("\n1. 测试获取 Bot 信息")
    print("-" * 40)

    me = await bot.get_me()

    print(f"   ✅ Bot ID: {me.id}")
    print(f"   ✅ Bot Name: {me.first_name}")
    print(f"   ✅ Bot Username: @{me.username}")
    print(f"   ✅ Can Join Groups: {me.can_join_groups}")
    print(f"   ✅ Can Read Group Messages: {me.can_read_all_group_messages}")

    assert me.username is not None


async def test_get_updates(bot):
    """测试获取更新 (查看是否有未处理的消息)"""
    print("\n2. 测试获取更新")
    print("-" * 40)
    
    try:
        updates = await bot.get_updates(limit=10, timeout=5)
        
        if updates:
            print(f"   ✅ 收到 {len(updates)} 条更新:")
            for update in updates[-5:]:  # 只显示最近5条
                if update.message:
                    msg = update.message
                    sender = msg.from_user.username or msg.from_user.first_name
                    text = msg.text or "[非文本消息]"
                    print(f"      - @{sender}: {text[:50]}...")
        else:
            print("   ℹ️ 没有新消息")
            print("   请在 Telegram 中给 @Jarvisuen_bot 发送消息后重新运行测试")
        
        return True
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False


async def test_send_message_to_user(bot, chat_id: int):
    """测试发送消息给用户"""
    print(f"\n3. 测试发送消息到 chat_id={chat_id}")
    print("-" * 40)
    
    try:
        message = await bot.send_message(
            chat_id=chat_id,
            text="🤖 *OpenAkita 测试消息*\n\n"
                 "这是来自 OpenAkita v0.5.0 的测试消息。\n"
                 "如果你看到这条消息，说明 Telegram 适配器工作正常！",
            parse_mode="Markdown",
        )
        
        print(f"   ✅ 消息发送成功!")
        print(f"   ✅ Message ID: {message.message_id}")
        
        return True
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False


async def test_webhook_info(bot):
    """测试 Webhook 信息"""
    print("\n4. 测试 Webhook 配置")
    print("-" * 40)
    
    try:
        info = await bot.get_webhook_info()
        
        if info.url:
            print(f"   ℹ️ Webhook URL: {info.url}")
            print(f"   ℹ️ Pending Updates: {info.pending_update_count}")
        else:
            print("   ✅ Webhook 未配置 (使用 Long Polling 模式)")
        
        return True
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False


async def test_adapter_message_conversion():
    """测试消息转换"""
    print("\n5. 测试消息类型转换")
    print("-" * 40)
    
    try:
        from openakita.channels import MessageContent, UnifiedMessage, MessageType, MediaFile
        
        # 测试纯文本
        content1 = MessageContent.text_only("Hello World")
        assert content1.message_type == MessageType.TEXT
        print("   ✅ 纯文本消息转换正常")
        
        # 测试命令
        content2 = MessageContent.text_only("/start 参数")
        assert content2.message_type == MessageType.COMMAND
        print("   ✅ 命令消息转换正常")
        
        # 测试图片
        media = MediaFile.create(filename="test.jpg", mime_type="image/jpeg")
        content3 = MessageContent.with_image(media, caption="图片说明")
        assert content3.message_type == MessageType.MIXED
        print("   ✅ 图片消息转换正常")
        
        # 测试 UnifiedMessage
        msg = UnifiedMessage.create(
            channel="telegram",
            channel_message_id="123",
            user_id="user_001",
            channel_user_id="tg_456",
            chat_id="chat_789",
            content=content2,
        )
        assert msg.is_command
        assert msg.command == "start"
        assert msg.command_args == "参数"
        print("   ✅ UnifiedMessage 命令解析正常")
        
        return True
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False


async def interactive_test(bot):
    """交互式测试 - 实时接收并回复消息"""
    print("\n" + "=" * 50)
    print("6. 交互式测试 (等待 30 秒)")
    print("=" * 50)
    print("请在 Telegram 中给 @Jarvisuen_bot 发送消息")
    print("Bot 会自动回复收到的消息")
    print("-" * 50)
    
    try:
        # 获取最新的 update_id
        updates = await bot.get_updates(limit=1, timeout=1)
        offset = updates[-1].update_id + 1 if updates else 0
        
        received_count = 0
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < 30:
            try:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=5,
                    allowed_updates=["message"],
                )
                
                for update in updates:
                    offset = update.update_id + 1
                    
                    if update.message:
                        msg = update.message
                        sender = msg.from_user
                        text = msg.text or "[非文本消息]"
                        
                        print(f"\n   📨 收到消息:")
                        print(f"      来自: @{sender.username or sender.first_name} (ID: {sender.id})")
                        print(f"      Chat ID: {msg.chat.id}")
                        print(f"      内容: {text[:100]}")
                        
                        # 回复消息
                        reply = f"✅ 收到你的消息: \"{text[:50]}...\"\n\n[OpenAkita 测试回复]"
                        await bot.send_message(
                            chat_id=msg.chat.id,
                            text=reply,
                            reply_to_message_id=msg.message_id,
                        )
                        print(f"      已回复! ✓")
                        
                        received_count += 1
                        
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"   ⚠️ 轮询错误: {e}")
                await asyncio.sleep(1)
        
        print("\n" + "-" * 50)
        if received_count > 0:
            print(f"   ✅ 成功处理 {received_count} 条消息")
        else:
            print("   ℹ️ 未收到新消息")
        
        return True
    except Exception as e:
        print(f"   ❌ 失败: {e}")
        return False


async def main():
    """主测试流程"""
    print("\n" + "=" * 60)
    print("Telegram Bot API 底层接口测试")
    print("Bot: @Jarvisuen_bot")
    print("=" * 60)
    
    results = {"passed": 0, "failed": 0}
    
    # 1. 测试 Bot 信息
    success, bot = await test_bot_info()
    if success:
        results["passed"] += 1
    else:
        results["failed"] += 1
        print("\n❌ Bot 连接失败，无法继续测试")
        return
    
    # 2. 测试获取更新
    success = await test_get_updates(bot)
    if success:
        results["passed"] += 1
    else:
        results["failed"] += 1
    
    # 3. 测试 Webhook
    success = await test_webhook_info(bot)
    if success:
        results["passed"] += 1
    else:
        results["failed"] += 1
    
    # 4. 测试消息转换
    success = await test_adapter_message_conversion()
    if success:
        results["passed"] += 1
    else:
        results["failed"] += 1
    
    # 5. 询问是否进行交互测试
    print("\n" + "-" * 60)
    print("是否进行交互式测试? (会等待 30 秒接收消息)")
    print("输入 y 开始交互测试，其他任意键跳过: ", end="", flush=True)
    
    import sys
    import select
    
    # 非阻塞读取 (Windows 兼容)
    try:
        if sys.platform == "win32":
            import msvcrt
            if msvcrt.kbhit():
                answer = msvcrt.getch().decode().lower()
            else:
                # 默认跳过
                answer = 'n'
                print("(自动跳过)")
        else:
            rlist, _, _ = select.select([sys.stdin], [], [], 5)
            answer = sys.stdin.readline().strip().lower() if rlist else 'n'
    except Exception:
        answer = 'n'
        print("(自动跳过)")
    
    if answer == 'y':
        success = await interactive_test(bot)
        if success:
            results["passed"] += 1
        else:
            results["failed"] += 1
    else:
        print("\n   跳过交互式测试")
    
    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"  ✅ 通过: {results['passed']}")
    print(f"  ❌ 失败: {results['failed']}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
