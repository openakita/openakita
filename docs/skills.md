# Skills System

OpenAkita's skill system enables dynamic capability extension.

## Overview

Skills are modular capabilities that can be:
- Loaded from local files
- Downloaded from GitHub
- Generated dynamically by the agent

## Skill Structure

### Basic Skill Template

```python
# skills/my_skill.py
from openakita.skills.base import BaseSkill, SkillResult

class MySkill(BaseSkill):
    """A custom skill for OpenAkita."""
    
    name = "my_skill"
    description = "Does something useful"
    version = "1.0.0"
    
    # Required dependencies
    dependencies = ["requests>=2.28.0"]
    
    async def execute(self, **kwargs) -> SkillResult:
        """Execute the skill logic."""
        try:
            # Your implementation here
            result = await self.do_something(kwargs.get("input"))
            return SkillResult(success=True, data=result)
        except Exception as e:
            return SkillResult(success=False, error=str(e))
    
    async def do_something(self, input_data):
        # Implementation
        return f"Processed: {input_data}"
```

### Skill Metadata

```python
class MySkill(BaseSkill):
    # Required
    name = "my_skill"           # Unique identifier
    description = "..."         # What it does
    version = "1.0.0"           # Semantic version
    
    # Optional
    author = "Your Name"
    tags = ["utility", "text"]
    dependencies = []           # PyPI packages
    examples = [
        {"input": "test", "output": "Processed: test"}
    ]
```

## Using Skills

### From CLI

```bash
# List available skills
openakita skills list

# Run a skill directly
openakita skills run my_skill --input "test data"

# Install a skill from GitHub
openakita skills install github:user/repo/skill_name
```

### From Conversation

```
You> Use the excel_reader skill to analyze data.xlsx
Agent> Using excel_reader skill...
```

### Programmatically

```python
from openakita.skills import SkillRegistry

registry = SkillRegistry()
skill = registry.get("my_skill")
result = await skill.execute(input="test")
```

## Skill Categories

### Built-in Skills

| Skill | Description |
|-------|-------------|
| `file_manager` | Advanced file operations |
| `code_analyzer` | Code analysis and refactoring |
| `data_processor` | Data transformation |

### Community Skills

Community skills can be found at:
- GitHub topics: `openakita-skill`
- Skills marketplace (coming soon)

## Creating Skills

### Step 1: Create Skill File

```python
# skills/weather_skill.py
from openakita.skills.base import BaseSkill, SkillResult
import httpx

class WeatherSkill(BaseSkill):
    name = "weather"
    description = "Get weather information for a location"
    version = "1.0.0"
    dependencies = ["httpx>=0.27.0"]
    
    async def execute(self, location: str = "Beijing") -> SkillResult:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://wttr.in/{location}?format=j1"
                )
                data = resp.json()
                weather = data["current_condition"][0]
                return SkillResult(
                    success=True,
                    data={
                        "location": location,
                        "temperature": weather["temp_C"],
                        "condition": weather["weatherDesc"][0]["value"]
                    }
                )
        except Exception as e:
            return SkillResult(success=False, error=str(e))
```

### Step 2: Register Skill

Skills in the `skills/` directory are auto-registered.

For external skills:

```python
from openakita.skills import SkillRegistry

registry = SkillRegistry()
registry.register(WeatherSkill())
```

### Step 3: Test Skill

```python
# tests/test_weather_skill.py
import pytest
from skills.weather_skill import WeatherSkill

@pytest.mark.asyncio
async def test_weather_skill():
    skill = WeatherSkill()
    result = await skill.execute(location="London")
    assert result.success
    assert "temperature" in result.data
```

## Skill Discovery

### GitHub Search

OpenAkita can search GitHub for skills:

```
Agent: Searching GitHub for "excel processing" skills...
Found: user/excel-processor (★ 45)
Installing...
```

### Auto-Discovery

When a task requires unknown capability:

1. Agent analyzes what's needed
2. Searches skill registry
3. If not found, searches GitHub
4. If still not found, generates skill

## Skill Lifecycle

```
┌─────────┐     ┌──────────┐     ┌────────┐     ┌─────────┐
│ Discover │ --> │ Install  │ --> │  Load  │ --> │ Execute │
└─────────┘     └──────────┘     └────────┘     └─────────┘
                     │                                │
                     v                                v
              ┌──────────┐                    ┌──────────┐
              │Dependencies│                   │  Result  │
              └──────────┘                    └──────────┘
```

## Best Practices

### Do's

- Keep skills focused on one task
- Include comprehensive error handling
- Add examples and documentation
- Use type hints
- Write tests

### Don'ts

- Don't include API keys in code
- Don't make skills too broad
- Don't skip input validation
- Don't ignore exceptions

## Skill Configuration

Skills can have their own config:

```yaml
# config/skills/weather.yaml
api_key: ${WEATHER_API_KEY}
default_location: Beijing
units: metric
cache_ttl: 3600
```

Access in skill:

```python
class WeatherSkill(BaseSkill):
    def __init__(self):
        super().__init__()
        self.config = self.load_config("weather")
```

## Publishing Skills

### To GitHub

1. Create repo with skill code
2. Add `openakita-skill` topic
3. Include README with usage
4. Tag releases with versions

### Skill Package Structure

```
my-skill/
├── __init__.py
├── skill.py          # Main skill class
├── requirements.txt  # Dependencies
├── README.md         # Documentation
├── tests/
│   └── test_skill.py
└── examples/
    └── example.py
```

## Troubleshooting

### Skill Not Loading

```bash
# Check skill syntax
python -c "from skills.my_skill import MySkill"

# Check dependencies
pip install -r skills/my_skill/requirements.txt
```

### Skill Execution Fails

```bash
# Enable debug logging
LOG_LEVEL=DEBUG openakita

# Run skill in isolation
openakita skills test my_skill
```

