# OpenAkita 插件系统 Beta 上线：来造你自己的 AI 能力模块

> 摘要：v1.27.5 起，OpenAkita 正式支持插件系统。SDK 已发布，官方插件仓库已上线。现在邀请有开发能力的用户（Vibe Coding 也行）体验插件开发，贡献社区。Beta 阶段可能存在 bug，以体验和反馈为主。

---

## 终于，OpenAkita 可以装插件了

从今天开始，OpenAkita 不再是一个「只能用内置功能」的 AI 助手。

**v1.27.5 正式引入插件系统**——你可以用 Python 写一个插件，给 Akita 加上任何你想要的能力：对接新的 IM 平台、接入你自己的知识库、注册自定义工具、替换记忆后端，甚至接入本地大模型。

插件系统支持 **8 种类别**，覆盖 OpenAkita 几乎所有可扩展点：

| 类别 | 你能做什么 | 举个例子 |
|------|-----------|---------|
| Tool | 给 AI 注册新工具 | 查天气、操作数据库、调用内部 API |
| Channel | 接入新的 IM 平台 | WhatsApp、Matrix、自建聊天系统 |
| Memory | 替换/扩展记忆后端 | 用 Qdrant 做向量记忆、SQLite 轻量存储 |
| LLM | 接入新的大模型 | Ollama 本地模型、私有部署的模型 |
| Knowledge/RAG | 对接知识源 | Obsidian 笔记、Notion、内部文档系统 |
| Hook | 拦截生命周期事件 | 消息日志、审计、自动翻译 |
| Skill | 注入提示词技能 | 翻译技能包、写作风格包（纯声明式，不用写代码） |
| MCP | 封装 MCP 服务 | GitHub、Jira 等 MCP 工具托管 |

---

## 怎么开始？3 分钟上手

### 1. 安装 SDK

```bash
pip install openakita-plugin-sdk
```

### 2. 脚手架生成插件骨架

```bash
python -m openakita_plugin_sdk.scaffold --id my-tool --type tool --dir ./my-plugin
```

一条命令生成完整的插件目录：`plugin.json`（清单）、`plugin.py`（入口）、`README.md`（说明）。

### 3. 写你的逻辑

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.decorators import tool, auto_register

@tool(name="hello", description="Say hello to someone")
async def hello(tool_name: str, arguments: dict) -> str:
    return f"Hello, {arguments['name']}!"

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        auto_register(api)
```

### 4. 放进 OpenAkita

把插件目录复制到 `data/plugins/`，重启，搞定。

---

## 官方插件仓库已上线

我们准备了 **11 个官方示例插件**，覆盖全部 8 种类别，可以直接拿来用，也可以当作开发参考：

👉 **https://github.com/openakita/openakita-plugins**

| 插件 | 类别 | 说明 |
|------|------|------|
| hello-tool | 工具 | 最简示例，5 分钟看懂插件结构 |
| echo-channel | 通道 | 消息回声，通道开发入门 |
| sqlite-memory | 记忆 | 纯 SQLite 实现，零外部依赖 |
| echo-llm | LLM | Echo 模型提供商，用于测试 |
| obsidian-kb | 知识库 | Obsidian 笔记全文检索 |
| message-logger | 钩子 | 消息日志记录 |
| translate-skill | 技能 | 翻译技能包（纯声明式） |
| github-mcp | MCP | GitHub MCP 服务封装 |
| whatsapp-channel | 通道 | WhatsApp 接入 |
| qdrant-memory | 记忆 | Qdrant 向量记忆后端 |
| ollama-provider | LLM | Ollama 本地大模型接入 |

仓库里同时包含完整的 **SDK 文档**（API 参考、权限模型、钩子系统、协议接口、测试工具等）和**贡献指南**。

---

## 谁适合来玩？

**不需要你是资深开发者。**

如果你会用 Cursor、Windsurf 或者任何 AI 编程工具做 Vibe Coding，那就足够了。插件结构很简单——一个 JSON 清单 + 一个 Python 入口文件，最简单的插件不到 20 行代码。

适合你的场景：
- 你有一个内部 API，想让 Akita 能调用它
- 你用 Obsidian / Notion 管理知识，想让 Akita 能检索
- 你想给 Akita 加一个新的 IM 通道
- 你在本地跑 Ollama，想让 Akita 直接调用
- 你就是想看看插件系统怎么回事，随便写着玩

---

## Beta 阶段说明

**这是 Beta 版本**，坦白讲：

- 插件系统的核心流程已经跑通，但边界场景可能还有 bug
- SDK 的 API 在正式版之前可能会有小幅调整
- 文档可能有遗漏或描述不够清晰的地方
- 权限系统采用优雅降级策略——权限不足时不会崩溃，而是静默跳过

**我们需要的就是你的反馈：**
- 开发过程中遇到的问题
- 文档哪里看不懂
- 哪些 API 不好用
- 哪些功能你觉得缺失

欢迎在 GitHub Issues 中提交反馈，或者直接在社区群里说。

---

## 相关链接

- 插件仓库：[github.com/openakita/openakita-plugins](https://github.com/openakita/openakita-plugins)
- 主项目：[github.com/openakita/openakita](https://github.com/openakita/openakita)
- SDK（PyPI）：`pip install openakita-plugin-sdk`
- 插件系统概览文档：[plugin-system-overview.md](../plugin-system-overview.md)

---

> **OpenAkita** —— 不只是聊天，是你的 AI 团队。现在，这个团队的能力由你来定义。
>
> 开源 · 免费 · 支持 30+ AI 模型 · 6 大 IM 平台 · **插件系统 Beta 上线**
