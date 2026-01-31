# Contributing to OpenAkita

First off, thank you for considering contributing to OpenAkita! It's people like you that make OpenAkita such a great tool.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Coding Standards](#coding-standards)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Community](#community)

## Code of Conduct

This project and everyone participating in it is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to [zacon365@gmail.com](mailto:zacon365@gmail.com).

## Getting Started

### Prerequisites

- Python 3.11 or higher
- Git
- An Anthropic API key (for testing)

### Fork and Clone

1. Fork the repository on GitHub
2. Clone your fork locally:

```bash
git clone https://github.com/YOUR_USERNAME/openakita.git
cd openakita
```

3. Add the upstream repository:

```bash
git remote add upstream https://github.com/openakita/openakita.git
```

4. Keep your fork synchronized:

```bash
git fetch upstream
git checkout main
git merge upstream/main
```

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check the [existing issues](https://github.com/openakita/openakita/issues) to avoid duplicates.

When you create a bug report, please include as many details as possible:

- **Use a clear and descriptive title**
- **Describe the exact steps to reproduce the problem**
- **Provide specific examples**
- **Describe the behavior you observed and what you expected**
- **Include screenshots if applicable**
- **Include your environment details** (OS, Python version, etc.)

Use the bug report template when creating an issue.

### Suggesting Enhancements

Enhancement suggestions are welcome! Please provide:

- **A clear and descriptive title**
- **A detailed description of the proposed feature**
- **Explain why this feature would be useful**
- **List any alternatives you've considered**

### Your First Code Contribution

Unsure where to begin? Look for issues labeled:

- `good first issue` - Simple issues for newcomers
- `help wanted` - Issues that need community help
- `documentation` - Documentation improvements needed

### Pull Requests

1. Fork the repo and create your branch from `main`
2. If you've added code that should be tested, add tests
3. If you've changed APIs, update the documentation
4. Ensure the test suite passes
5. Make sure your code follows the coding standards
6. Issue the pull request

## Development Setup

### Install Development Dependencies

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install with development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks (optional but recommended)
pip install pre-commit
pre-commit install
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src/openakita --cov-report=html

# Run specific test file
pytest tests/test_agent.py -v

# Run tests matching a pattern
pytest tests/ -v -k "test_tool"
```

### Code Quality Checks

```bash
# Type checking
mypy src/

# Linting
ruff check src/

# Format code
ruff format src/

# All checks
pytest && mypy src/ && ruff check src/
```

## Coding Standards

### Python Style

- Follow [PEP 8](https://pep8.org/) style guide
- Use type hints for all functions
- Maximum line length: 100 characters
- Use `async/await` for I/O operations

### Code Organization

```python
# Standard library imports
import asyncio
import logging

# Third-party imports
from anthropic import Anthropic

# Local imports
from openakita.core.agent import Agent
```

### Docstrings

Use Google-style docstrings:

```python
async def process_message(self, message: str, context: dict | None = None) -> str:
    """Process a user message and return a response.
    
    Args:
        message: The user's input message.
        context: Optional context dictionary for the conversation.
        
    Returns:
        The agent's response as a string.
        
    Raises:
        ValueError: If the message is empty.
        APIError: If the Claude API call fails.
    """
    pass
```

### Type Hints

Always use type hints:

```python
from typing import Optional, Dict, List, Any

def get_user(user_id: str) -> Optional[User]:
    ...

async def process_batch(items: List[str]) -> Dict[str, Any]:
    ...
```

### File Organization

- Keep files under 500 lines
- One class per file (generally)
- Group related functionality in modules

## Commit Guidelines

### Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer(s)]
```

### Types

- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation only changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code change that neither fixes a bug nor adds a feature
- `perf`: Performance improvement
- `test`: Adding or correcting tests
- `chore`: Changes to build process or auxiliary tools

### Examples

```
feat(tools): add browser automation support

fix(brain): handle API timeout gracefully

docs(readme): update installation instructions

refactor(agent): simplify tool execution logic
```

### Commit Best Practices

- Keep commits atomic (one logical change per commit)
- Write clear, concise commit messages
- Reference issues when applicable: `fix(brain): handle timeout (#123)`

## Pull Request Process

### Before Submitting

1. **Update your fork** with the latest upstream changes
2. **Run all tests** and ensure they pass
3. **Run code quality checks** (mypy, ruff)
4. **Update documentation** if needed
5. **Add/update tests** for your changes

### PR Template

When creating a PR, please include:

```markdown
## Description
Brief description of the changes.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## How Has This Been Tested?
Describe the tests you ran.

## Checklist
- [ ] My code follows the project's coding standards
- [ ] I have added tests for my changes
- [ ] All new and existing tests pass
- [ ] I have updated the documentation
- [ ] My changes generate no new warnings
```

### Review Process

1. **Automated checks** must pass (CI/CD)
2. **At least one maintainer** must approve
3. **Address all review comments** before merging
4. **Squash commits** if requested

### After Your PR is Merged

- Delete your feature branch
- Update your local main branch
- Celebrate! 🎉

## Community

### Getting Help

- 📖 [Documentation](docs/)
- 💬 [GitHub Discussions](https://github.com/openakita/openakita/discussions)
- 🐛 [Issue Tracker](https://github.com/openakita/openakita/issues)

### Recognition

Contributors are recognized in:
- The [Contributors](https://github.com/openakita/openakita/graphs/contributors) page
- Release notes for significant contributions
- Our README (for major contributors)

## Thank You!

Your contributions make OpenAkita better for everyone. We appreciate your time and effort!

---

*This contributing guide is adapted from open source best practices and the [Contributor Covenant](https://www.contributor-covenant.org/).*
