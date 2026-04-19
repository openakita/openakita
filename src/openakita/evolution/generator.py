"""
Skill Generator

Automatically generates skills compliant with Agent Skills specifications (SKILL.md) using LLM.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import settings
from ..core.brain import Brain
from ..skills.loader import SkillLoader
from ..skills.registry import SkillRegistry
from ..tools.file import FileTool
from ..tools.shell import ShellTool

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """Generation result"""

    success: bool
    skill_name: str
    skill_dir: str | None = None
    error: str | None = None
    test_passed: bool = False


class SkillGenerator:
    """
    Skill Generator

    Automatically generates skills compliant with Agent Skills specifications using LLM based on descriptions.

    Generated Skill Structure:
    skills/<skill-name>/
    ├── SKILL.md          # Skill definition (Required)
    ├── scripts/          # Executable scripts (Optional)
    │   └── main.py
    └── references/       # Reference documents (Optional)
        └── REFERENCE.md
    """

    SKILL_MD_TEMPLATE = """---
name: {name}
description: |
  {description}
license: MIT
metadata:
  author: openakita-generator
  version: "1.0.0"
---

# {title}

{body}

## When to Use

{when_to_use}

## Instructions

{instructions}
"""

    SCRIPT_TEMPLATE = '''#!/usr/bin/env python3
"""
{name} - {description}

Usage:
    python {script_name} [options]
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description="{description}")
    # Add parameters
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
    Execute main logic

    Args:
        args: Command line arguments

    Returns:
        Result dictionary
    """
    {execute_code}


if __name__ == "__main__":
    main()
'''

    def __init__(
        self,
        brain: Brain,
        skills_dir: Path | None = None,
        skill_registry: SkillRegistry | None = None,
    ):
        self.brain = brain
        self.skills_dir = skills_dir or settings.skills_path
        self.registry = skill_registry if skill_registry is not None else SkillRegistry()
        self.loader = SkillLoader(self.registry)
        self.file_tool = FileTool()
        self.shell = ShellTool()

    async def generate(self, description: str, name: str | None = None) -> GenerationResult:
        """
        Generate a skill

        Args:
            description: Description of the skill functionality
            name: Skill name (optional, auto-generated)

        Returns:
            GenerationResult
        """
        logger.info(f"Generating skill: {description}")

        # 1. Generate skill name
        if not name:
            name = await self._generate_name(description)

        # Ensure name format is correct (lowercase, hyphens)
        name = self._normalize_name(name)

        # 2. Check if it already exists
        skill_dir = self.skills_dir / name
        if skill_dir.exists():
            logger.warning(f"Skill directory already exists: {skill_dir}")
            # Can choose to overwrite or return an error

        # 3. Create directory structure
        skill_dir.mkdir(parents=True, exist_ok=True)
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)

        # 4. Generate SKILL.md
        skill_md_content = await self._generate_skill_md(name, description)
        skill_md_path = skill_dir / "SKILL.md"
        await self.file_tool.write(str(skill_md_path), skill_md_content)

        # 4.5 Generate i18n translations (agents/openai.yaml)
        await self._generate_i18n(skill_dir, name, description)

        # 5. Generate script
        script_content = await self._generate_script(name, description)
        script_path = scripts_dir / "main.py"
        await self.file_tool.write(str(script_path), script_content)

        # 6. Test script
        test_passed = await self._test_script(script_path)

        if not test_passed:
            # Attempt to fix
            logger.info("Initial test failed, attempting to fix...")
            fixed_script = await self._fix_script(script_content, name, description)
            if fixed_script:
                await self.file_tool.write(str(script_path), fixed_script)
                test_passed = await self._test_script(script_path)

        # 7. Load skill into registry
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
        """Normalize skill name (lowercase, hyphens only)"""
        # Convert to lowercase
        name = name.lower()
        # Replace spaces and underscores with hyphens
        name = name.replace("_", "-").replace(" ", "-")
        # Keep only lowercase letters, digits, and hyphens
        name = re.sub(r"[^a-z0-9-]", "", name)
        # Collapse consecutive hyphens
        name = re.sub(r"-+", "-", name)
        # Strip leading/trailing hyphens
        name = name.strip("-")

        if not name:
            name = "custom-skill"

        return name

    async def _generate_name(self, description: str) -> str:
        """Use LLM to generate a skill name"""
        prompt = f"""Generate a short skill name for the following functionality (use lowercase letters and hyphens, e.g., datetime-tool, file-manager):

{description}

Return ONLY the name, no explanation."""

        response = await self.brain.think(prompt)
        return response.content.strip()

    async def _generate_i18n(self, skill_dir: Path, name: str, description: str) -> None:
        """Generate i18n translations and write to agents/openai.yaml i18n fields."""
        try:
            from ..skills.i18n import auto_translate_skill

            await auto_translate_skill(skill_dir, name, description, self.brain)
        except Exception as e:
            logger.warning(f"Failed to generate i18n for {name}: {e}")

    async def _generate_skill_md(self, name: str, description: str) -> str:
        """Generate SKILL.md content"""
        prompt = f"""Generate the content part of SKILL.md for the following skill (excluding YAML frontmatter):

Skill Name: {name}
Function Description: {description}

Please generate:
1. Title and introduction
2. "When to Use" section (list usage scenarios)
3. "Instructions" section (usage instructions, including how to run the script)

The script path is `scripts/main.py`, run it using `python scripts/main.py [args]`.

Return ONLY the Markdown content, do not include frontmatter."""

        response = await self.brain.think(prompt)
        body_content = response.content.strip()

        # Assemble the complete SKILL.md
        title = name.replace("-", " ").title()

        # Parse the LLM-generated content and extract sections
        when_to_use = "- See the description above"
        instructions = "Run `python scripts/main.py --help` for usage information"

        # Try to extract from response
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
            description=description.replace("\n", "\n  "),  # YAML multiline indentation
            title=title,
            body=body_content if body_content else f"Provides {description} functionality.",
            when_to_use=when_to_use,
            instructions=instructions,
        )

    async def _generate_script(self, name: str, description: str) -> str:
        """Generate Python script"""
        prompt = f'''Please generate a Python script to implement the following functionality:

Skill Name: {name}
Function Description: {description}

Requirements:
1. Use argparse to handle command-line arguments
2. Output results in JSON format
3. Include comprehensive error handling
4. Include docstrings and type hints
5. The script should be runnable independently

Template Structure:
```python
#!/usr/bin/env python3
"""
{name} - Script description
"""

import argparse
import json
import sys

def main():
    parser = argparse.ArgumentParser(description="...")
    # Add parameters
    args = parser.parse_args()

    try:
        result = execute(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))
        sys.exit(1)

def execute(args):
    """Execute main logic"""
    # Implementation logic
    return {{"success": True, "data": ...}}

if __name__ == "__main__":
    main()
```

Generate the complete code. Return ONLY the code, no explanation.'''

        response = await self.brain.think(prompt)

        # Extract code
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
        """Test script"""
        logger.info(f"Testing script: {script_path}")

        # Syntax check
        result = await self.shell.run(f'python -m py_compile "{script_path}"')

        if not result.success:
            logger.error(f"Syntax error: {result.stderr}")
            return False

        # Try running --help
        result = await self.shell.run(f'python "{script_path}" --help')

        if not result.success:
            logger.error(f"Script error: {result.output}")
            return False

        logger.info("Script test passed")
        return True

    async def _fix_script(self, code: str, name: str, description: str) -> str | None:
        """Attempt to fix script errors"""
        prompt = f"""The following Python script has errors, please fix them:

```python
{code}
```

Skill Name: {name}
Function Description: {description}

Requirements:
1. Fix all syntax errors
2. Fix import errors
3. Ensure --help can be run
4. Maintain original functionality
5. Output in JSON format

Return ONLY the complete fixed code, no explanation."""

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
        Improve a skill based on feedback

        Args:
            skill_name: Skill name
            feedback: Improvement feedback

        Returns:
            GenerationResult
        """
        skill_dir = self.skills_dir / skill_name

        if not skill_dir.exists():
            return GenerationResult(
                success=False,
                skill_name=skill_name,
                error="Skill directory does not exist",
            )

        script_path = skill_dir / "scripts" / "main.py"
        if not script_path.exists():
            return GenerationResult(
                success=False,
                skill_name=skill_name,
                error="Script file does not exist",
            )

        current_code = await self.file_tool.read(str(script_path))

        prompt = f"""Please improve the following skill script based on the feedback:

Current Code:
```python
{current_code}
```

Feedback:
{feedback}

Return ONLY the complete improved code, no explanation."""

        response = await self.brain.think(prompt)

        improved_code = response.content
        if "```python" in improved_code:
            start = improved_code.find("```python") + 9
            end = improved_code.find("```", start)
            if end > start:
                improved_code = improved_code[start:end].strip()

        # Save and test
        await self.file_tool.write(str(script_path), improved_code)
        test_passed = await self._test_script(script_path)

        return GenerationResult(
            success=test_passed,
            skill_name=skill_name,
            skill_dir=str(skill_dir),
            test_passed=test_passed,
        )
