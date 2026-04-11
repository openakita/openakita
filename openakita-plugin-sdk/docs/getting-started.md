# 快速入门 / Getting Started

从零开始创建一个 OpenAkita 插件：安装 SDK、脚手架生成、编写代码、测试验证、部署运行。

Build your first OpenAkita plugin from scratch: install the SDK, scaffold, code, test, and deploy.

---

## 环境要求 / Prerequisites

- Python **3.11+**
- OpenAkita **≥ 1.5.0**（仅运行时需要，开发和测试不需要完整安装）
- OpenAkita **≥ 1.5.0** (only needed at runtime; development and testing work without the full app)

## 安装 SDK / Install the SDK

```bash
# 从源码安装（开发模式）/ Install from source (editable mode)
pip install -e ./openakita-plugin-sdk

# 或从 PyPI 安装 / Or install from PyPI (once published)
pip install openakita-plugin-sdk
```

验证安装 / Verify installation:

```python
import openakita_plugin_sdk
print(openakita_plugin_sdk.__version__)  # 0.1.0
```

## 三种上手方式 / Three Ways to Get Started

### 方式一：脚手架（推荐）/ Option A: Scaffold (Recommended)

一条命令生成完整的插件目录：

Generate a complete plugin directory with one command:

```bash
python -m openakita_plugin_sdk.scaffold --id my-tool --type tool --dir ./plugins
```

可选类型 / Available types: `tool`, `channel`, `rag`, `memory`, `llm`, `hook`, `skill`, `mcp`, `ui`

生成结构 / Generated structure:

```
plugins/my-tool/
  plugin.json      # 清单文件，已填好权限 / manifest with correct permissions
  plugin.py        # 模板代码 / template code
  README.md        # 说明文件 / description
```

也可以在 Python 中调用 / Programmatic usage:

```python
from openakita_plugin_sdk.scaffold import scaffold_plugin

scaffold_plugin(
    target_dir="./plugins/my-tool",
    plugin_id="my-tool",
    plugin_name="My Tool",
    plugin_type="tool",
    author="Your Name",
    description="A tool that does something useful",
)
```

### 方式二：装饰器（简洁）/ Option B: Decorators (Concise)

用 `@tool` 和 `@hook` 装饰器声明式注册，`auto_register()` 一行搞定。

Use `@tool` and `@hook` decorators for declarative registration, then `auto_register()` in one line.

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.decorators import tool, hook, auto_register

@tool(name="greet", description="Greet someone by name")
async def greet(tool_name: str, arguments: dict) -> str:
    return f"Hello, {arguments['name']}!"

@hook("on_message_received")
async def log_incoming(**kwargs):
    print(f"Got message: {kwargs.get('text', '')[:50]}")

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        auto_register(api)
```

### 方式三：手动注册（完全控制）/ Option C: Manual Registration (Full Control)

适合需要复杂逻辑的插件。

Best for plugins that need complex initialization logic.

```python
from openakita_plugin_sdk import PluginBase, PluginAPI
from openakita_plugin_sdk.tools import tool_definition

TOOLS = [
    tool_definition(
        name="hello",
        description="Say hello",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
]

class Plugin(PluginBase):
    def on_load(self, api: PluginAPI) -> None:
        async def handler(tool_name: str, arguments: dict) -> str:
            return f"Hello, {arguments['name']}!"

        api.register_tools(TOOLS, handler)
        api.log("Plugin loaded")

    def on_unload(self) -> None:
        pass
```

## plugin.json 清单文件 / Manifest

每个插件必须包含 `plugin.json`。最小示例：

Every plugin must include a `plugin.json`. Minimal example:

```json
{
  "id": "hello-tool",
  "name": "Hello Tool",
  "version": "1.0.0",
  "type": "python",
  "entry": "plugin.py",
  "permissions": ["tools.register"],
  "provides": { "tools": ["hello"] }
}
```

详细字段说明见 [plugin-json.md](plugin-json.md)。

See [plugin-json.md](plugin-json.md) for the full field reference.

## 关键约定 / Critical Conventions

### 入口类命名（Python 插件必读）/ Entry Class Naming

Python 插件的入口文件（`entry` 字段指向的 `.py` 文件）**必须**导出一个名为 **`Plugin`** 的类，且该类**必须**继承 **`PluginBase`**。其他名称（如 `MyPlugin`）会导致加载失败。

The entry file **must** export a class named **`Plugin`** that inherits from **`PluginBase`**. Any other class name (e.g., `MyPlugin`) will cause a load failure.

```python
from openakita_plugin_sdk import PluginBase, PluginAPI

class Plugin(PluginBase):             # 名称必须是 Plugin / Name MUST be Plugin
    def on_load(self, api: PluginAPI) -> None:  # 必须实现 / REQUIRED
        ...

    def on_unload(self) -> None:      # 可选 / OPTIONAL (has default no-op)
        ...
```

**生命周期要点 / Lifecycle notes:**
- `on_load()` 由宿主**同步**调用，不要执行长时间阻塞操作（超过 `load_timeout` 秒会被终止）
- `on_load()` is called **synchronously** by the host; avoid long blocking operations
- `on_unload()` 是可选的，有默认空实现 / `on_unload()` is optional with a default no-op

### 不同 `type` 的要求 / Requirements by `type`

| `type` | 需要 `plugin.py` + `Plugin` 类？ | 入口文件 | 说明 |
|--------|:---:|---------|------|
| `python` | **是 / Yes** | `plugin.py` | 完整 Python 插件 |
| `mcp` | 否 / No | `mcp_config.json` | 仅 JSON 配置，无 Python 代码 |
| `skill` | 否 / No | `SKILL.md` | 仅 Skill 声明文件，无 Python 代码 |

## 目录结构 / Directory Layout

### Python 插件 / Python Plugin

```
my-plugin/
  plugin.json          # 必需：清单 / required: manifest
  plugin.py            # 必需：入口 (type: python) / required: entry (type: python)
  config_schema.json   # 可选：配置 schema / optional: config schema
  README.md            # 推荐 / recommended
  tests/
    test_plugin.py     # 推荐 / recommended
```

### MCP 插件 / MCP Plugin

```
my-mcp/
  plugin.json          # type: "mcp"
  mcp_config.json      # MCP 服务配置 / MCP server config
  README.md
```

### Skill 插件 / Skill Plugin

```
my-skill/
  plugin.json          # type: "skill"
  SKILL.md             # 技能声明文件 / Skill declaration
  README.md
```

> **注意 / Note:** 持久化数据应存放在 `api.get_data_dir()` 返回的目录中，不要写入插件安装目录。
>
> Persistent data should go in `api.get_data_dir()`, not the plugin install directory.

## 测试 / Testing

使用 `MockPluginAPI` 在没有 OpenAkita 运行时的情况下测试插件：

Test your plugin without a running OpenAkita instance using `MockPluginAPI`:

```python
from openakita_plugin_sdk.testing import MockPluginAPI, assert_plugin_loads
from plugin import Plugin

def test_plugin_loads():
    plugin = Plugin()
    api = assert_plugin_loads(plugin)
    assert "hello" in api.registered_tools

def test_unload():
    plugin = Plugin()
    api = MockPluginAPI()
    plugin.on_load(api)
    plugin.on_unload()  # 不应抛出异常 / should not raise
```

详见 [testing.md](testing.md)。See [testing.md](testing.md) for more patterns.

## 开发模式运行 / Run in Development Mode

1. 将插件目录放入（或软链接到）宿主的 plugins 目录
2. 启动 OpenAkita（CLI 或 API 模式）
3. 确保 `plugin.json` 中声明的 `permissions` 覆盖了所有 `api.register_*` 和 `api.get_*` 调用

Steps:
1. Place (or symlink) your plugin directory into the host's plugins directory
2. Start OpenAkita (CLI or API server mode)
3. Ensure `permissions` in `plugin.json` covers every `api.register_*` and `api.get_*` call

```bash
# 默认插件目录 / Default plugin directory
{project_root}/data/plugins/my-tool/

# 状态文件 / State file
{project_root}/data/plugin_state.json
```

> `project_root` 是 OpenAkita 的工作目录（通常是运行 `openakita` 命令的目录）。
>
> `project_root` is the OpenAkita working directory (typically where `openakita` is run).

## 下一步 / Next Steps

| 文档 / Doc | 内容 / Content |
|-----------|----------------|
| [api-reference.md](api-reference.md) | PluginAPI 完整方法参考 / Full method reference |
| [**plugin-ui.md**](plugin-ui.md) | **全栈 UI 插件开发 / Full-stack UI plugin development (Plugin 2.0)** |
| [rest-api.md](rest-api.md) | 管理 REST API / Plugin management REST API |
| [permissions.md](permissions.md) | 三级权限模型 / Three-tier permission model |
| [hooks.md](hooks.md) | 14 个生命周期钩子 / 14 lifecycle hooks |
| [protocols.md](protocols.md) | Memory/Retrieval/Search 接口 / Backend interfaces |
| [plugin-json.md](plugin-json.md) | 清单文件字段详解 / Manifest field reference |
| [testing.md](testing.md) | 测试模式和工具 / Test patterns and utilities |
| [cross-ecosystem.md](cross-ecosystem.md) | 跨生态兼容 / Cross-ecosystem compatibility |
| [examples/](examples/) | 每种插件类型的完整示例 / Full examples for each plugin type |
