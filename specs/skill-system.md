# Skill System Specification

## Overview

技能系统允许 OpenAkita 动态加载、执行和管理技能。

## Jobs to Be Done

1. 定义技能的标准接口
2. 注册和发现技能
3. 动态加载技能
4. 从 GitHub 搜索和安装技能
5. 自动生成新技能

## Components

### BaseSkill (`skills/base.py`)

所有技能的基类。

```python
class BaseSkill(ABC):
    name: str
    description: str
    version: str
    
    @abstractmethod
    async def execute(self, **kwargs) -> SkillResult
    
    def get_schema(self) -> dict  # JSON Schema for parameters
```

### SkillRegistry (`skills/registry.py`)

技能注册和发现。

```python
class SkillRegistry:
    skills: dict[str, BaseSkill]
    
    def register(self, skill: BaseSkill)
    def get(self, name: str) -> BaseSkill | None
    def list_all(self) -> list[BaseSkill]
    def search(self, query: str) -> list[BaseSkill]
```

### SkillLoader (`skills/loader.py`)

动态加载技能。

```python
class SkillLoader:
    def load_from_file(self, path: str) -> BaseSkill
    def load_from_directory(self, dir: str) -> list[BaseSkill]
    def load_from_package(self, package: str) -> BaseSkill
```

### SkillMarket (`skills/market.py`)

从 GitHub 搜索和安装技能。

```python
class SkillMarket:
    async def search(self, query: str) -> list[SkillInfo]
    async def install(self, url: str) -> BaseSkill
    async def update(self, name: str) -> BaseSkill
```

### SkillGenerator (`skills/generator.py`)

自动生成技能代码。

```python
class SkillGenerator:
    async def generate(self, description: str) -> str  # Returns code
    async def test(self, code: str) -> TestResult
    async def save(self, code: str, name: str) -> str  # Returns path
```

## Acceptance Criteria

- [ ] 可以定义和注册新技能
- [ ] 可以动态加载 skills/ 目录下的技能
- [ ] 可以从 GitHub 搜索技能
- [ ] 可以安装 GitHub 上的技能
- [ ] 可以自动生成技能代码
- [ ] 生成的技能会自动测试

