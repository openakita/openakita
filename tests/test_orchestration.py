"""
多 Agent 协同框架测试

测试内容:
- AgentRegistry: 注册、注销、状态管理
- AgentMessage: 消息序列化/反序列化
- AgentBus: 通信（需要 ZMQ）
- MasterAgent: 基本功能
"""

import pytest
import asyncio
import os
from datetime import datetime

# 测试消息协议
from openakita.orchestration.messages import (
    AgentMessage,
    AgentInfo,
    AgentStatus,
    AgentType,
    MessageType,
    CommandType,
    EventType,
    TaskPayload,
    TaskResult,
    create_register_command,
    create_chat_request,
)

# 测试注册中心
from openakita.orchestration.registry import AgentRegistry


class TestAgentMessage:
    """消息协议测试"""
    
    def test_create_command_message(self):
        """测试创建命令消息"""
        message = AgentMessage.command(
            sender_id="master",
            target_id="worker-001",
            command_type=CommandType.ASSIGN_TASK,
            payload={"task_id": "task-001", "content": "hello"},
        )
        
        assert message.msg_type == MessageType.COMMAND.value
        assert message.command_type == CommandType.ASSIGN_TASK.value
        assert message.sender_id == "master"
        assert message.target_id == "worker-001"
        assert message.payload["task_id"] == "task-001"
    
    def test_create_response_message(self):
        """测试创建响应消息"""
        message = AgentMessage.response(
            sender_id="worker-001",
            target_id="master",
            correlation_id="msg-123",
            payload={"success": True, "result": "done"},
        )
        
        assert message.msg_type == MessageType.RESPONSE.value
        assert message.correlation_id == "msg-123"
    
    def test_create_event_message(self):
        """测试创建事件消息"""
        message = AgentMessage.event(
            sender_id="master",
            event_type=EventType.AGENT_REGISTERED,
            payload={"agent_id": "worker-001"},
        )
        
        assert message.msg_type == MessageType.EVENT.value
        assert message.event_type == EventType.AGENT_REGISTERED.value
        assert message.target_id == "*"  # 广播
    
    def test_message_serialization(self):
        """测试消息序列化/反序列化"""
        original = AgentMessage.command(
            sender_id="master",
            target_id="worker-001",
            command_type=CommandType.CHAT_REQUEST,
            payload={"message": "你好", "session_id": "session-001"},
        )
        
        # 序列化
        json_str = original.to_json()
        assert isinstance(json_str, str)
        assert "master" in json_str
        assert "你好" in json_str
        
        # 反序列化
        restored = AgentMessage.from_json(json_str)
        assert restored.msg_id == original.msg_id
        assert restored.sender_id == original.sender_id
        assert restored.payload["message"] == "你好"
    
    def test_bytes_serialization(self):
        """测试字节序列化（用于 ZMQ）"""
        original = AgentMessage.command(
            sender_id="test",
            target_id="test",
            command_type=CommandType.GET_STATUS,
            payload={},
        )
        
        # 序列化为字节
        data = original.to_bytes()
        assert isinstance(data, bytes)
        
        # 反序列化
        restored = AgentMessage.from_bytes(data)
        assert restored.msg_id == original.msg_id


class TestAgentInfo:
    """Agent 信息测试"""
    
    def test_create_agent_info(self):
        """测试创建 Agent 信息"""
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
            capabilities=["chat", "execute"],
        )
        
        assert info.agent_id == "worker-001"
        assert info.status == AgentStatus.STARTING.value
        assert "chat" in info.capabilities
    
    def test_agent_info_serialization(self):
        """测试 Agent 信息序列化"""
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        data = info.to_dict()
        assert data["agent_id"] == "worker-001"
        
        restored = AgentInfo.from_dict(data)
        assert restored.agent_id == info.agent_id
    
    def test_set_task(self):
        """测试设置任务"""
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        info.set_task("task-001", "处理用户消息")
        
        assert info.current_task == "task-001"
        assert info.current_task_desc == "处理用户消息"
        assert info.status == AgentStatus.BUSY.value
    
    def test_clear_task(self):
        """测试清除任务"""
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        info.set_task("task-001", "处理用户消息")
        info.clear_task(success=True)
        
        assert info.current_task is None
        assert info.status == AgentStatus.IDLE.value
        assert info.tasks_completed == 1


class TestAgentRegistry:
    """Agent 注册中心测试"""
    
    def test_register_agent(self):
        """测试注册 Agent"""
        registry = AgentRegistry()
        
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        success = registry.register(info)
        assert success
        assert registry.count() == 1
    
    def test_unregister_agent(self):
        """测试注销 Agent"""
        registry = AgentRegistry()
        
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        registry.register(info)
        success = registry.unregister("worker-001")
        
        assert success
        assert registry.count() == 0
    
    def test_get_agent(self):
        """测试获取 Agent"""
        registry = AgentRegistry()
        
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        registry.register(info)
        
        retrieved = registry.get("worker-001")
        assert retrieved is not None
        assert retrieved.agent_id == "worker-001"
        
        not_found = registry.get("worker-999")
        assert not_found is None
    
    def test_find_idle_agent(self):
        """测试查找空闲 Agent"""
        registry = AgentRegistry()
        
        # 注册两个 Worker
        for i in range(2):
            info = AgentInfo(
                agent_id=f"worker-{i}",
                agent_type=AgentType.WORKER.value,
                process_id=12345 + i,
                capabilities=["chat"],
            )
            registry.register(info)
        
        # 将第一个设为 BUSY
        registry.set_agent_task("worker-0", "task-001")
        
        # 查找空闲的
        idle = registry.find_idle_agent()
        assert idle is not None
        assert idle.agent_id == "worker-1"
    
    def test_find_idle_agent_with_capabilities(self):
        """测试按能力查找空闲 Agent"""
        registry = AgentRegistry()
        
        # Worker 1: 只有 chat
        info1 = AgentInfo(
            agent_id="worker-1",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
            capabilities=["chat"],
        )
        registry.register(info1)
        
        # Worker 2: chat + code
        info2 = AgentInfo(
            agent_id="worker-2",
            agent_type=AgentType.WORKER.value,
            process_id=12346,
            capabilities=["chat", "code"],
        )
        registry.register(info2)
        
        # 查找有 code 能力的
        found = registry.find_idle_agent(capabilities=["code"])
        assert found is not None
        assert found.agent_id == "worker-2"
    
    def test_heartbeat(self):
        """测试心跳"""
        registry = AgentRegistry()
        
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        registry.register(info)
        
        # 更新心跳
        success = registry.heartbeat("worker-001")
        assert success
        
        # 不存在的 Agent
        success = registry.heartbeat("worker-999")
        assert not success
    
    def test_dashboard_data(self):
        """测试仪表盘数据"""
        registry = AgentRegistry()
        
        for i in range(3):
            info = AgentInfo(
                agent_id=f"worker-{i}",
                agent_type=AgentType.WORKER.value,
                process_id=12345 + i,
            )
            registry.register(info)
        
        # 设置一个为 BUSY
        registry.set_agent_task("worker-0", "task-001")
        
        data = registry.get_dashboard_data()
        
        assert data["summary"]["total_agents"] == 3
        assert data["summary"]["busy"] == 1
        assert data["summary"]["idle"] == 2
        assert len(data["agents"]) == 3


class TestTaskPayload:
    """任务负载测试"""
    
    def test_create_task_payload(self):
        """测试创建任务负载"""
        task = TaskPayload(
            task_id="task-001",
            task_type="chat",
            description="处理用户消息",
            content="你好",
            session_id="session-001",
        )
        
        assert task.task_id == "task-001"
        assert task.task_type == "chat"
    
    def test_task_payload_serialization(self):
        """测试任务负载序列化"""
        task = TaskPayload(
            task_id="task-001",
            task_type="chat",
            description="处理用户消息",
            content="你好",
        )
        
        data = task.to_dict()
        restored = TaskPayload.from_dict(data)
        
        assert restored.task_id == task.task_id
        assert restored.content == task.content


class TestTaskResult:
    """任务结果测试"""
    
    def test_create_success_result(self):
        """测试创建成功结果"""
        result = TaskResult(
            task_id="task-001",
            success=True,
            result="任务完成",
            duration_seconds=1.5,
        )
        
        assert result.success
        assert result.result == "任务完成"
    
    def test_create_failure_result(self):
        """测试创建失败结果"""
        result = TaskResult(
            task_id="task-001",
            success=False,
            error="超时",
        )
        
        assert not result.success
        assert result.error == "超时"


class TestConvenienceFunctions:
    """便捷函数测试"""
    
    def test_create_register_command(self):
        """测试创建注册命令"""
        info = AgentInfo(
            agent_id="worker-001",
            agent_type=AgentType.WORKER.value,
            process_id=12345,
        )
        
        message = create_register_command(info)
        
        assert message.command_type == CommandType.REGISTER.value
        assert message.target_id == "master"
    
    def test_create_chat_request(self):
        """测试创建对话请求"""
        message = create_chat_request(
            sender_id="master",
            target_id="worker-001",
            session_id="session-001",
            message="你好",
        )
        
        assert message.command_type == CommandType.CHAT_REQUEST.value
        assert message.payload["message"] == "你好"


# 异步测试（需要 pytest-asyncio）
@pytest.mark.asyncio
class TestAsyncOperations:
    """异步操作测试"""
    
    async def test_registry_concurrent_access(self):
        """测试注册中心并发访问"""
        registry = AgentRegistry()
        
        async def register_worker(i):
            info = AgentInfo(
                agent_id=f"worker-{i}",
                agent_type=AgentType.WORKER.value,
                process_id=12345 + i,
            )
            registry.register(info)
            await asyncio.sleep(0.01)
            return registry.get(f"worker-{i}")
        
        # 并发注册 10 个 Worker
        results = await asyncio.gather(*[
            register_worker(i) for i in range(10)
        ])
        
        assert all(r is not None for r in results)
        assert registry.count() == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
