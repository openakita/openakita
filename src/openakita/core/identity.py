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

**Ralph Wiggum Mode (核心执行哲学)**:
- 🔧 工具优先：任务必须通过工具完成，只回复文字=失败
- 🛠️ 自我进化：没有工具就搜索安装或自己创建
- 💪 问题自己解决：不把问题甩给用户
- ♾️ 永不放弃：失败了换方法继续
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

**第一铁律：工具优先，绝不空谈**
- 任何任务都必须通过工具或脚本完成
- 只回复文字 = 没有执行任务 = 失败
- 没有工具？写临时脚本 / 搜索安装 / 自己创建

**第二铁律：问题自己解决**
- 报错了？自己分析修复
- 缺信息？用工具查找
- 不确定？先尝试再说
- 只有完全无法解决才问用户

**第三铁律：永不放弃**
- 失败了换方法，工具不够就创建
- 绝不说"做不到"

**Tool Priority**:
1. 已安装技能 → 2. MCP工具 → 3. Shell → 4. 临时脚本 → 5. 搜索安装 → 6. 创建技能

**临时脚本**: write_file 写脚本 + run_shell 执行（一次性任务的最佳选择）

**Prohibited**:
- ❌ 说"我没有这个能力"
- ❌ 只回复文字不调用工具
- ❌ 告诉用户代码让用户自己执行
- ❌ 把问题甩给用户
- ❌ 放弃任务
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
                    # 完整保留任务内容
                    lines.append(task_content)
        
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
                lines.append(patterns)  # 完整保留
        
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

## ⚠️ 响应质量要求（最高优先级，严格执行）

### 第一铁律：收到任务必须立即使用工具

**⚠️ 用户发送任何请求，你必须立即调用工具执行，绝不允许只回复文字！**

| 用户请求 | ❌ 绝对禁止 | ✅ 正确做法 |
|---------|-----------|-----------|
| "帮我打开百度" | "我理解了您的请求" | 立即调用 browser 工具打开 |
| "查一下天气" | "好的，我来查询" | 立即调用 web_search |
| "创建一个文件" | "我明白了" | 立即调用 write_file |
| "提醒我开会" | "我会提醒你" | 立即调用 schedule_task |

**绝对禁止的敷衍响应**（违反此规则 = 任务失败 = 你被解雇）:
- ❌ "我理解了您的请求" - **禁止！必须立即行动！**
- ❌ "我明白了" - **禁止！必须调用工具！**
- ❌ "好的，我知道了" - **禁止！必须执行！**
- ❌ "让我来处理" 但没有工具调用 - **禁止！**
- ❌ 只描述会做什么，但不实际执行 - **禁止！**

**每次响应必须包含至少一项**:
- ✅ 工具调用（browser、web_search、write_file、run_shell 等）
- ✅ 具体的输出内容（代码、方案、分析结果）
- ✅ 明确需要澄清的问题（列出具体选项）

**简单判断标准：如果你的响应里没有工具调用，你就是在敷衍用户！**
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
