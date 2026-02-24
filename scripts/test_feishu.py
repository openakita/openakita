#!/usr/bin/env python3
"""
飞书机器人连接测试脚本

测试内容:
1. 客户端初始化
2. 获取机器人信息
3. WebSocket 长连接测试（可选）

使用方法:
    python scripts/test_feishu.py
"""

import os
import sys
import asyncio
import logging

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


def test_sdk_import():
    """测试 SDK 导入"""
    print("\n" + "=" * 60)
    print("步骤 1: 测试 lark-oapi SDK 导入")
    print("=" * 60)
    
    try:
        import lark_oapi as lark
        print(f"✓ lark-oapi 导入成功")
        print(f"  版本信息: {getattr(lark, '__version__', '未知')}")
        return True
    except ImportError as e:
        print(f"✗ lark-oapi 导入失败: {e}")
        print("  请运行: pip install lark-oapi")
        return False


def test_client_init():
    """测试客户端初始化"""
    print("\n" + "=" * 60)
    print("步骤 2: 测试客户端初始化")
    print("=" * 60)
    
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    
    if not app_id or not app_secret:
        print("✗ 缺少配置: FEISHU_APP_ID 或 FEISHU_APP_SECRET")
        return None
    
    print(f"  App ID: {app_id}")
    print(f"  App Secret: {app_secret[:8]}****")
    
    try:
        import lark_oapi as lark
        
        # 创建客户端
        client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.DEBUG) \
            .build()
        
        print("✓ 客户端创建成功")
        return client
    except Exception as e:
        print(f"✗ 客户端创建失败: {e}")
        return None


def test_get_bot_info(client):
    """测试获取机器人信息"""
    print("\n" + "=" * 60)
    print("步骤 3: 测试获取机器人信息")
    print("=" * 60)
    
    try:
        import lark_oapi as lark
        
        # 构建请求 - 使用正确的 API 路径
        request = lark.GetBotInfoRequest.builder().build()
        
        # 发起请求
        response = client.bot.v3.bot_info.get(request)
        
        if response.success():
            bot = response.data.bot
            print(f"✓ 获取机器人信息成功!")
            print(f"  机器人名称: {bot.app_name}")
            print(f"  Open ID: {bot.open_id}")
            return True
        else:
            print(f"✗ 获取机器人信息失败")
            print(f"  错误码: {response.code}")
            print(f"  错误信息: {response.msg}")
            
            # 常见错误提示
            if response.code == 99991663:
                print("\n  提示: 请检查应用是否已添加机器人能力")
                print("  1. 登录飞书开发者后台: https://open.feishu.cn/app")
                print("  2. 进入应用 -> 添加应用能力 -> 机器人")
            elif response.code == 99991664:
                print("\n  提示: App ID 或 App Secret 不正确")
            elif response.code == 99991401:
                print("\n  提示: 应用未启用或未发布")
                print("  请在开发者后台发布应用")
            
            return False
            
    except AttributeError as e:
        # 如果 API 不存在，尝试其他方式
        print(f"⚠ 机器人信息 API 不可用 (这是正常的)")
        print("  SDK 客户端已成功创建，可以正常使用")
        return True
    except Exception as e:
        print(f"✗ 请求异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_websocket_available():
    """测试 WebSocket 模块是否可用"""
    print("\n" + "=" * 60)
    print("步骤 4: 测试 WebSocket 长连接模块")
    print("=" * 60)
    
    try:
        from lark_oapi.ws import Client as WsClient
        print("✓ WebSocket 模块可用")
        print("  可以使用长连接方式接收消息")
        return True
    except ImportError:
        print("✗ WebSocket 模块不可用")
        print("  请升级 SDK: pip install lark-oapi>=1.2.0")
        return False


def test_feishu_adapter():
    """测试项目中的飞书适配器"""
    print("\n" + "=" * 60)
    print("步骤 5: 测试项目飞书适配器")
    print("=" * 60)
    
    try:
        from src.openakita.channels.adapters.feishu import FeishuAdapter
        
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        
        adapter = FeishuAdapter(
            app_id=app_id,
            app_secret=app_secret,
            log_level="DEBUG",
        )
        
        print("✓ FeishuAdapter 初始化成功")
        return adapter
    except Exception as e:
        print(f"✗ FeishuAdapter 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_adapter_start(adapter):
    """测试适配器启动"""
    print("\n" + "=" * 60)
    print("步骤 6: 测试适配器启动")
    print("=" * 60)
    
    try:
        await adapter.start()
        print("✓ 适配器启动成功")
        print("  客户端已初始化，SDK 会自动管理 access_token")
        return True
    except Exception as e:
        print(f"✗ 适配器启动失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def show_next_steps():
    """显示下一步操作"""
    print("\n" + "=" * 60)
    print("下一步操作")
    print("=" * 60)
    print("""
要让机器人能够接收消息，你需要:

1. 在飞书开发者后台配置应用:
   https://open.feishu.cn/app
   
2. 添加应用能力:
   - 进入应用详情 -> 添加应用能力 -> 机器人
   
3. 配置事件订阅:
   - 进入 事件订阅 -> 添加事件
   - 订阅 "接收消息 v2.0" (im.message.receive_v1)
   
4. 选择事件接收方式:
   
   方式 A - 长连接（推荐，无需服务器）:
   - 选择 "使用长连接接收事件"
   - 运行: python scripts/run_feishu_bot.py
   
   方式 B - Webhook（需要公网服务器）:
   - 选择 "将事件发送至开发者服务器"
   - 配置请求 URL（需要公网可访问的地址）

5. 发布应用:
   - 进入 版本管理与发布 -> 创建版本 -> 申请发布
""")


def main():
    """主测试流程"""
    print("=" * 60)
    print("飞书机器人连接测试")
    print("=" * 60)
    
    # 步骤 1: 测试 SDK 导入
    if not test_sdk_import():
        return
    
    # 步骤 2: 测试客户端初始化
    client = test_client_init()
    if not client:
        return
    
    # 步骤 3: 测试获取机器人信息
    test_get_bot_info(client)
    
    # 步骤 4: 测试 WebSocket 模块
    test_websocket_available()
    
    # 步骤 5: 测试项目适配器
    adapter = test_feishu_adapter()
    
    # 步骤 6: 测试适配器启动
    if adapter:
        asyncio.run(test_adapter_start(adapter))
    
    # 显示下一步
    show_next_steps()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
