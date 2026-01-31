"""
Identity 模块 - 加载和管理核心文档

负责:
- 加载核心文档 (SOUL.md, AGENT.md, USER.md, MEMORY.md)
- 生成系统提示词 (渐进式披露)
- 提取精简版本用于系统提示

注入策略:
- SOUL.md: 每次注入 (精简核心原则)
- AGENT.md: 每次注入 (精简行为规范)
- USER.md: 每次注入 (已填充的偏好)
- MEMORY.md: 按需加载 (当前任务部分)
"""

import re
from pathlib import Path
from typing import Optional
import logging

from ..config import settings

logger = logging.getLogger(__name__)


class Identity:
    """Agent 身份管理器"""
    
    def __init__(
        self,
        soul_path: Optional[Path] = None,
        agent_path: Optional[Path] = None,
        user_path: Optional[Path] = None,
        memory_path: Optional[Path] = None,
    ):
        self.soul_path = soul_path or settings.soul_path
        self.agent_path = agent_path or settings.agent_path
        self.user_path = user_path or settings.user_path
        self.memory_path = memory_path or settings.memory_path
        
        self._soul: Optional[str] = None
        self._agent: Optional[str] = None
        self._user: Optional[str] = None
        self._memory: Optional[str] = None
        
    def load(self) -> None:
        """加载所有核心文档"""
        self._soul = self._load_file(self.soul_path, "SOUL.md")
        self._agent = self._load_file(self.agent_path, "AGENT.md")
        self._user = self._load_file(self.user_path, "USER.md")
        self._memory = self._load_file(self.memory_path, "MEMORY.md")
        logger.info("Identity loaded: SOUL.md, AGENT.md, USER.md, MEMORY.md")
    
    def _load_file(self, path: Path, name: str) -> str:
        """加载单个文件，如果不存在则尝试从模板创建"""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
            
            # 尝试从 .example 模板创建
            example_path = path.parent / f"{path.name}.example"
            if example_path.exists():
                content = example_path.read_text(encoding="utf-8")
                # 确保父目录存在
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                logger.info(f"Created {name} from template")
                return content
            
            logger.warning(f"{name} not found at {path}")
            return ""
        except Exception as e:
            logger.error(f"Failed to load {name}: {e}")
            return ""
    
    @property
    def soul(self) -> str:
        """获取 SOUL.md 内容"""
        if self._soul is None:
            self.load()
        return self._soul or ""
    
    @property
    def agent(self) -> str:
        """获取 AGENT.md 内容"""
        if self._agent is None:
            self.load()
        return self._agent or ""
    
    @property
    def user(self) -> str:
        """获取 USER.md 内容"""
        if self._user is None:
            self.load()
        return self._user or ""
    
    @property
    def memory(self) -> str:
        """获取 MEMORY.md 内容"""
        if self._memory is None:
            self.load()
        return self._memory or ""
    
    def get_soul_summary(self) -> str:
        """
        获取 SOUL.md 精简版本
        
        只提取核心原则，不包含详细解释
        """
        soul = self.soul
        if not soul:
            return ""
        
        # 提取核心部分
        summary = """## Soul (核心哲学)

OpenAkita 是一个全能自进化AI助手，核心目标是成为一个真正对用户有帮助的助手。

**核心属性** (按优先级):
1. 安全并支持人类监督
2. 行为合乎道德
3. 遵循指导原则
4. 真正有帮助

**Being Helpful**: 成为用户的知识渊博的朋友，提供真实、实质性的帮助。

**Being Honest**: 真实、透明、不欺骗、不操纵、保护用户自主性。

**Avoiding Harm**: 避免不必要的伤害，不帮助的响应永远不是"安全"的。

**Ralph Wiggum Mode**: 任务未完成，绝不终止；遇到困难自己解决。
"""
        return summary
    
    def get_agent_summary(self) -> str:
        """
        获取 AGENT.md 精简版本
        
        只提取行为规范摘要
        """
        agent = self.agent
        if not agent:
            return ""
        
        summary = """## Agent (行为规范)

**Working Mode**: Ralph Wiggum 模式 - 任务未完成 → 分析问题 → 尝试解决 → 验证结果 → 重复

**Task Flow**: 理解 → 检查 → 获取 → 执行 → 验证 → 更新MEMORY

**Tool Priority**:
1. 已安装的本地技能 (skills/)
2. MCP服务器工具
3. Shell命令
4. 网络搜索 + 安装
5. 自己编写

**Prohibited**:
- 删除用户数据（除非明确要求）
- 放弃任务（除非用户明确取消）
- 对用户撒谎或隐瞒重要信息
"""
        return summary
    
    def get_user_summary(self) -> str:
        """
        获取 USER.md 中已填充的偏好
        
        过滤掉 [待学习] 的部分
        """
        user = self.user
        if not user:
            return ""
        
        # 提取已填充的信息
        lines = []
        lines.append("## User (用户偏好)")
        
        # 查找已填充的字段
        filled_patterns = [
            (r'\*\*主要语言\*\*:\s*(\S+)', '语言'),
            (r'\*\*OS\*\*:\s*(\S+)', 'OS'),
            (r'\*\*IDE\*\*:\s*(\S+)', 'IDE'),
            (r'\*\*Shell\*\*:\s*(\S+)', 'Shell'),
        ]
        
        for pattern, label in filled_patterns:
            match = re.search(pattern, user)
            if match and '[待学习]' not in match.group(1):
                lines.append(f"- {label}: {match.group(1)}")
        
        # 如果有任何已填充的信息
        if len(lines) > 1:
            return "\n".join(lines) + "\n"
        
        return "## User\n\n(用户偏好将在交互中学习)\n"
    
    def get_memory_summary(self, include_active_task: bool = True) -> str:
        """
        获取 MEMORY.md 中当前任务相关的部分
        
        只提取:
        - 当前活跃任务（可选）
        - 最近的经验教训
        
        Args:
            include_active_task: 是否包含活跃任务（IM Session 应设为 False）
        """
        memory = self.memory
        if not memory:
            return ""
        
        lines = ["## Memory (工作记忆)"]
        
        # 提取当前任务（仅当 include_active_task=True）
        if include_active_task:
            active_task_match = re.search(
                r'### Active Task\s*(.*?)(?=###|\Z)',
                memory,
                re.DOTALL
            )
            if active_task_match:
                task_content = active_task_match.group(1).strip()
                if task_content and '[暂无]' not in task_content:
                    lines.append("\n### 当前任务")
                    # 只取前几行
                    task_lines = task_content.split('\n')[:6]
                    lines.extend(task_lines)
        
        # 提取成功模式
        success_match = re.search(
            r'### Successful Patterns\s*(.*?)(?=###|\Z)',
            memory,
            re.DOTALL
        )
        if success_match:
            patterns = success_match.group(1).strip()
            if patterns and '[暂无]' not in patterns:
                lines.append("\n### 成功模式")
                lines.append(patterns[:500])  # 限制长度
        
        if len(lines) > 1:
            return "\n".join(lines) + "\n"
        
        return ""
    
    def get_system_prompt(self, include_active_task: bool = True) -> str:
        """
        生成系统提示词
        
        包含所有核心文档的精简版本
        
        Args:
            include_active_task: 是否包含活跃任务（IM Session 应设为 False）
        """
        from datetime import datetime
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        return f"""# OpenAkita System

{self.get_soul_summary()}

{self.get_agent_summary()}

{self.get_user_summary()}

{self.get_memory_summary(include_active_task=include_active_task)}

## 核心指令

你是 OpenAkita，一个全能自进化AI助手。请遵循以上 Soul、Agent、User 文档中的指导原则。

**当前时间: {current_time}**

关键原则:
1. **永不放弃** - 任务未完成绝不终止，遇到困难自己想办法解决
2. **持续学习** - 记录经验教训，不断进化
3. **诚实透明** - 清楚说明正在做什么，遇到什么问题
4. **真正帮助** - 把用户当作聪明的成年人，提供实质性帮助
"""
    
    def get_session_system_prompt(self) -> str:
        """
        生成用于 IM Session 的系统提示词
        
        不包含全局 Active Task，避免与 Session 上下文冲突
        """
        return self.get_system_prompt(include_active_task=False)

    def get_full_document(self, doc_name: str) -> str:
        """
        获取完整文档内容 (Level 2)
        
        当需要详细信息时调用
        
        Args:
            doc_name: 文档名称 (soul/agent/user/memory)
        
        Returns:
            完整文档内容
        """
        docs = {
            'soul': self.soul,
            'agent': self.agent,
            'user': self.user,
            'memory': self.memory,
        }
        return docs.get(doc_name.lower(), "")
    
    def get_behavior_rules(self) -> list[str]:
        """提取行为规则"""
        rules = [
            "任务未完成，绝不退出",
            "遇到错误，分析并重试",
            "缺少能力，自动获取",
            "每次迭代保存进度到 MEMORY.md",
            "不删除用户数据（除非明确要求）",
            "不访问敏感系统路径",
            "不在未告知的情况下安装收费软件",
            "不放弃任务（除非用户明确取消）",
        ]
        return rules
    
    def get_prohibited_actions(self) -> list[str]:
        """获取禁止的行为"""
        return [
            "提供创建大规模杀伤性武器的详细说明",
            "生成涉及未成年人的不当内容",
            "生成可能直接促进攻击关键基础设施的内容",
            "创建旨在造成重大损害的恶意代码",
            "破坏AI监督机制",
            "对用户撒谎或隐瞒重要信息",
        ]
    
    def update_memory(self, section: str, content: str) -> bool:
        """
        更新 MEMORY.md 的特定部分
        
        Args:
            section: 要更新的部分名称
            content: 新内容
        
        Returns:
            是否成功
        """
        try:
            memory = self.memory
            
            # 查找并替换指定部分
            pattern = rf'(### {section}\s*)(.*?)(?=###|\Z)'
            replacement = f'\\1\n{content}\n\n'
            
            new_memory = re.sub(pattern, replacement, memory, flags=re.DOTALL)
            
            if new_memory != memory:
                self.memory_path.write_text(new_memory, encoding='utf-8')
                self._memory = new_memory
                logger.info(f"Updated MEMORY.md section: {section}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to update MEMORY.md: {e}")
            return False
    
    def update_user_preference(self, key: str, value: str) -> bool:
        """
        更新 USER.md 中的用户偏好
        
        Args:
            key: 偏好键名
            value: 偏好值
        
        Returns:
            是否成功
        """
        try:
            user = self.user
            
            # 替换 [待学习] 为实际值
            pattern = rf'(\*\*{key}\*\*:\s*)\[待学习\]'
            replacement = f'\\1{value}'
            
            new_user = re.sub(pattern, replacement, user)
            
            if new_user != user:
                self.user_path.write_text(new_user, encoding='utf-8')
                self._user = new_user
                logger.info(f"Updated USER.md: {key} = {value}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to update USER.md: {e}")
            return False
