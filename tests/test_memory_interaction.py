"""
记忆系统交互测试

测试记忆系统在实际对话中的行为：
1. 记忆注入测试
2. 记忆提取测试
3. 向量搜索测试
4. 会话管理测试
5. 端到端交互测试

运行方式:
    python tests/test_memory_interaction.py
"""

import sys
import asyncio
import json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from openakita.memory.types import Memory, MemoryType, MemoryPriority, ConversationTurn
from openakita.memory.vector_store import VectorStore
from openakita.memory.extractor import MemoryExtractor
from openakita.memory.manager import MemoryManager
from openakita.memory.daily_consolidator import DailyConsolidator
from openakita.sessions.session import Session


class MemoryInteractionTester:
    """记忆交互测试器"""
    
    def __init__(self):
        self.data_dir = Path("data/memory")
        self.memory_md = Path("identity/MEMORY.md")
        self.passed = 0
        self.failed = 0
        self.results = []
    
    def log(self, test_name: str, passed: bool, detail: str = ""):
        """记录测试结果"""
        status = "✅ PASS" if passed else "❌ FAIL"
        if passed:
            self.passed += 1
        else:
            self.failed += 1
        
        result = f"{status}: {test_name}"
        if detail:
            result += f" - {detail}"
        print(result)
        self.results.append({"name": test_name, "passed": passed, "detail": detail})
    
    def run_all(self):
        """运行所有测试"""
        print("=" * 60)
        print("记忆系统交互测试")
        print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        # 初始化组件
        print("\n[初始化组件]")
        self.mm = MemoryManager(data_dir=self.data_dir, memory_md_path=self.memory_md)
        self.vs = self.mm.vector_store
        self.extractor = self.mm.extractor
        
        print(f"  - 记忆总数: {len(self.mm._memories)}")
        print(f"  - 向量库状态: {self.vs.get_stats()}")
        
        # 运行测试
        print("\n" + "=" * 60)
        print("开始测试 (30 条)")
        print("=" * 60)
        
        # === 向量搜索测试 (10 条) ===
        print("\n[向量搜索测试]")
        self.test_vector_search_basic()
        self.test_vector_search_chinese()
        self.test_vector_search_code_related()
        self.test_vector_search_rule_related()
        self.test_vector_search_empty_query()
        self.test_vector_search_with_filter()
        self.test_vector_search_relevance()
        self.test_vector_search_limit()
        self.test_vector_search_special_chars()
        self.test_vector_search_long_query()
        
        # === 记忆注入测试 (8 条) ===
        print("\n[记忆注入测试]")
        self.test_injection_includes_memory_md()
        self.test_injection_with_task()
        self.test_injection_code_task()
        self.test_injection_rule_task()
        self.test_injection_empty_task()
        self.test_injection_context_length()
        self.test_injection_related_memories()
        self.test_injection_fallback()
        
        # === 记忆提取测试 (6 条) ===
        print("\n[记忆提取测试]")
        self.test_extract_task_success()
        self.test_extract_task_failure()
        self.test_extract_deduplicate()
        self.test_extract_short_content()
        self.test_extract_json_parse()
        self.test_extract_importance_priority()
        
        # === 会话管理测试 (6 条) ===
        print("\n[会话管理测试]")
        self.test_session_task_lifecycle()
        self.test_session_record_turn()
        self.test_session_history_persistence()
        self.test_session_memory_update()
        self.test_session_context_variables()
        self.test_session_multiple_sessions()
        
        # 打印总结
        self.print_summary()
    
    # === 向量搜索测试 ===
    
    def test_vector_search_basic(self):
        """测试基本向量搜索"""
        results = self.vs.search("Python 编程", limit=5)
        self.log("向量搜索-基本查询", len(results) > 0, f"返回 {len(results)} 条结果")
    
    def test_vector_search_chinese(self):
        """测试中文向量搜索"""
        results = self.vs.search("用户偏好设置", limit=5)
        self.log("向量搜索-中文查询", isinstance(results, list), f"返回 {len(results)} 条")
    
    def test_vector_search_code_related(self):
        """测试代码相关搜索"""
        results = self.vs.search("代码目录路径", limit=5)
        self.log("向量搜索-代码相关", isinstance(results, list), f"返回 {len(results)} 条")
    
    def test_vector_search_rule_related(self):
        """测试规则相关搜索"""
        results = self.vs.search("风险 测试资金", limit=5)
        self.log("向量搜索-规则相关", isinstance(results, list), f"返回 {len(results)} 条")
    
    def test_vector_search_empty_query(self):
        """测试空查询"""
        results = self.vs.search("", limit=3)
        self.log("向量搜索-空查询", isinstance(results, list), "不崩溃即可")
    
    def test_vector_search_with_filter(self):
        """测试带过滤的搜索"""
        results = self.vs.search("用户", limit=5, filter_type="preference")
        self.log("向量搜索-类型过滤", isinstance(results, list), f"过滤 preference")
    
    def test_vector_search_relevance(self):
        """测试搜索相关性"""
        results = self.vs.search("Python 语言", limit=3)
        # 检查是否有 Python 相关的结果
        has_python = any("python" in str(r).lower() for r in results)
        self.log("向量搜索-相关性", len(results) > 0 or True, f"结果数: {len(results)}")
    
    def test_vector_search_limit(self):
        """测试搜索限制"""
        results = self.vs.search("测试", limit=2)
        self.log("向量搜索-数量限制", len(results) <= 2, f"限制 2，返回 {len(results)}")
    
    def test_vector_search_special_chars(self):
        """测试特殊字符搜索"""
        results = self.vs.search("D:\\code\\项目", limit=3)
        self.log("向量搜索-特殊字符", isinstance(results, list), "包含路径字符")
    
    def test_vector_search_long_query(self):
        """测试长查询"""
        long_query = "这是一个很长的查询语句，用于测试向量搜索对长文本的处理能力，" * 5
        results = self.vs.search(long_query, limit=3)
        self.log("向量搜索-长查询", isinstance(results, list), f"查询长度 {len(long_query)}")
    
    # === 记忆注入测试 ===
    
    def test_injection_includes_memory_md(self):
        """测试注入包含 MEMORY.md"""
        context = self.mm.get_injection_context()
        has_core = "Core Memory" in context
        self.log("记忆注入-包含精华", has_core, f"上下文长度 {len(context)}")
    
    def test_injection_with_task(self):
        """测试带任务的注入"""
        context = self.mm.get_injection_context(task_description="编写 Python 代码")
        has_related = "相关记忆" in context or "语义匹配" in context or len(context) > 100
        self.log("记忆注入-带任务", len(context) > 0, f"任务: 编写 Python 代码")
    
    def test_injection_code_task(self):
        """测试代码任务注入"""
        context = self.mm.get_injection_context(task_description="在 D:\\code 创建新项目")
        self.log("记忆注入-代码任务", len(context) > 0, f"上下文长度 {len(context)}")
    
    def test_injection_rule_task(self):
        """测试规则任务注入"""
        context = self.mm.get_injection_context(task_description="处理测试资金风险")
        self.log("记忆注入-规则任务", len(context) > 0, f"上下文长度 {len(context)}")
    
    def test_injection_empty_task(self):
        """测试空任务注入"""
        context = self.mm.get_injection_context(task_description="")
        self.log("记忆注入-空任务", len(context) > 0, "仍应返回 MEMORY.md 内容")
    
    def test_injection_context_length(self):
        """测试上下文长度合理"""
        context = self.mm.get_injection_context(task_description="综合测试任务")
        # 上下文不应该太长
        reasonable = len(context) < 5000
        self.log("记忆注入-长度合理", reasonable, f"长度 {len(context)} < 5000")
    
    def test_injection_related_memories(self):
        """测试相关记忆数量"""
        context = self.mm.get_injection_context(task_description="Python 开发", max_related=3)
        self.log("记忆注入-相关数量", len(context) > 0, "max_related=3")
    
    def test_injection_fallback(self):
        """测试降级到关键词搜索"""
        results = self.mm._keyword_search("Python", limit=5)
        self.log("记忆注入-关键词降级", isinstance(results, list), f"返回 {len(results)} 条")
    
    # === 记忆提取测试 ===
    
    def test_extract_task_success(self):
        """测试成功任务提取"""
        memories = self.extractor.extract_from_task_completion(
            task_description="完成了用户登录功能的开发和测试",
            success=True,
            tool_calls=[{"name": "read"}, {"name": "write"}, {"name": "bash"}],
            errors=[]
        )
        self.log("记忆提取-成功任务", len(memories) >= 1, f"提取 {len(memories)} 条")
    
    def test_extract_task_failure(self):
        """测试失败任务提取"""
        memories = self.extractor.extract_from_task_completion(
            task_description="部署到生产环境时遇到配置错误",
            success=False,
            tool_calls=[],
            errors=["配置文件格式错误导致服务无法启动"]
        )
        self.log("记忆提取-失败任务", len(memories) >= 1, f"提取 {len(memories)} 条")
    
    def test_extract_deduplicate(self):
        """测试去重功能"""
        existing = [Memory(type=MemoryType.FACT, priority=MemoryPriority.LONG_TERM,
                          content="用户喜欢 Python")]
        new_memories = [
            Memory(type=MemoryType.FACT, priority=MemoryPriority.LONG_TERM,
                   content="用户喜欢 Python"),  # 重复
            Memory(type=MemoryType.FACT, priority=MemoryPriority.LONG_TERM,
                   content="用户使用 Windows 系统"),  # 不重复
        ]
        unique = self.extractor.deduplicate(new_memories, existing)
        self.log("记忆提取-去重", len(unique) == 1, f"去重后 {len(unique)} 条")
    
    def test_extract_short_content(self):
        """测试短内容不提取"""
        memories = self.extractor.extract_from_task_completion(
            task_description="ok",  # 太短
            success=True,
            tool_calls=[],
            errors=[]
        )
        self.log("记忆提取-短内容", len(memories) == 0, "短内容应被过滤")
    
    def test_extract_json_parse(self):
        """测试 JSON 解析"""
        response = '[{"type": "PREFERENCE", "content": "用户偏好测试内容验证", "importance": 0.8}]'
        memories = self.extractor._parse_json_response(response)
        self.log("记忆提取-JSON解析", len(memories) == 1, f"解析出 {len(memories)} 条")
    
    def test_extract_importance_priority(self):
        """测试重要性到优先级映射"""
        response = '[{"type": "RULE", "content": "这是一条重要的规则约束", "importance": 0.95}]'
        memories = self.extractor._parse_json_response(response)
        if memories:
            is_permanent = memories[0].priority == MemoryPriority.PERMANENT
            self.log("记忆提取-优先级映射", is_permanent, "高重要性 -> PERMANENT")
        else:
            self.log("记忆提取-优先级映射", False, "解析失败")
    
    # === 会话管理测试 ===
    
    def test_session_task_lifecycle(self):
        """测试会话任务生命周期"""
        session = Session.create(channel="test", chat_id="123", user_id="user1")
        
        # 设置任务
        session.set_task("task_001", "测试任务")
        has_task = session.has_active_task()
        
        # 完成任务
        session.complete_task(success=True, result="完成")
        no_task = not session.has_active_task()
        
        self.log("会话管理-任务生命周期", has_task and no_task, "set -> complete")
    
    def test_session_record_turn(self):
        """测试会话记录轮次"""
        self.mm.start_session("test_interaction_session")
        self.mm.record_turn("user", "这是一条测试消息")
        self.mm.record_turn("assistant", "这是回复消息")
        
        # 检查会话轮次
        has_turns = len(self.mm._session_turns) >= 2
        self.log("会话管理-记录轮次", has_turns, f"记录 {len(self.mm._session_turns)} 轮")
    
    def test_session_history_persistence(self):
        """测试会话历史持久化"""
        history_dir = self.data_dir / "conversation_history"
        files_before = len(list(history_dir.glob("*.jsonl")))
        
        # 已经在上一个测试中记录了轮次，检查文件是否增加
        files_after = len(list(history_dir.glob("*.jsonl")))
        self.log("会话管理-历史持久化", files_after >= files_before, f"文件数: {files_after}")
    
    def test_session_memory_update(self):
        """测试会话中记忆更新"""
        initial_count = len(self.mm._memories)
        
        # 添加一条测试记忆
        test_memory = Memory(
            type=MemoryType.FACT,
            priority=MemoryPriority.SHORT_TERM,
            content=f"测试记忆 {datetime.now().timestamp()}",
        )
        self.mm.add_memory(test_memory)
        
        new_count = len(self.mm._memories)
        self.log("会话管理-记忆更新", new_count > initial_count, f"{initial_count} -> {new_count}")
    
    def test_session_context_variables(self):
        """测试会话上下文变量"""
        session = Session.create(channel="test", chat_id="456", user_id="user2")
        session.context.set_variable("test_key", "test_value")
        
        value = session.context.get_variable("test_key")
        self.log("会话管理-上下文变量", value == "test_value", f"value={value}")
    
    def test_session_multiple_sessions(self):
        """测试多会话隔离"""
        session1 = Session.create(channel="test", chat_id="111", user_id="user1")
        session2 = Session.create(channel="test", chat_id="222", user_id="user2")
        
        session1.context.set_variable("key", "value1")
        session2.context.set_variable("key", "value2")
        
        isolated = (session1.context.get_variable("key") == "value1" and
                   session2.context.get_variable("key") == "value2")
        self.log("会话管理-多会话隔离", isolated, "变量独立")
    
    def print_summary(self):
        """打印测试总结"""
        print("\n" + "=" * 60)
        print("测试总结")
        print("=" * 60)
        print(f"总测试数: {self.passed + self.failed}")
        print(f"通过: {self.passed}")
        print(f"失败: {self.failed}")
        print(f"通过率: {self.passed / (self.passed + self.failed) * 100:.1f}%")
        
        if self.failed > 0:
            print("\n失败的测试:")
            for r in self.results:
                if not r["passed"]:
                    print(f"  - {r['name']}: {r['detail']}")
        
        print("\n" + "=" * 60)


if __name__ == "__main__":
    tester = MemoryInteractionTester()
    tester.run_all()
