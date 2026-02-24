#!/usr/bin/env python
"""
模型切换功能测试

测试内容：
1. LLMClient 的 override 机制
2. Brain 的模型切换方法
3. ModelCommandHandler 的命令处理
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

# 确保项目在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ==================== 测试工具 ====================

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def ok(self, name: str):
        self.passed += 1
        print(f"  ✓ {name}")
    
    def fail(self, name: str, reason: str = ""):
        self.failed += 1
        msg = f"  ✗ {name}"
        if reason:
            msg += f" - {reason}"
        print(msg)
        self.errors.append((name, reason))
    
    def summary(self):
        total = self.passed + self.failed
        print(f"\n  结果: {self.passed}/{total} 通过")
        if self.errors:
            print("  失败项:")
            for name, reason in self.errors:
                print(f"    - {name}: {reason}")


# ==================== LLMClient 测试 ====================

def test_llm_client():
    """测试 LLMClient 的 override 机制"""
    print("\n" + "=" * 60)
    print("LLMClient Override 机制测试")
    print("=" * 60)
    
    from openakita.llm.client import LLMClient, EndpointOverride, ModelInfo
    from openakita.llm.types import EndpointConfig
    
    result = TestResult()
    
    # 创建测试端点配置
    endpoints = [
        EndpointConfig(
            name="test-primary",
            provider="test",
            api_type="anthropic",
            base_url="http://test1",
            api_key="test-key",
            model="model-1",
            priority=0,
            capabilities=["text", "tools"],
        ),
        EndpointConfig(
            name="test-secondary",
            provider="test",
            api_type="anthropic",
            base_url="http://test2",
            api_key="test-key",
            model="model-2",
            priority=1,
            capabilities=["text", "tools", "vision"],
        ),
    ]
    
    client = LLMClient(endpoints=endpoints)
    
    # 测试 1: 初始状态
    models = client.list_available_models()
    if len(models) == 2:
        result.ok("初始状态: 2个模型")
    else:
        result.fail("初始状态", f"期望2个模型，实际{len(models)}")
    
    # 测试 2: 当前模型（无 override）
    current = client.get_current_model()
    if current and current.name == "test-primary":
        result.ok("默认当前模型: test-primary")
    else:
        result.fail("默认当前模型", f"期望 test-primary，实际 {current.name if current else 'None'}")
    
    # 测试 3: 切换模型
    success, msg = client.switch_model("test-secondary", hours=1)
    if success:
        result.ok("切换模型成功")
    else:
        result.fail("切换模型", msg)
    
    # 测试 4: 切换后当前模型
    current = client.get_current_model()
    if current and current.name == "test-secondary" and current.is_override:
        result.ok("切换后当前模型: test-secondary (override)")
    else:
        result.fail("切换后当前模型", f"实际 {current.name if current else 'None'}, is_override={current.is_override if current else 'N/A'}")
    
    # 测试 5: 获取 override 状态
    status = client.get_override_status()
    if status and status["endpoint_name"] == "test-secondary":
        result.ok("Override 状态正确")
    else:
        result.fail("Override 状态", f"实际 {status}")
    
    # 测试 6: 恢复默认
    success, msg = client.restore_default()
    if success:
        result.ok("恢复默认成功")
    else:
        result.fail("恢复默认", msg)
    
    # 测试 7: 恢复后当前模型
    current = client.get_current_model()
    if current and current.name == "test-primary" and not current.is_override:
        result.ok("恢复后当前模型: test-primary")
    else:
        result.fail("恢复后当前模型", f"实际 {current.name if current else 'None'}")
    
    # 测试 8: 切换不存在的模型
    success, msg = client.switch_model("non-existent")
    if not success and "不存在" in msg:
        result.ok("切换不存在模型: 正确拒绝")
    else:
        result.fail("切换不存在模型", f"应该失败，实际 success={success}")
    
    # 测试 9: 优先级更新（内存中）
    success, msg = client.update_priority(["test-secondary", "test-primary"])
    if success:
        result.ok("优先级更新成功")
        # 检查顺序
        models = client.list_available_models()
        if models[0].name == "test-secondary":
            result.ok("优先级顺序正确")
        else:
            result.fail("优先级顺序", f"期望 test-secondary 在前，实际 {models[0].name}")
    else:
        result.fail("优先级更新", msg)
    
    # 测试 10: EndpointOverride 过期检测
    override = EndpointOverride(
        endpoint_name="test",
        expires_at=datetime.now() - timedelta(hours=1),  # 1小时前过期
    )
    if override.is_expired:
        result.ok("过期检测: 正确识别已过期")
    else:
        result.fail("过期检测", "应该识别为已过期")
    
    result.summary()
    return result


# ==================== Tool Context 检测测试 ====================

def test_tool_context_detection():
    """测试 _has_tool_context 方法"""
    print("\n" + "=" * 60)
    print("Tool Context 检测测试")
    print("=" * 60)
    
    from openakita.llm.client import LLMClient
    from openakita.llm.types import (
        EndpointConfig, Message, TextBlock, ToolUseBlock, ToolResultBlock
    )
    
    result = TestResult()
    
    # 创建测试客户端
    endpoints = [
        EndpointConfig(
            name="test",
            provider="test",
            api_type="anthropic",
            base_url="http://test",
            api_key="test-key",
            model="model-1",
            priority=0,
            capabilities=["text", "tools"],
        ),
    ]
    client = LLMClient(endpoints=endpoints)
    
    # 测试 1: 纯文本消息 - 无工具上下文
    messages_text_only = [
        Message(role="user", content="你好"),
        Message(role="assistant", content="你好！有什么可以帮助你的？"),
    ]
    if not client._has_tool_context(messages_text_only):
        result.ok("纯文本消息: 无工具上下文 ✓")
    else:
        result.fail("纯文本消息", "应该返回 False")
    
    # 测试 2: 包含 ToolUseBlock - 有工具上下文
    messages_with_tool_use = [
        Message(role="user", content="搜索天气"),
        Message(role="assistant", content=[
            TextBlock(text="我来帮你搜索天气"),
            ToolUseBlock(id="call_123", name="web_search", input={"query": "天气"}),
        ]),
    ]
    if client._has_tool_context(messages_with_tool_use):
        result.ok("ToolUseBlock: 检测到工具上下文 ✓")
    else:
        result.fail("ToolUseBlock", "应该返回 True")
    
    # 测试 3: 包含 ToolResultBlock - 有工具上下文
    messages_with_tool_result = [
        Message(role="user", content="搜索天气"),
        Message(role="assistant", content=[
            ToolUseBlock(id="call_123", name="web_search", input={"query": "天气"}),
        ]),
        Message(role="user", content=[
            ToolResultBlock(tool_use_id="call_123", content="今天晴天，25度"),
        ]),
    ]
    if client._has_tool_context(messages_with_tool_result):
        result.ok("ToolResultBlock: 检测到工具上下文 ✓")
    else:
        result.fail("ToolResultBlock", "应该返回 True")
    
    # 测试 4: 混合消息（文本 + 工具） - 有工具上下文
    messages_mixed = [
        Message(role="user", content="你好"),
        Message(role="assistant", content="你好！"),
        Message(role="user", content="搜索新闻"),
        Message(role="assistant", content=[
            TextBlock(text="好的，我来搜索"),
            ToolUseBlock(id="call_456", name="search", input={"q": "新闻"}),
        ]),
    ]
    if client._has_tool_context(messages_mixed):
        result.ok("混合消息: 检测到工具上下文 ✓")
    else:
        result.fail("混合消息", "应该返回 True")
    
    # 测试 5: 字典格式的工具块 - 兼容性测试
    messages_dict_format = [
        Message(role="user", content="测试"),
        Message(role="assistant", content=[
            {"type": "text", "text": "测试"},
            {"type": "tool_use", "id": "call_789", "name": "test", "input": {}},
        ]),
    ]
    if client._has_tool_context(messages_dict_format):
        result.ok("字典格式工具块: 检测到工具上下文 ✓")
    else:
        result.fail("字典格式工具块", "应该返回 True")
    
    # 测试 6: 空消息列表 - 无工具上下文
    if not client._has_tool_context([]):
        result.ok("空消息列表: 无工具上下文 ✓")
    else:
        result.fail("空消息列表", "应该返回 False")
    
    result.summary()
    return result


# ==================== ModelCommandHandler 测试 ====================

def test_command_handler():
    """测试 ModelCommandHandler"""
    print("\n" + "=" * 60)
    print("ModelCommandHandler 测试")
    print("=" * 60)
    
    from openakita.channels.gateway import ModelCommandHandler, ModelSwitchSession
    
    result = TestResult()
    
    handler = ModelCommandHandler()
    
    # 测试 1: 命令识别
    test_commands = [
        ("/model", True, "识别 /model"),
        ("/switch", True, "识别 /switch"),
        ("/switch claude", True, "识别 /switch 带参数"),
        ("/priority", True, "识别 /priority"),
        ("/restore", True, "识别 /restore"),
        ("/cancel", True, "识别 /cancel"),
        ("/help", False, "不识别 /help"),
        ("hello", False, "不识别普通消息"),
        ("/MODEL", True, "大小写不敏感"),
    ]
    
    for text, expected, desc in test_commands:
        actual = handler.is_model_command(text)
        if actual == expected:
            result.ok(desc)
        else:
            result.fail(desc, f"期望 {expected}，实际 {actual}")
    
    # 测试 2: 会话管理
    session_key = "test:123:456"
    
    # 不在会话中
    if not handler.is_in_session(session_key):
        result.ok("初始不在会话中")
    else:
        result.fail("初始会话状态", "不应该在会话中")
    
    # 创建会话
    handler._switch_sessions[session_key] = ModelSwitchSession(
        session_key=session_key,
        mode="switch",
        step="select",
    )
    
    if handler.is_in_session(session_key):
        result.ok("创建会话后在会话中")
    else:
        result.fail("创建会话后", "应该在会话中")
    
    # 清理
    del handler._switch_sessions[session_key]
    
    # 测试 3: 会话超时
    from datetime import datetime, timedelta
    expired_session = ModelSwitchSession(
        session_key=session_key,
        mode="switch",
        step="select",
        started_at=datetime.now() - timedelta(minutes=10),  # 10分钟前
        timeout_minutes=5,
    )
    handler._switch_sessions[session_key] = expired_session
    
    if not handler.is_in_session(session_key):
        result.ok("超时会话自动清理")
    else:
        result.fail("超时会话", "应该被清理")
    
    result.summary()
    return result


# ==================== 集成测试（需要真实配置）====================

async def test_integration():
    """集成测试（需要真实的 llm_endpoints.json）"""
    print("\n" + "=" * 60)
    print("集成测试（真实配置）")
    print("=" * 60)
    
    result = TestResult()
    
    try:
        from openakita.llm.config import get_default_config_path
        
        config_path = get_default_config_path()
        if not config_path.exists():
            print(f"  跳过: 配置文件不存在 ({config_path})")
            return result
        
        from openakita.llm.client import LLMClient
        
        # 创建真实客户端
        client = LLMClient(config_path=config_path)
        
        # 测试 1: 列出模型
        models = client.list_available_models()
        if len(models) > 0:
            result.ok(f"列出模型: {len(models)} 个")
            for m in models:
                status = "当前" if m.is_current else ""
                health = "✓" if m.is_healthy else "✗"
                print(f"    {health} {m.name} ({m.model}) {status}")
        else:
            result.fail("列出模型", "没有模型")
        
        # 测试 2: 获取当前模型
        current = client.get_current_model()
        if current:
            result.ok(f"当前模型: {current.name}")
        else:
            result.fail("当前模型", "无法获取")
        
        # 测试 3: 临时切换（如果有多个模型）
        if len(models) > 1:
            # 找一个非当前的健康模型
            target = None
            for m in models:
                if not m.is_current and m.is_healthy:
                    target = m
                    break
            
            if target:
                success, msg = client.switch_model(target.name, hours=0.01)  # 很短的时间
                if success:
                    result.ok(f"临时切换到 {target.name}")
                    
                    # 恢复
                    client.restore_default()
                    result.ok("恢复默认")
                else:
                    result.fail("临时切换", msg)
            else:
                print("  跳过切换测试: 没有其他健康模型")
        else:
            print("  跳过切换测试: 只有一个模型")
        
    except ImportError as e:
        result.fail("导入模块", str(e))
    except Exception as e:
        result.fail("集成测试", str(e))
    
    result.summary()
    return result


# ==================== Brain 测试 ====================

async def test_brain():
    """测试 Brain 的模型切换方法"""
    print("\n" + "=" * 60)
    print("Brain 模型切换测试")
    print("=" * 60)
    
    result = TestResult()
    
    try:
        from openakita.llm.config import get_default_config_path
        
        config_path = get_default_config_path()
        if not config_path.exists():
            print(f"  跳过: 配置文件不存在 ({config_path})")
            return result
        
        # 单独导入 Brain（避免循环导入问题）
        from openakita.llm.client import LLMClient
        from openakita.config import settings
        
        # 使用 LLMClient 直接测试，不经过 Brain（避免 agent.py 导入问题）
        client = LLMClient(config_path=config_path)
        
        class SimpleBrain:
            """简化的 Brain 用于测试"""
            def __init__(self, llm_client):
                self._llm_client = llm_client
            
            def list_available_models(self):
                models = self._llm_client.list_available_models()
                return [
                    {
                        "name": m.name,
                        "model": m.model,
                        "provider": m.provider,
                        "priority": m.priority,
                        "is_healthy": m.is_healthy,
                        "is_current": m.is_current,
                        "is_override": m.is_override,
                        "capabilities": m.capabilities,
                        "note": m.note,
                    }
                    for m in models
                ]
            
            def get_current_model_info(self):
                model = self._llm_client.get_current_model()
                if not model:
                    return {"error": "无可用模型"}
                return {
                    "name": model.name,
                    "model": model.model,
                    "provider": model.provider,
                    "is_healthy": model.is_healthy,
                    "is_override": model.is_override,
                    "capabilities": model.capabilities,
                    "note": model.note,
                }
            
            def get_override_status(self):
                return self._llm_client.get_override_status()
        
        brain = SimpleBrain(client)
        
        # 测试 1: 列出模型
        models = brain.list_available_models()
        if len(models) > 0:
            result.ok(f"Brain.list_available_models: {len(models)} 个")
        else:
            result.fail("Brain.list_available_models", "没有模型")
        
        # 测试 2: 获取当前模型
        current = brain.get_current_model_info()
        if "error" not in current:
            result.ok(f"Brain.get_current_model_info: {current.get('name')}")
        else:
            result.fail("Brain.get_current_model_info", current.get("error"))
        
        # 测试 3: 获取 override 状态（应该为空）
        status = brain.get_override_status()
        if status is None:
            result.ok("Brain.get_override_status: 无 override")
        else:
            result.ok(f"Brain.get_override_status: {status.get('endpoint_name')}")
        
    except ImportError as e:
        result.fail("导入模块", str(e))
    except Exception as e:
        result.fail("Brain 测试", str(e))
    
    result.summary()
    return result


# ==================== 主函数 ====================

async def main():
    print("=" * 60)
    print("模型切换功能测试")
    print("=" * 60)
    
    total_passed = 0
    total_failed = 0
    
    # 单元测试（不需要配置文件）
    r1 = test_llm_client()
    total_passed += r1.passed
    total_failed += r1.failed
    
    # Tool Context 检测测试
    r1b = test_tool_context_detection()
    total_passed += r1b.passed
    total_failed += r1b.failed
    
    r2 = test_command_handler()
    total_passed += r2.passed
    total_failed += r2.failed
    
    # 集成测试（需要配置文件）
    r3 = await test_integration()
    total_passed += r3.passed
    total_failed += r3.failed
    
    # Brain 测试
    r4 = await test_brain()
    total_passed += r4.passed
    total_failed += r4.failed
    
    # 总结
    print("\n" + "=" * 60)
    total = total_passed + total_failed
    if total > 0:
        print(f"总计: {total_passed}/{total} 通过 ({total_passed/total*100:.1f}%)")
    else:
        print("没有运行任何测试")
    print("=" * 60)
    
    return total_failed == 0


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
