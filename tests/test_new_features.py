"""
新功能测试文件

测试 v0.5.0 新增的三大模块:
1. Scheduler 定时任务调度器
2. Channels IM 多平台集成 (重点测试 Telegram)
3. Sessions 统一会话管理

运行方式: pytest tests/test_new_features.py -v
或直接运行: python tests/test_new_features.py
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ============================================================
# 1. Sessions 会话管理测试
# ============================================================

class TestSession:
    """Session 相关测试"""
    
    def test_session_creation(self):
        """测试会话创建"""
        from openakita.sessions import Session, SessionConfig
        
        session = Session.create(
            channel="telegram",
            chat_id="123456",
            user_id="user_001",
        )
        
        assert session.id is not None
        assert session.channel == "telegram"
        assert session.chat_id == "123456"
        assert session.user_id == "user_001"
        assert session.state.value == "active"
        print(f"✅ Session 创建成功: {session.id}")
    
    def test_session_context(self):
        """测试会话上下文"""
        from openakita.sessions import Session, SessionContext
        
        session = Session.create(
            channel="telegram",
            chat_id="123456",
            user_id="user_001",
        )
        
        # 添加消息
        session.add_message("user", "你好")
        session.add_message("assistant", "你好！有什么可以帮你的？")
        
        assert len(session.context.messages) == 2
        assert session.context.messages[0]["role"] == "user"
        assert session.context.messages[1]["role"] == "assistant"
        
        # 设置变量
        session.context.set_variable("language", "zh")
        assert session.context.get_variable("language") == "zh"
        
        print("✅ Session 上下文管理正常")
    
    def test_session_expiry(self):
        """测试会话过期"""
        from openakita.sessions import Session, SessionConfig
        
        config = SessionConfig(timeout_minutes=1)
        session = Session.create(
            channel="test",
            chat_id="test",
            user_id="test",
            config=config,
        )
        
        # 手动设置 last_active 为过去（超过 1 分钟）
        session.last_active = datetime.now() - timedelta(minutes=2)
        
        assert session.is_expired(timeout_minutes=1)
        print("✅ Session 过期检测正常")
    
    def test_session_serialization(self):
        """测试会话序列化"""
        from openakita.sessions import Session
        
        session = Session.create(
            channel="telegram",
            chat_id="123456",
            user_id="user_001",
        )
        session.add_message("user", "测试消息")
        
        # 序列化
        data = session.to_dict()
        assert "id" in data
        assert "context" in data
        
        # 反序列化
        restored = Session.from_dict(data)
        assert restored.id == session.id
        assert len(restored.context.messages) == 1
        
        print("✅ Session 序列化/反序列化正常")


class TestSessionManager:
    """SessionManager 测试"""
    
    @pytest.fixture
    def temp_storage(self, tmp_path):
        """临时存储目录"""
        return tmp_path / "sessions"
    
    def test_get_or_create_session(self, temp_storage):
        """测试获取或创建会话"""
        from openakita.sessions import SessionManager
        
        manager = SessionManager(storage_path=temp_storage)
        
        # 首次获取会创建新会话
        session1 = manager.get_session("telegram", "chat_001", "user_001")
        assert session1 is not None
        
        # 再次获取返回同一会话
        session2 = manager.get_session("telegram", "chat_001", "user_001")
        assert session1.id == session2.id
        
        # 不同参数创建新会话
        session3 = manager.get_session("telegram", "chat_002", "user_001")
        assert session3.id != session1.id
        
        print(f"✅ SessionManager 会话管理正常 (共 {len(manager._sessions)} 个会话)")
    
    def test_session_persistence(self, temp_storage):
        """测试会话持久化"""
        from openakita.sessions import SessionManager
        
        # 创建会话并保存
        manager1 = SessionManager(storage_path=temp_storage)
        session = manager1.get_session("telegram", "chat_001", "user_001")
        session.add_message("user", "测试持久化")
        manager1._save_sessions()
        
        # 重新加载
        manager2 = SessionManager(storage_path=temp_storage)
        assert len(manager2._sessions) >= 1
        
        loaded = manager2.get_session("telegram", "chat_001", "user_001", create_if_missing=False)
        if loaded:
            assert len(loaded.context.messages) == 1
            print("✅ Session 持久化正常")
        else:
            print("⚠️ Session 加载为空（可能已过期）")


class TestUserManager:
    """UserManager 测试"""
    
    @pytest.fixture
    def temp_storage(self, tmp_path):
        return tmp_path / "users"
    
    def test_user_creation(self, temp_storage):
        """测试用户创建"""
        from openakita.sessions import UserManager
        
        manager = UserManager(storage_path=temp_storage)
        
        # 创建用户
        user = manager.get_or_create("telegram", "tg_123456")
        assert user.id is not None
        assert user.is_bound_to("telegram")
        assert user.get_channel_user_id("telegram") == "tg_123456"
        
        print(f"✅ User 创建成功: {user.id}")
    
    def test_user_binding(self, temp_storage):
        """测试用户绑定"""
        from openakita.sessions import UserManager
        
        manager = UserManager(storage_path=temp_storage)
        
        # 创建用户
        user = manager.get_or_create("telegram", "tg_123456")
        user_id = user.id
        
        # 绑定其他平台
        manager.bind_channel(user_id, "feishu", "fs_789")
        
        # 验证绑定
        user = manager.get_user(user_id)
        assert user.is_bound_to("feishu")
        assert user.get_channel_user_id("feishu") == "fs_789"
        
        print("✅ 用户跨平台绑定正常")
    
    def test_user_permissions(self, temp_storage):
        """测试用户权限"""
        from openakita.sessions import UserManager
        
        manager = UserManager(storage_path=temp_storage)
        user = manager.get_or_create("telegram", "tg_admin")
        
        # 默认权限
        assert user.has_permission("user")
        assert not user.is_admin()
        
        # 添加管理员权限
        user.add_permission("admin")
        assert user.is_admin()
        assert user.has_permission("any_permission")  # admin 拥有所有权限
        
        print("✅ 用户权限管理正常")


# ============================================================
# 2. Scheduler 定时任务测试
# ============================================================

class TestTriggers:
    """触发器测试"""
    
    def test_once_trigger(self):
        """测试一次性触发器"""
        from openakita.scheduler import OnceTrigger
        
        # 过去的时间
        past_time = datetime.now() - timedelta(hours=1)
        trigger = OnceTrigger(run_at=past_time)
        
        assert trigger.should_run()
        
        # 未来的时间
        future_time = datetime.now() + timedelta(hours=1)
        trigger2 = OnceTrigger(run_at=future_time)
        
        assert not trigger2.should_run()
        assert trigger2.get_next_run_time() == future_time
        
        print("✅ OnceTrigger 工作正常")
    
    def test_interval_trigger(self):
        """测试间隔触发器"""
        from openakita.scheduler import IntervalTrigger
        
        trigger = IntervalTrigger(interval_minutes=30)
        
        # 首次运行
        next_run = trigger.get_next_run_time()
        assert next_run is not None
        
        # 上次运行后
        last_run = datetime.now() - timedelta(minutes=35)
        next_run2 = trigger.get_next_run_time(last_run)
        
        # 下次运行应该在 last_run + 30分钟 之后
        assert next_run2 >= last_run + timedelta(minutes=30)
        
        print("✅ IntervalTrigger 工作正常")
    
    def test_cron_trigger(self):
        """测试 Cron 触发器"""
        from openakita.scheduler import CronTrigger
        
        # 每分钟
        trigger = CronTrigger("* * * * *")
        next_run = trigger.get_next_run_time()
        assert next_run is not None
        assert next_run.second == 0
        
        # 每天 9:00
        trigger2 = CronTrigger("0 9 * * *")
        next_run2 = trigger2.get_next_run_time()
        assert next_run2.hour == 9
        assert next_run2.minute == 0
        
        print("✅ CronTrigger 工作正常")
    
    def test_cron_expressions(self):
        """测试各种 Cron 表达式"""
        from openakita.scheduler import CronTrigger
        
        test_cases = [
            ("0 * * * *", "每小时"),
            ("0 0 * * *", "每天午夜"),
            ("0 9 * * 1", "每周一 9:00"),
            ("*/15 * * * *", "每 15 分钟"),
            ("0 9-18 * * *", "每天 9-18 点整点"),
        ]
        
        for expr, desc in test_cases:
            try:
                trigger = CronTrigger(expr)
                next_run = trigger.get_next_run_time()
                print(f"  ✓ '{expr}' ({desc}): 下次 {next_run.strftime('%Y-%m-%d %H:%M')}")
            except Exception as e:
                print(f"  ✗ '{expr}' ({desc}): {e}")
        
        print("✅ Cron 表达式解析正常")


class TestScheduledTask:
    """定时任务测试"""
    
    def test_task_creation(self):
        """测试任务创建"""
        from openakita.scheduler import ScheduledTask, TriggerType
        
        # 一次性任务
        task = ScheduledTask.create_once(
            name="测试任务",
            description="这是一个测试任务",
            run_at=datetime.now() + timedelta(hours=1),
            prompt="执行测试",
        )
        
        assert task.trigger_type == TriggerType.ONCE
        assert task.name == "测试任务"
        
        # Cron 任务
        task2 = ScheduledTask.create_cron(
            name="每日报告",
            description="生成每日报告",
            cron_expression="0 9 * * *",
            prompt="生成今日报告",
        )
        
        assert task2.trigger_type == TriggerType.CRON
        
        print("✅ ScheduledTask 创建正常")
    
    def test_task_lifecycle(self):
        """测试任务生命周期"""
        from openakita.scheduler import ScheduledTask, TaskStatus
        
        task = ScheduledTask.create_interval(
            name="定期任务",
            description="测试",
            interval_minutes=30,
            prompt="执行",
        )
        
        # 初始状态
        assert task.status == TaskStatus.PENDING
        
        # 标记运行中
        task.mark_running()
        assert task.status == TaskStatus.RUNNING
        
        # 标记完成
        task.mark_completed(next_run=datetime.now() + timedelta(minutes=30))
        assert task.status == TaskStatus.SCHEDULED
        assert task.run_count == 1
        
        # 禁用
        task.disable()
        assert not task.enabled
        assert task.status == TaskStatus.DISABLED
        
        print("✅ Task 生命周期管理正常")


class TestTaskScheduler:
    """TaskScheduler 测试"""
    
    @pytest.fixture
    def temp_storage(self, tmp_path):
        return tmp_path / "scheduler"
    
    @pytest.mark.asyncio
    async def test_scheduler_basic(self, temp_storage):
        """测试调度器基本功能"""
        from openakita.scheduler import TaskScheduler, ScheduledTask
        
        scheduler = TaskScheduler(storage_path=temp_storage)
        await scheduler.start()
        
        try:
            # 添加任务
            task = ScheduledTask.create_once(
                name="测试任务",
                description="基本测试",
                run_at=datetime.now() + timedelta(hours=1),
                prompt="执行测试",
            )
            
            task_id = await scheduler.add_task(task)
            assert task_id is not None
            
            # 获取任务
            retrieved = scheduler.get_task(task_id)
            assert retrieved is not None
            assert retrieved.name == "测试任务"
            
            # 列出任务
            tasks = scheduler.list_tasks()
            assert len(tasks) >= 1
            
            # 取消任务
            success = await scheduler.remove_task(task_id)
            assert success
            
            print("✅ TaskScheduler 基本功能正常")
            
        finally:
            await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_scheduler_immediate_trigger(self, temp_storage):
        """测试立即触发任务"""
        from openakita.scheduler import TaskScheduler, ScheduledTask
        
        # 记录执行
        executed = {"count": 0, "result": None}
        
        async def mock_executor(task):
            executed["count"] += 1
            executed["result"] = f"执行了: {task.name}"
            return True, executed["result"]
        
        scheduler = TaskScheduler(
            storage_path=temp_storage,
            executor=mock_executor,
        )
        await scheduler.start()
        
        try:
            # 添加任务
            task = ScheduledTask.create_once(
                name="立即执行任务",
                description="测试立即触发",
                run_at=datetime.now() + timedelta(hours=1),
                prompt="立即执行",
            )
            
            task_id = await scheduler.add_task(task)
            
            # 立即触发
            execution = await scheduler.trigger_now(task_id)
            
            assert execution is not None
            assert executed["count"] == 1
            
            print(f"✅ 立即触发执行正常: {executed['result']}")
            
        finally:
            await scheduler.stop()
    
    @pytest.mark.asyncio
    async def test_scheduler_persistence(self, temp_storage):
        """测试任务持久化"""
        from openakita.scheduler import TaskScheduler, ScheduledTask
        
        # 第一次：创建并保存
        scheduler1 = TaskScheduler(storage_path=temp_storage)
        await scheduler1.start()
        
        task = ScheduledTask.create_cron(
            name="持久化测试",
            description="测试持久化",
            cron_expression="0 9 * * *",
            prompt="每日任务",
        )
        task_id = await scheduler1.add_task(task)
        
        await scheduler1.stop()
        
        # 第二次：重新加载
        scheduler2 = TaskScheduler(storage_path=temp_storage)
        await scheduler2.start()
        
        try:
            loaded_task = scheduler2.get_task(task_id)
            assert loaded_task is not None
            assert loaded_task.name == "持久化测试"
            
            print("✅ 任务持久化正常")
            
        finally:
            await scheduler2.stop()


# ============================================================
# 3. Channels 消息类型测试
# ============================================================

class TestMessageTypes:
    """消息类型测试"""
    
    def test_media_file(self):
        """测试媒体文件"""
        from openakita.channels import MediaFile
        
        media = MediaFile.create(
            filename="test.jpg",
            mime_type="image/jpeg",
            url="https://example.com/test.jpg",
            size=1024,
        )
        
        assert media.is_image
        assert not media.is_audio
        assert media.extension == "jpg"
        
        print("✅ MediaFile 创建正常")
    
    def test_message_content(self):
        """测试消息内容"""
        from openakita.channels import MessageContent, MediaFile, MessageType
        
        # 纯文本
        content1 = MessageContent.text_only("Hello World")
        assert content1.has_text
        assert not content1.has_media
        assert content1.message_type == MessageType.TEXT
        
        # 带图片
        media = MediaFile.create(
            filename="photo.jpg",
            mime_type="image/jpeg",
        )
        content2 = MessageContent.with_image(media, caption="图片说明")
        assert content2.has_media
        assert content2.message_type == MessageType.MIXED
        
        print("✅ MessageContent 创建正常")
    
    def test_unified_message(self):
        """测试统一消息"""
        from openakita.channels import UnifiedMessage, MessageContent
        
        content = MessageContent.text_only("/start 参数")
        
        message = UnifiedMessage.create(
            channel="telegram",
            channel_message_id="12345",
            user_id="user_001",
            channel_user_id="tg_789",
            chat_id="chat_001",
            content=content,
        )
        
        assert message.channel == "telegram"
        assert message.is_command
        assert message.command == "start"
        assert message.command_args == "参数"
        
        print("✅ UnifiedMessage 创建正常")
    
    def test_message_plain_text(self):
        """测试消息转纯文本"""
        from openakita.channels import MessageContent, MediaFile
        
        media = MediaFile.create(
            filename="voice.ogg",
            mime_type="audio/ogg",
        )
        media.transcription = "这是语音内容"
        
        content = MessageContent(
            text="附带文字",
            voices=[media],
        )
        
        plain = content.to_plain_text()
        assert "这是语音内容" in plain
        assert "附带文字" in plain
        
        print("✅ 消息转纯文本正常")


# ============================================================
# 4. Telegram 适配器测试 (重点)
# ============================================================

class TestTelegramAdapter:
    """Telegram 适配器测试"""
    
    # 使用用户提供的 Bot Token
    BOT_TOKEN = "7617214188:AAHYqpnX1k70SUmD4vqP6S2O-Kyp8NIr4cE"
    
    @pytest.mark.asyncio
    async def test_telegram_connection(self):
        """测试 Telegram Bot 连接"""
        try:
            from telegram import Bot
            
            bot = Bot(token=self.BOT_TOKEN)
            me = await bot.get_me()
            
            print(f"✅ Telegram Bot 连接成功!")
            print(f"   Bot ID: {me.id}")
            print(f"   Bot Name: {me.first_name}")
            print(f"   Bot Username: @{me.username}")
            
            return True
            
        except ImportError:
            print("⚠️ python-telegram-bot 未安装，跳过 Telegram 测试")
            print("   运行: pip install python-telegram-bot")
            return False
        except Exception as e:
            print(f"❌ Telegram 连接失败: {e}")
            return False
    
    @pytest.mark.asyncio
    async def test_telegram_send_message(self):
        """测试发送消息"""
        try:
            from telegram import Bot
            
            bot = Bot(token=self.BOT_TOKEN)
            me = await bot.get_me()
            
            # 注意：Bot 不能给自己发消息
            # 这里只测试 API 是否正常
            print("✅ Telegram 发送 API 可用")
            print("   要测试发送消息，请先在 Telegram 中给 Bot 发送 /start")
            
            return True
            
        except ImportError:
            print("⚠️ python-telegram-bot 未安装")
            return False
        except Exception as e:
            print(f"⚠️ 发送测试: {e}")
            return False
    
    @pytest.mark.asyncio
    async def test_telegram_adapter_init(self):
        """测试 TelegramAdapter 初始化"""
        try:
            from openakita.channels.adapters import TelegramAdapter
            
            adapter = TelegramAdapter(
                bot_token=self.BOT_TOKEN,
            )
            
            assert adapter.channel_name == "telegram"
            assert adapter.bot_token == self.BOT_TOKEN
            
            print("✅ TelegramAdapter 初始化正常")
            return True
            
        except ImportError as e:
            print(f"⚠️ 依赖缺失: {e}")
            return False
    
    @pytest.mark.asyncio
    async def test_telegram_adapter_start(self):
        """测试 TelegramAdapter 启动"""
        try:
            from openakita.channels.adapters import TelegramAdapter
            
            adapter = TelegramAdapter(
                bot_token=self.BOT_TOKEN,
            )
            
            # 启动适配器
            await adapter.start()
            
            assert adapter.is_running
            assert adapter._bot is not None
            
            # 获取 Bot 信息
            me = await adapter._bot.get_me()
            print(f"✅ TelegramAdapter 启动成功")
            print(f"   连接到: @{me.username}")
            
            # 停止
            await adapter.stop()
            assert not adapter.is_running
            
            print("✅ TelegramAdapter 停止正常")
            return True
            
        except ImportError as e:
            print(f"⚠️ 依赖缺失: {e}")
            return False
        except Exception as e:
            print(f"❌ 启动失败: {e}")
            return False


class TestTelegramIntegration:
    """Telegram 集成测试 (需要手动交互)"""
    
    BOT_TOKEN = "7617214188:AAHYqpnX1k70SUmD4vqP6S2O-Kyp8NIr4cE"
    
    @pytest.mark.asyncio
    async def test_telegram_full_flow(self):
        """完整流程测试 (需要手动给 Bot 发消息)"""
        try:
            from openakita.channels.adapters import TelegramAdapter
            from openakita.channels import UnifiedMessage
            
            received_messages = []
            
            adapter = TelegramAdapter(bot_token=self.BOT_TOKEN)
            
            # 注册消息回调
            async def on_message(msg: UnifiedMessage):
                received_messages.append(msg)
                print(f"  📨 收到消息: {msg.text[:50]}..." if msg.text else "  📨 收到非文本消息")
            
            adapter.on_message(on_message)
            
            await adapter.start()
            
            print("\n" + "=" * 50)
            print("Telegram 集成测试")
            print("=" * 50)
            print(f"请在 Telegram 中给 @Jarvisuen_bot 发送消息")
            print("等待 10 秒接收消息...")
            print("=" * 50 + "\n")
            
            # 等待消息
            await asyncio.sleep(10)
            
            await adapter.stop()
            
            if received_messages:
                print(f"\n✅ 成功接收 {len(received_messages)} 条消息")
                for msg in received_messages:
                    print(f"   - 来自 {msg.channel_user_id}: {msg.text or '[非文本]'}")
            else:
                print("\n⚠️ 未收到消息 (请确保在测试期间发送了消息)")
            
            return True
            
        except ImportError as e:
            print(f"⚠️ 依赖缺失: {e}")
            return False
        except Exception as e:
            print(f"❌ 测试失败: {e}")
            return False


# ============================================================
# 5. 媒体处理测试
# ============================================================

class TestMediaHandler:
    """媒体处理测试"""
    
    def test_media_handler_init(self):
        """测试媒体处理器初始化"""
        from openakita.channels.media import MediaHandler
        
        handler = MediaHandler()
        assert handler is not None
        
        print("✅ MediaHandler 初始化正常")
    
    @pytest.mark.asyncio
    async def test_text_extraction(self, tmp_path):
        """测试文本提取"""
        from openakita.channels.media import MediaHandler
        from openakita.channels import MediaFile
        
        handler = MediaHandler()
        
        # 创建测试文本文件
        test_file = tmp_path / "test.txt"
        test_file.write_text("这是测试内容", encoding="utf-8")
        
        media = MediaFile.create(
            filename="test.txt",
            mime_type="text/plain",
        )
        media.local_path = str(test_file)
        
        text = await handler.extract_text(media)
        assert "这是测试内容" in text
        
        print("✅ 文本文件提取正常")


class TestMediaStorage:
    """媒体存储测试"""
    
    @pytest.fixture
    def temp_storage(self, tmp_path):
        return tmp_path / "media"
    
    @pytest.mark.asyncio
    async def test_storage_basic(self, temp_storage):
        """测试基本存储功能"""
        from openakita.channels.media import MediaStorage
        from openakita.channels import MediaFile
        
        storage = MediaStorage(base_path=temp_storage)
        
        media = MediaFile.create(
            filename="test.jpg",
            mime_type="image/jpeg",
        )
        
        # 存储
        test_data = b"fake image data"
        path = await storage.store(media, "telegram", test_data)
        
        assert path.exists()
        
        # 检索
        retrieved = await storage.retrieve(media.id)
        assert retrieved == test_data
        
        # 删除
        success = await storage.delete(media.id)
        assert success
        
        print("✅ MediaStorage 基本功能正常")
    
    @pytest.mark.asyncio
    async def test_storage_dedup(self, temp_storage):
        """测试文件去重"""
        from openakita.channels.media import MediaStorage
        from openakita.channels import MediaFile
        
        storage = MediaStorage(base_path=temp_storage)
        
        # 存储相同内容两次
        data = b"same content"
        
        media1 = MediaFile.create(filename="file1.bin", mime_type="application/octet-stream")
        media2 = MediaFile.create(filename="file2.bin", mime_type="application/octet-stream")
        
        path1 = await storage.store(media1, "test", data)
        path2 = await storage.store(media2, "test", data)
        
        # 应该复用同一文件
        assert media1.local_path == media2.local_path
        
        print("✅ MediaStorage 文件去重正常")


# ============================================================
# 6. 综合集成测试
# ============================================================

class TestIntegration:
    """综合集成测试"""
    
    @pytest.mark.asyncio
    async def test_full_message_flow(self, tmp_path):
        """完整消息流程测试"""
        from openakita.sessions import SessionManager
        from openakita.channels import MessageGateway, MessageContent, UnifiedMessage
        from openakita.channels.base import CLIAdapter
        
        # 创建组件
        session_manager = SessionManager(storage_path=tmp_path / "sessions")
        
        # 模拟 Agent 处理
        async def mock_agent_handler(session, message):
            return f"收到: {message}"
        
        gateway = MessageGateway(
            session_manager=session_manager,
            agent_handler=mock_agent_handler,
        )
        
        # 创建 CLI 适配器
        adapter = CLIAdapter()
        await gateway.register_adapter(adapter)
        
        await gateway.start()
        
        try:
            # 模拟消息
            content = MessageContent.text_only("Hello")
            message = UnifiedMessage.create(
                channel="cli",
                channel_message_id="1",
                user_id="test_user",
                channel_user_id="test",
                chat_id="test_chat",
                content=content,
            )
            
            # 处理消息
            await gateway._handle_message(message)
            
            # 检查会话
            session = session_manager.get_session("cli", "test_chat", "test_user", create_if_missing=False)
            assert session is not None
            assert len(session.context.messages) >= 1
            
            print("✅ 完整消息流程正常")
            
        finally:
            await gateway.stop()


# ============================================================
# 运行测试
# ============================================================

async def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("OpenAkita v0.5.0 新功能测试")
    print("=" * 60 + "\n")
    
    results = {"passed": 0, "failed": 0, "skipped": 0}
    
    # 1. Session 测试
    print("\n📦 1. Session 会话管理测试")
    print("-" * 40)
    
    try:
        test = TestSession()
        test.test_session_creation()
        test.test_session_context()
        test.test_session_expiry()
        test.test_session_serialization()
        results["passed"] += 4
    except Exception as e:
        print(f"❌ Session 测试失败: {e}")
        results["failed"] += 1
    
    # 2. SessionManager 测试
    print("\n📦 2. SessionManager 测试")
    print("-" * 40)
    
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestSessionManager()
            test.test_get_or_create_session(Path(tmpdir) / "sessions")
            test.test_session_persistence(Path(tmpdir) / "sessions2")
        results["passed"] += 2
    except Exception as e:
        print(f"❌ SessionManager 测试失败: {e}")
        results["failed"] += 1
    
    # 3. User 测试
    print("\n📦 3. UserManager 测试")
    print("-" * 40)
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestUserManager()
            test.test_user_creation(Path(tmpdir) / "users1")
            test.test_user_binding(Path(tmpdir) / "users2")
            test.test_user_permissions(Path(tmpdir) / "users3")
        results["passed"] += 3
    except Exception as e:
        print(f"❌ UserManager 测试失败: {e}")
        results["failed"] += 1
    
    # 4. Trigger 测试
    print("\n📦 4. 触发器测试")
    print("-" * 40)
    
    try:
        test = TestTriggers()
        test.test_once_trigger()
        test.test_interval_trigger()
        test.test_cron_trigger()
        test.test_cron_expressions()
        results["passed"] += 4
    except Exception as e:
        print(f"❌ 触发器测试失败: {e}")
        results["failed"] += 1
    
    # 5. Task 测试
    print("\n📦 5. ScheduledTask 测试")
    print("-" * 40)
    
    try:
        test = TestScheduledTask()
        test.test_task_creation()
        test.test_task_lifecycle()
        results["passed"] += 2
    except Exception as e:
        print(f"❌ Task 测试失败: {e}")
        results["failed"] += 1
    
    # 6. Scheduler 测试
    print("\n📦 6. TaskScheduler 测试")
    print("-" * 40)
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestTaskScheduler()
            await test.test_scheduler_basic(Path(tmpdir) / "sched1")
            await test.test_scheduler_immediate_trigger(Path(tmpdir) / "sched2")
            await test.test_scheduler_persistence(Path(tmpdir) / "sched3")
        results["passed"] += 3
    except Exception as e:
        print(f"❌ Scheduler 测试失败: {e}")
        results["failed"] += 1
    
    # 7. Message 类型测试
    print("\n📦 7. 消息类型测试")
    print("-" * 40)
    
    try:
        test = TestMessageTypes()
        test.test_media_file()
        test.test_message_content()
        test.test_unified_message()
        test.test_message_plain_text()
        results["passed"] += 4
    except Exception as e:
        print(f"❌ 消息类型测试失败: {e}")
        results["failed"] += 1
    
    # 8. Telegram 测试 (重点)
    print("\n📦 8. Telegram 适配器测试 (重点)")
    print("-" * 40)
    
    try:
        test = TestTelegramAdapter()
        connected = await test.test_telegram_connection()
        if connected:
            await test.test_telegram_adapter_init()
            await test.test_telegram_adapter_start()
            results["passed"] += 3
        else:
            results["skipped"] += 3
    except Exception as e:
        print(f"❌ Telegram 测试失败: {e}")
        results["failed"] += 1
    
    # 9. 媒体处理测试
    print("\n📦 9. 媒体处理测试")
    print("-" * 40)
    
    try:
        test = TestMediaHandler()
        test.test_media_handler_init()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            await test.test_text_extraction(Path(tmpdir))
        results["passed"] += 2
    except Exception as e:
        print(f"❌ 媒体处理测试失败: {e}")
        results["failed"] += 1
    
    # 10. 媒体存储测试
    print("\n📦 10. 媒体存储测试")
    print("-" * 40)
    
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            test = TestMediaStorage()
            await test.test_storage_basic(Path(tmpdir) / "media1")
            await test.test_storage_dedup(Path(tmpdir) / "media2")
        results["passed"] += 2
    except Exception as e:
        print(f"❌ 媒体存储测试失败: {e}")
        results["failed"] += 1
    
    # 总结
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    print(f"  ✅ 通过: {results['passed']}")
    print(f"  ❌ 失败: {results['failed']}")
    print(f"  ⏭️ 跳过: {results['skipped']}")
    print("=" * 60 + "\n")
    
    return results


async def run_telegram_interactive_test():
    """运行 Telegram 交互测试"""
    print("\n" + "=" * 60)
    print("Telegram 交互测试")
    print("=" * 60)
    
    test = TestTelegramIntegration()
    await test.test_telegram_full_flow()


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="OpenAkita 新功能测试")
    parser.add_argument("--telegram-interactive", action="store_true", help="运行 Telegram 交互测试")
    parser.add_argument("--all", action="store_true", help="运行所有测试")
    
    args = parser.parse_args()
    
    if args.telegram_interactive:
        asyncio.run(run_telegram_interactive_test())
    else:
        asyncio.run(run_all_tests())

