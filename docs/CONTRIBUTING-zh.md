# 为 OpenAkita 做贡献

首先，感谢你考虑为 OpenAkita 做出贡献！正是像你这样的人让 OpenAkita 成为如此优秀的工具。

## 目录

- [行为准则](#行为准则)
- [入门指南](#入门指南)
- [如何贡献？](#如何贡献)
- [开发环境设置](#开发环境设置)
- [编码规范](#编码规范)
- [提交指南](#提交指南)
- [Pull Request 流程](#pull-request-流程)
- [社区](#社区)

## 行为准则

本项目及所有参与者受我们的 [行为准则](CODE_OF_CONDUCT.md) 约束。通过参与，你应遵守此准则。请向 [zacon365@gmail.com](mailto:zacon365@gmail.com) 举报不当行为。

## 入门指南

### 前置要求

- Python 3.11 或更高版本
- Git
- Anthropic API 密钥（用于测试）

### Fork 和克隆

1. 在 GitHub 上 Fork 仓库
2. 克隆你的 Fork 到本地：

```bash
git clone https://github.com/YOUR_USERNAME/openakita.git
cd openakita
```

3. 添加上游仓库：

```bash
git remote add upstream https://github.com/openakita/openakita.git
```

4. 保持你的 Fork 同步：

```bash
git fetch upstream
git checkout main
git merge upstream/main
```

## 如何贡献？

### 报告 Bug

在创建 Bug 报告之前，请检查 [现有 Issues](https://github.com/openakita/openakita/issues) 以避免重复。

创建 Bug 报告时，请提供尽可能多的细节：

- **使用清晰描述性的标题**
- **描述重现问题的确切步骤**
- **提供具体示例**
- **描述你观察到的行为和期望行为**
- **如适用请附上截图**
- **包含你的环境信息**（操作系统、Python 版本等）

创建 Issue 时使用 Bug 报告模板。

### 建议新功能

欢迎功能建议！请提供：

- **清晰描述性的标题**
- **新功能的详细描述**
- **解释为什么这个功能有用**
- **列出你考虑过的替代方案**

### 你的第一次代码贡献

不确定从哪里开始？查找带有以下标签的 Issues：

- `good first issue` - 适合新手的简单问题
- `help wanted` - 需要社区帮助的问题
- `documentation` - 需要文档改进

### Pull Requests

1. Fork 仓库并从 `main` 创建你的分支
2. 如果添加了需要测试的代码，请添加测试
3. 如果更改了 API，请更新文档
4. 确保测试套件通过
5. 确保你的代码符合编码规范
6. 提交 Pull Request

## 开发环境设置

### 安装开发依赖

```bash
# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安装开发依赖
pip install -e ".[dev]"

# 安装 pre-commit hooks（可选但推荐）
pip install pre-commit
pre-commit install
```

### 运行测试

```bash
# 运行所有测试
pytest tests/ -v

# 带覆盖率运行
pytest tests/ --cov=src/openakita --cov-report=html

# 运行特定测试文件
pytest tests/test_agent.py -v

# 运行匹配模式的测试
pytest tests/ -v -k "test_tool"
```

### 代码质量检查

```bash
# 类型检查
mypy src/

# 代码检查
ruff check src/

# 格式化代码
ruff format src/

# 所有检查
pytest && mypy src/ && ruff check src/
```

## 编码规范

### Python 风格

- 遵循 [PEP 8](https://pep8.org/) 风格指南
- 为所有函数使用类型提示
- 最大行长度：100 字符
- I/O 操作使用 `async/await`

### 代码组织

```python
# 标准库导入
import asyncio
import logging

# 第三方导入
from anthropic import Anthropic

# 本地导入
from openakita.core.agent import Agent
```

### 文档字符串

使用 Google 风格的文档字符串：

```python
async def process_message(self, message: str, context: dict | None = None) -> str:
    """处理用户消息并返回响应。
    
    Args:
        message: 用户的输入消息。
        context: 可选的对话上下文字典。
        
    Returns:
        Agent 的响应字符串。
        
    Raises:
        ValueError: 如果消息为空。
        APIError: 如果 Claude API 调用失败。
    """
    pass
```

### 类型提示

始终使用类型提示：

```python
from typing import Optional, Dict, List, Any

def get_user(user_id: str) -> Optional[User]:
    ...

async def process_batch(items: List[str]) -> Dict[str, Any]:
    ...
```

### 文件组织

- 保持文件在 500 行以内
- 每个文件一个类（通常）
- 将相关功能分组到模块中

## 提交指南

### 提交信息格式

我们遵循 [约定式提交](https://www.conventionalcommits.org/)：

```
<类型>(<范围>): <描述>

[可选的正文]

[可选的脚注]
```

### 类型

- `feat`: 新功能
- `fix`: Bug 修复
- `docs`: 仅文档更改
- `style`: 代码风格更改（格式化等）
- `refactor`: 代码重构（既不是 Bug 修复也不是新功能）
- `perf`: 性能改进
- `test`: 添加或修正测试
- `chore`: 构建过程或辅助工具的更改

### 示例

```
feat(tools): 添加浏览器自动化支持

fix(brain): 优雅处理 API 超时

docs(readme): 更新安装说明

refactor(agent): 简化工具执行逻辑
```

### 提交最佳实践

- 保持提交的原子性（每次提交一个逻辑更改）
- 编写清晰简洁的提交信息
- 引用相关问题：`fix(brain): handle timeout (#123)`

## Pull Request 流程

### 提交前

1. **用最新的上游代码更新你的 Fork**
2. **运行所有测试**并确保通过
3. **运行代码质量检查**（mypy, ruff）
4. **更新文档**（如需要）
5. **为你的更改添加/更新测试**

### PR 模板

创建 PR 时，请包含：

```markdown
## 描述
简要描述更改内容。

## 更改类型
- [ ] Bug 修复
- [ ] 新功能
- [ ] 破坏性更改
- [ ] 文档更新

## 如何测试
描述你运行的测试。

## 检查清单
- [ ] 我的代码遵循项目的编码规范
- [ ] 我为我的更改添加了测试
- [ ] 所有新的和现有的测试都通过
- [ ] 我已更新文档
- [ ] 我的更改没有产生新的警告
```

### 审查流程

1. **自动化检查**必须通过（CI/CD）
2. **至少一名维护者**必须批准
3. **解决所有审查意见**后再合并
4. 如需要请 **压缩提交**

### PR 合并后

- 删除你的功能分支
- 更新本地 main 分支
- 庆祝！🎉

## 社区

### 获取帮助

- 📖 [文档](docs/)
- 💬 [GitHub Discussions](https://github.com/openakita/openakita/discussions)
- 🐛 [Issue Tracker](https://github.com/openakita/openakita/issues)

### 认可

贡献者将在以下地方被认可：

- [贡献者](https://github.com/openakita/openakita/graphs/contributors) 页面
- 重要贡献的发布说明
- README（对主要贡献者）

## 感谢你！

你的贡献让 OpenAkita 变得更好。我们感谢你的时间和努力！

---

*本贡献指南改编自开源最佳实践和 [贡献者公约](https://www.contributor-covenant.org/)。*
