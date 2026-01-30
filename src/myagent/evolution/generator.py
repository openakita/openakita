"""
技能生成器

使用 LLM 自动生成符合 Agent Skills 规范 (SKILL.md) 的技能。
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.brain import Brain
from ..skills.registry import SkillRegistry
from ..skills.loader import SkillLoader
from ..tools.file import FileTool
from ..tools.shell import ShellTool
from ..config import settings

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """生成结果"""
    success: bool
    skill_name: str
    skill_dir: Optional[str] = None
    error: Optional[str] = None
    test_passed: bool = False


class SkillGenerator:
    """
    技能生成器
    
    使用 LLM 根据描述自动生成符合 Agent Skills 规范的技能。
    
    生成的技能结构:
    skills/<skill-name>/
    ├── SKILL.md          # 技能定义 (必需)
    ├── scripts/          # 可执行脚本 (可选)
    │   └── main.py
    └── references/       # 参考文档 (可选)
        └── REFERENCE.md
    """
    
    SKILL_MD_TEMPLATE = '''---
name: {name}
description: |
  {description}
license: MIT
metadata:
  author: myagent-generator
  version: "1.0.0"
---

# {title}

{body}

## When to Use

{when_to_use}

## Instructions

{instructions}
'''

    SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""
{name} - {description}

用法:
    python {script_name} [options]
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="{description}")
    # 添加参数
    {args_code}
    
    args = parser.parse_args()
    
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}, ensure_ascii=False))
        sys.exit(1)


def execute(args):
    """
    执行主逻辑
    
    Args:
        args: 命令行参数
    
    Returns:
        结果字典
    """
    {execute_code}


if __name__ == "__main__":
    main()
'''

    def __init__(
        self,
        brain: Brain,
        skills_dir: Optional[Path] = None,
        skill_registry: Optional[SkillRegistry] = None,
    ):
        self.brain = brain
        self.skills_dir = skills_dir or settings.skills_path
        self.registry = skill_registry if skill_registry is not None else SkillRegistry()
        self.loader = SkillLoader(self.registry)
        self.file_tool = FileTool()
        self.shell = ShellTool()
    
    async def generate(self, description: str, name: Optional[str] = None) -> GenerationResult:
        """
        生成技能
        
        Args:
            description: 技能功能描述
            name: 技能名称（可选，自动生成）
        
        Returns:
            GenerationResult
        """
        logger.info(f"Generating skill: {description[:100]}...")
        
        # 1. 生成技能名称
        if not name:
            name = await self._generate_name(description)
        
        # 确保名称格式正确 (lowercase, hyphens)
        name = self._normalize_name(name)
        
        # 2. 检查是否已存在
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            logger.warning(f"Skill directory already exists: {skill_dir}")
            # 可以选择覆盖或返回错误
        
        # 3. 创建目录结构
        skill_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        
        # 4. 生成 SKILL.md
        skill_md_content = await self._generate_skill_md(name, description)
        skill_md_path = skill_dir / "SKILL.md"
        await self.file_tool.write(str(skill_md_path), skill_md_content)
        
        # 5. 生成脚本
        script_content = await self._generate_script(name, description)
        script_path = scripts_dir / "main.py"
        await self.file_tool.write(str(script_path), script_content)
        
        # 6. 测试脚本
        test_passed = await self._test_script(script_path)
        
        if not test_passed:
            # 尝试修复
            logger.info("Initial test failed, attempting to fix...")
            fixed_script = await self._fix_script(script_content, name, description)
            if fixed_script:
                await self.file_tool.write(str(script_path), fixed_script)
                test_passed = await self._test_script(script_path)
        
        # 7. 加载技能到 registry
        if test_passed:
            try:
                loaded = self.loader.load_skill(skill_dir)
                if loaded:
                    logger.info(f"Skill loaded successfully: {name}")
            except Exception as e:
                logger.error(f"Failed to load generated skill: {e}")
        
        return GenerationResult(
            success=test_passed,
            skill_name=name,
            skill_dir=str(skill_dir),
            test_passed=test_passed,
        )
    
    def _normalize_name(self, name: str) -> str:
        """标准化技能名称 (lowercase, hyphens only)"""
        # 转小写
        name = name.lower()
        # 替换空格和下划线为连字符
        name = name.replace("_", "-").replace(" ", "-")
        # 只保留小写字母、数字和连字符
        name = re.sub(r"[^a-z0-9-]", "", name)
        # 去除连续连字符
        name = re.sub(r"-+", "-", name)
        # 去除首尾连字符
        name = name.strip("-")
        
        if not name:
            name = "custom-skill"
        
        return name
    
    async def _generate_name(self, description: str) -> str:
        """使用 LLM 生成技能名称"""
        prompt = f"""为以下功能生成一个简短的技能名称（使用小写字母和连字符，如 datetime-tool, file-manager）:

{description}

只返回名称，不要解释。"""

        response = await self.brain.think(prompt)
        return response.content.strip()
    
    async def _generate_skill_md(self, name: str, description: str) -> str:
        """生成 SKILL.md 内容"""
        prompt = f'''为以下技能生成 SKILL.md 的内容部分（不包括 YAML frontmatter）:

技能名称: {name}
功能描述: {description}

请生成:
1. 标题和简介
2. "When to Use" 部分（列出使用场景）
3. "Instructions" 部分（使用说明，包括如何运行脚本）

脚本路径是 `scripts/main.py`，使用 `python scripts/main.py [args]` 运行。

只返回 Markdown 内容，不要包含 frontmatter。'''

        response = await self.brain.think(prompt)
        body_content = response.content.strip()
        
        # 组装完整的 SKILL.md
        title = name.replace("-", " ").title()
        
        # 解析 LLM 生成的内容，提取各部分
        when_to_use = "- 见上述描述"
        instructions = "运行 `python scripts/main.py --help` 查看帮助"
        
        # 尝试从响应中提取
        if "## When to Use" in body_content:
            parts = body_content.split("## When to Use")
            if len(parts) > 1:
                intro = parts[0].strip()
                rest = parts[1]
                if "## Instructions" in rest:
                    wu_parts = rest.split("## Instructions")
                    when_to_use = wu_parts[0].strip()
                    instructions = wu_parts[1].strip() if len(wu_parts) > 1 else instructions
                else:
                    when_to_use = rest.strip()
                body_content = intro
        
        return self.SKILL_MD_TEMPLATE.format(
            name=name,
            description=description.replace("\n", "\n  "),  # YAML 多行缩进
            title=title,
            body=body_content if body_content else f"提供 {description} 的功能。",
            when_to_use=when_to_use,
            instructions=instructions,
        )
    
    async def _generate_script(self, name: str, description: str) -> str:
        """生成 Python 脚本"""
        prompt = f'''请生成一个 Python 脚本来实现以下功能:

技能名称: {name}
功能描述: {description}

要求:
1. 使用 argparse 处理命令行参数
2. 输出 JSON 格式的结果
3. 包含完整的错误处理
4. 包含 docstring 和类型提示
5. 脚本应该可以独立运行

模板结构:
```python
#!/usr/bin/env python3
"""
{name} - 脚本描述
"""

import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser(description="...")
    # 添加参数
    args = parser.parse_args()
    
    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
        sys.exit(1)

def execute(args):
    """执行主逻辑"""
    # 实现逻辑
    return {{"success": True, "data": ...}}

if __name__ == "__main__":
    main()
```

请生成完整的代码。只输出代码，不要解释。'''

        response = await self.brain.think(prompt)
        
        # 提取代码
        code = response.content
        if "```python" in code:
            start = code.find("```python") + 9
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        elif "```" in code:
            start = code.find("```") + 3
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        
        return code
    
    async def _test_script(self, script_path: Path) -> bool:
        """测试脚本"""
        logger.info(f"Testing script: {script_path}")
        
        # 语法检查
        result = await self.shell.run(f"python -m py_compile \"{script_path}\"")
        
        if not result.success:
            logger.error(f"Syntax error: {result.stderr}")
            return False
        
        # 尝试运行 --help
        result = await self.shell.run(f"python \"{script_path}\" --help")
        
        if not result.success:
            logger.error(f"Script error: {result.output}")
            return False
        
        logger.info("Script test passed")
        return True
    
    async def _fix_script(self, code: str, name: str, description: str) -> Optional[str]:
        """尝试修复脚本错误"""
        prompt = f"""以下 Python 脚本有错误，请修复:

```python
{code}
```

技能名称: {name}
功能描述: {description}

要求:
1. 修复所有语法错误
2. 修复导入错误
3. 确保可以运行 --help
4. 保持原有功能
5. 输出 JSON 格式

只输出修复后的完整代码，不要解释。"""

        response = await self.brain.think(prompt)
        
        fixed_code = response.content
        if "```python" in fixed_code:
            start = fixed_code.find("```python") + 9
            end = fixed_code.find("```", start)
            if end > start:
                fixed_code = fixed_code[start:end].strip()
        
        return fixed_code
    
    async def improve(self, skill_name: str, feedback: str) -> GenerationResult:
        """
        根据反馈改进技能
        
        Args:
            skill_name: 技能名称
            feedback: 改进反馈
        
        Returns:
            GenerationResult
        """
        skill_dir = self.skills_dir / skill_name
        
        if not skill_dir.exists():
            return GenerationResult(
                success=False,
                skill_name=skill_name,
                error="技能目录不存在",
            )
        
        script_path = skill_dir / "scripts" / "main.py"
        if not script_path.exists():
            return GenerationResult(
                success=False,
                skill_name=skill_name,
                error="脚本文件不存在",
            )
        
        current_code = await self.file_tool.read(str(script_path))
        
        prompt = f"""请根据反馈改进以下技能脚本:

当前代码:
```python
{current_code}
```

反馈:
{feedback}

请输出改进后的完整代码，不要解释。"""

        response = await self.brain.think(prompt)
        
        improved_code = response.content
        if "```python" in improved_code:
            start = improved_code.find("```python") + 9
            end = improved_code.find("```", start)
            if end > start:
                improved_code = improved_code[start:end].strip()
        
        # 保存并测试
        await self.file_tool.write(str(script_path), improved_code)
        test_passed = await self._test_script(script_path)
        
        return GenerationResult(
            success=test_passed,
            skill_name=skill_name,
            skill_dir=str(skill_dir),
            test_passed=test_passed,
        )
