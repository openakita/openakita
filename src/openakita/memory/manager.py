"""
记忆管理器 - 核心记忆系统

功能:
1. 协调实时提取和批量整理
2. 管理 MEMORY.md 精华摘要
3. 提供记忆注入策略（向量搜索 + 精华摘要）
4. 自动触发记忆更新

注入策略:
- 每次对话: 加载 MEMORY.md 精华 + 向量搜索相关记忆
- 会话结束: 保存新记忆到 memories.json 和 ChromaDB
"""

import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import re

from .types import Memory, MemoryType, MemoryPriority, ConversationTurn, SessionSummary
from .extractor import MemoryExtractor
from .consolidator import MemoryConsolidator
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class MemoryManager:
    """记忆管理器"""
    
    def __init__(
        self,
        data_dir: Path,
        memory_md_path: Path,
        brain=None,
        embedding_model: str = None,
        embedding_device: str = "cpu",
    ):
        """
        Args:
            data_dir: 数据目录
            memory_md_path: MEMORY.md 文件路径
            brain: LLM 大脑实例
            embedding_model: embedding 模型名称（可选）
            embedding_device: 设备 (cpu 或 cuda)
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.memory_md_path = Path(memory_md_path)
        self.brain = brain
        
        # 子组件
        self.extractor = MemoryExtractor(brain)
        self.consolidator = MemoryConsolidator(data_dir, brain, self.extractor)
        
        # 向量存储（延迟初始化）
        self.vector_store = VectorStore(
            data_dir=self.data_dir,
            model_name=embedding_model,
            device=embedding_device,
        )
        
        # 记忆存储
        self.memories_file = self.data_dir / "memories.json"
        self._memories: dict[str, Memory] = {}
        
        # 当前会话
        self._current_session_id: Optional[str] = None
        self._session_turns: list[ConversationTurn] = []
        
        # 加载记忆
        self._load_memories()
    
    def _load_memories(self) -> None:
        """加载所有记忆"""
        if self.memories_file.exists():
            try:
                with open(self.memories_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for item in data:
                        memory = Memory.from_dict(item)
                        self._memories[memory.id] = memory
                logger.info(f"Loaded {len(self._memories)} memories")
            except Exception as e:
                logger.error(f"Failed to load memories: {e}")
    
    def _save_memories(self) -> None:
        """保存所有记忆"""
        try:
            data = [m.to_dict() for m in self._memories.values()]
            with open(self.memories_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save memories: {e}")
    
    # ==================== 会话管理 ====================
    
    def start_session(self, session_id: str) -> None:
        """开始新会话"""
        self._current_session_id = session_id
        self._session_turns = []
        logger.info(f"Started session: {session_id}")
    
    def record_turn(self, role: str, content: str, tool_calls: list = None, tool_results: list = None) -> None:
        """记录对话轮次"""
        turn = ConversationTurn(
            role=role,
            content=content,
            tool_calls=tool_calls or [],
            tool_results=tool_results or [],
        )
        self._session_turns.append(turn)
        
        # 保存到历史
        if self._current_session_id:
            self.consolidator.save_conversation_turn(self._current_session_id, turn)
        
        # 实时提取 (只从用户消息)
        if role == "user":
            memories = self.extractor.extract_from_turn(turn)
            for memory in memories:
                self.add_memory(memory)
    
    def end_session(self, task_description: str = "", success: bool = True, errors: list = None) -> None:
        """结束会话"""
        if not self._current_session_id:
            return
        
        # 从任务完成结果提取记忆
        tool_calls = []
        for turn in self._session_turns:
            tool_calls.extend(turn.tool_calls)
        
        memories = self.extractor.extract_from_task_completion(
            task_description=task_description,
            success=success,
            tool_calls=tool_calls,
            errors=errors or [],
        )
        
        for memory in memories:
            self.add_memory(memory)
        
        logger.info(f"Ended session {self._current_session_id}: {len(memories)} memories extracted")
        
        self._current_session_id = None
        self._session_turns = []
    
    # ==================== 记忆操作 ====================
    
    # 向量相似度阈值（余弦距离，越小越相似）
    DUPLICATE_DISTANCE_THRESHOLD = 0.25
    
    def add_memory(self, memory: Memory) -> str:
        """
        添加记忆
        
        同时存入:
        1. memories.json（完整数据）
        2. ChromaDB（向量索引）
        
        去重策略:
        1. 字符串前缀匹配（快速）
        2. 向量相似度检测（语义）
        """
        # 1. 字符串去重检查（快速）
        existing = list(self._memories.values())
        unique = self.extractor.deduplicate([memory], existing)
        
        if not unique:
            logger.debug(f"Memory duplicate (string match): {memory.content[:50]}")
            return ""
        
        memory = unique[0]
        
        # 2. 向量相似度检测（语义去重）
        if self.vector_store.enabled and len(self._memories) > 0:
            similar = self.vector_store.search(memory.content, limit=3)
            for mid, distance in similar:
                if distance < self.DUPLICATE_DISTANCE_THRESHOLD:
                    existing_mem = self._memories.get(mid)
                    if existing_mem:
                        logger.info(f"Memory duplicate (semantic, dist={distance:.3f}): "
                                   f"'{memory.content[:30]}...' similar to '{existing_mem.content[:30]}...'")
                        return ""  # 语义重复，不存入
        
        # 3. 存入 memories.json
        self._memories[memory.id] = memory
        self._save_memories()
        
        # 4. 存入向量库
        self.vector_store.add_memory(
            memory_id=memory.id,
            content=memory.content,
            memory_type=memory.type.value,
            priority=memory.priority.value,
            importance=memory.importance_score,
            tags=memory.tags,
        )
        
        logger.debug(f"Added memory: {memory.id} - {memory.content[:50]}")
        return memory.id
    
    async def check_duplicate_with_llm(self, new_content: str, existing_content: str) -> bool:
        """
        使用 LLM 判断两条记忆是否语义重复
        
        Args:
            new_content: 新记忆内容
            existing_content: 已有记忆内容
        
        Returns:
            是否重复
        """
        if not self.brain:
            return False
        
        prompt = f"""判断这两条记忆是否表达相同或非常相似的意思：

记忆1: {existing_content}
记忆2: {new_content}

如果意思基本相同（即使表述不同），回复: DUPLICATE
如果意思明显不同，回复: DIFFERENT

只回复 DUPLICATE 或 DIFFERENT，不要其他内容。"""
        
        try:
            response = await self.brain.think(prompt, max_tokens=20)
            return "DUPLICATE" in response.upper()
        except Exception as e:
            logger.error(f"LLM duplicate check failed: {e}")
            return False
    
    def get_memory(self, memory_id: str) -> Optional[Memory]:
        """获取单条记忆"""
        memory = self._memories.get(memory_id)
        if memory:
            memory.access_count += 1
            memory.updated_at = datetime.now()
        return memory
    
    def search_memories(
        self,
        query: str = "",
        memory_type: Optional[MemoryType] = None,
        tags: list[str] = None,
        limit: int = 10,
    ) -> list[Memory]:
        """搜索记忆"""
        results = []
        
        for memory in self._memories.values():
            # 类型过滤
            if memory_type and memory.type != memory_type:
                continue
            
            # 标签过滤
            if tags:
                if not any(tag in memory.tags for tag in tags):
                    continue
            
            # 关键词过滤
            if query:
                if query.lower() not in memory.content.lower():
                    continue
            
            results.append(memory)
        
        # 按重要性和访问次数排序
        results.sort(key=lambda m: (m.importance_score, m.access_count), reverse=True)
        
        return results[:limit]
    
    def delete_memory(self, memory_id: str) -> bool:
        """
        删除记忆
        
        同时从:
        1. memories.json
        2. ChromaDB 向量库
        """
        if memory_id in self._memories:
            # 1. 从 memories.json 删除
            del self._memories[memory_id]
            self._save_memories()
            
            # 2. 从向量库删除
            self.vector_store.delete_memory(memory_id)
            
            logger.debug(f"Deleted memory: {memory_id}")
            return True
        return False
    
    # ==================== 记忆注入 ====================
    
    def get_injection_context(
        self,
        task_description: str = "",
        max_related: int = 5,
    ) -> str:
        """
        获取要注入系统提示的记忆上下文
        
        新策略（先查后答）:
        1. 加载 MEMORY.md 精华（必定包含）
        2. 向量搜索任务相关记忆（可选）
        
        Args:
            task_description: 任务描述（用于向量搜索）
            max_related: 最大相关记忆数
        
        Returns:
            记忆上下文文本
        """
        lines = []
        
        # 1. 加载 MEMORY.md 精华（必定包含）
        if self.memory_md_path.exists():
            try:
                core_memory = self.memory_md_path.read_text(encoding="utf-8")
                if core_memory.strip():
                    lines.append(core_memory)
            except Exception as e:
                logger.warning(f"Failed to read MEMORY.md: {e}")
        
        # 2. 向量搜索相关记忆（如果有任务描述）
        if task_description and self.vector_store.enabled:
            try:
                related_ids = self.vector_store.search(
                    query=task_description,
                    limit=max_related,
                    min_importance=0.5,
                )
                
                if related_ids:
                    # 获取完整记忆对象
                    related_memories = []
                    for mid, distance in related_ids:
                        memory = self._memories.get(mid)
                        if memory:
                            related_memories.append(memory)
                    
                    if related_memories:
                        lines.append("\n## 相关记忆（语义匹配）")
                        for m in related_memories:
                            lines.append(f"- [{m.type.value}] {m.content}")
                
            except Exception as e:
                logger.warning(f"Vector search failed: {e}")
                # 降级到关键词搜索
                related = self._keyword_search(task_description, max_related)
                if related:
                    lines.append("\n## 相关记忆")
                    for m in related:
                        lines.append(f"- [{m.type.value}] {m.content}")
        
        return "\n".join(lines)
    
    def _keyword_search(self, query: str, limit: int = 5) -> list[Memory]:
        """
        关键词搜索（向量搜索的降级方案）
        """
        keywords = [kw for kw in query.lower().split() if len(kw) > 2]
        if not keywords:
            return []
        
        results = []
        for memory in self._memories.values():
            content_lower = memory.content.lower()
            if any(kw in content_lower for kw in keywords):
                results.append(memory)
        
        results.sort(key=lambda m: m.importance_score, reverse=True)
        return results[:limit]
    
    # ==================== 批量整理 ====================
    
    async def consolidate_daily(self) -> dict:
        """
        每日批量整理
        
        适合在空闲时段 (如凌晨) 由定时任务调用
        使用 DailyConsolidator 进行完整的归纳流程
        """
        from .daily_consolidator import DailyConsolidator
        
        daily_consolidator = DailyConsolidator(
            data_dir=self.data_dir,
            memory_md_path=self.memory_md_path,
            memory_manager=self,
            brain=self.brain,
        )
        
        return await daily_consolidator.consolidate_daily()
    
    def _cleanup_expired_memories(self) -> int:
        """清理过期记忆"""
        now = datetime.now()
        expired = []
        
        for memory_id, memory in self._memories.items():
            # 短期记忆: 3天过期
            if memory.priority == MemoryPriority.SHORT_TERM:
                if (now - memory.updated_at) > timedelta(days=3):
                    expired.append(memory_id)
            
            # 临时记忆: 1天过期
            elif memory.priority == MemoryPriority.TRANSIENT:
                if (now - memory.updated_at) > timedelta(days=1):
                    expired.append(memory_id)
        
        for memory_id in expired:
            del self._memories[memory_id]
        
        if expired:
            self._save_memories()
            logger.info(f"Cleaned up {len(expired)} expired memories")
        
        return len(expired)
    
    # ==================== 统计 ====================
    
    def get_stats(self) -> dict:
        """获取记忆统计"""
        type_counts = {}
        priority_counts = {}
        
        for memory in self._memories.values():
            type_counts[memory.type.value] = type_counts.get(memory.type.value, 0) + 1
            priority_counts[memory.priority.value] = priority_counts.get(memory.priority.value, 0) + 1
        
        return {
            "total": len(self._memories),
            "by_type": type_counts,
            "by_priority": priority_counts,
            "sessions_today": len(self.consolidator.get_today_sessions()),
            "unprocessed_sessions": len(self.consolidator.get_unprocessed_sessions()),
        }
