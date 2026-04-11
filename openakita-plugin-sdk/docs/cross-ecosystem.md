# 跨生态兼容 / Cross-Ecosystem Compatibility

OpenAkita 可以发现并安装来自其他 AI 工具生态的插件包：**OpenClaw**、**Claude**、**Cursor** 和 **Codex**。

OpenAkita can discover and install plugin bundles from four AI tool ecosystems: **OpenClaw**, **Claude**, **Cursor**, and **Codex**.

---

## 工作原理 / How It Works

`BundleMapper`（位于 `openakita.plugins.bundles`）检测插件包的格式，并将其映射为 OpenAkita 原生的 `plugin.json` 清单。

The `BundleMapper` in `openakita.plugins.bundles` detects the format of a plugin package and maps it to OpenAkita's native `plugin.json` manifest.

### 检测规则 / Detection Rules

| 生态 / Ecosystem | 识别文件 / Identifier Files | 映射内容 / Mapped Content |
|-----------------|---------------------------|--------------------------|
| **OpenClaw** | `openclaw.plugin.json` 或 `package.json` 含 `openclaw.extensions` | Skills、MCP 服务、钩子 |
| **Claude** | `.claude-plugin/plugin.json` 或 `skills/` + `.mcp.json` | Skills、命令、MCP、配置 |
| **Cursor** | `.cursor-plugin/plugin.json` 或 `.cursor/rules/` + `.cursor/skills/` | Skills、规则（作为 Prompt）、MCP |
| **Codex** | `.codex-plugin/plugin.json` 或 `.codex/skills/` | Skills、钩子、MCP |

### 映射结果 / What Gets Mapped

| 源文件 / Source | 映射为 / Mapped To |
|----------------|-------------------|
| `SKILL.md` 文件 | 直接加载为 OpenAkita Skill（格式原生兼容）/ Loaded directly as skills |
| `commands/` 目录 | 每个子目录作为一个 Skill / Each subdirectory as a skill |
| `.mcp.json` | 注册为 MCP 服务配置 / Registered as MCP server config |
| `.cursor/rules/*.mdc` | 转换为 Skill 文本用于 Prompt 注入 / Converted to skill text |

> **注意 / Note:** `settings.json` 不会自动导入为插件配置。`map_to_manifest` 仅提取 `version`、`description`、`author` 等元数据字段。
>
> `settings.json` is **not** auto-imported as plugin config. `map_to_manifest` only extracts metadata fields like `version`, `description`, `author`.

---

## 安装跨生态插件 / Installing Cross-Ecosystem Bundles

```bash
# 从 GitHub 安装 / From GitHub
openakita plugin install https://github.com/user/openclaw-obsidian-plugin

# 从本地路径安装 / From local path
openakita plugin install /path/to/claude-skill-bundle

# 通过对话安装 / Via conversation
用户 / User: "安装 OpenClaw 的 Notion 插件 / Install the OpenClaw Notion plugin"
```

---

## 让你的插件兼容其他生态 / Making Your Plugin Compatible

要让你的 OpenAkita 插件被其他生态发现：

To make your OpenAkita plugin discoverable by other ecosystems:

### 1. 包含 SKILL.md（通用兼容）/ Include SKILL.md (Universal)

`SKILL.md` 是所有生态通用的格式。在插件根目录放一个 `SKILL.md`，任何支持 Skill 的工具都能识别。

`SKILL.md` is universally compatible. Place one in your plugin root for any skill-aware tool to discover.

### 2. 添加生态标记文件（可选）/ Add Ecosystem Markers (Optional)

```
my-plugin/
  plugin.json              # OpenAkita 原生 / native
  plugin.py                # Python 入口 / entry point
  SKILL.md                 # 通用兼容 / universal compatibility
  .claude-plugin/
    plugin.json            # Claude 发现标记 / Claude discovery marker
  .cursor-plugin/
    plugin.json            # Cursor 发现标记 / Cursor discovery marker
  README.md
```

### 3. Claude 兼容标记示例 / Claude Marker Example

```json
// .claude-plugin/plugin.json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "A plugin for AI assistants",
  "skills": ["SKILL.md"],
  "mcp": []
}
```

### 4. Cursor 兼容标记示例 / Cursor Marker Example

```json
// .cursor-plugin/plugin.json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "skills": ["."],
  "rules": []
}
```

---

## 限制 / Limitations

- 跨生态包仅映射为 **skill** 和 **mcp** 类型 / Cross-ecosystem bundles map to **skill** and **mcp** types only
- Python 原生能力（通道、记忆、LLM 提供商）需要 OpenAkita 原生 `plugin.json` / Python-native capabilities require native `plugin.json`
- 钩子和 API 路由是 OpenAkita 特有的，不映射到其他生态 / Hooks and API routes are OpenAkita-specific
- 权限模型是 OpenAkita 特有的 / The permission model is OpenAkita-specific

---

## 相关文档 / Related

- [plugin-json.md](plugin-json.md) — OpenAkita 原生清单格式 / Native manifest format
- [getting-started.md](getting-started.md) — 创建 OpenAkita 原生插件 / Create native plugins
- [rest-api.md](rest-api.md) — 安装 API（含 bundle 安装） / Install API (incl. bundle install)
- [api-reference.md](api-reference.md) — 运行时 API 参考 / Runtime API reference
