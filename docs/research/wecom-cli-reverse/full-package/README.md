# @wecom/wecom-openclaw-cli

企业微信（WeCom）OpenClaw 官方插件安装脚手架工具。帮助用户快速安装、配置、更新和诊断企业微信 OpenClaw 插件。

## 快速使用

### 通过 npx 执行（推荐，无需安装）

```bash
npx -y @wecom/wecom-openclaw-cli <command>
```

例如：

```bash
# 安装插件
npx -y @wecom/wecom-openclaw-cli install

# 查看信息
npx -y @wecom/wecom-openclaw-cli info

# 诊断问题
npx -y @wecom/wecom-openclaw-cli doctor
```

### 全局安装

如果需要频繁使用，也可以全局安装：

```bash
npm install -g @wecom/wecom-openclaw-cli
```

安装完成后，可以直接使用 `wecom-openclaw-cli` 命令：

```bash
npx -y @wecom/wecom-openclaw-cli --help
```

## 命令

### `install` — 安装插件

安装并配置企业微信官方 OpenClaw 插件。安装过程中会自动检测并禁用冲突插件，并交互式提示输入机器人 ID（Bot ID）和机器人密钥（Secret）。

> **提示**：如果插件已安装，`install` 会自动进入更新流程，无需手动调用 `update`。

```bash
npx -y @wecom/wecom-openclaw-cli install

# 指定版本安装
npx -y @wecom/wecom-openclaw-cli install --version 1.2.0

# 强制重新安装（清除已有插件和配置后全量重装）
npx -y @wecom/wecom-openclaw-cli install --force

# 指定 npm 包名（适用于私有源或自定义包名场景）
npx -y @wecom/wecom-openclaw-cli install --package-name @my-scope/my-plugin

# 跳过 channels 配置引导（有历史配置则自动恢复，无则跳过）
npx -y @wecom/wecom-openclaw-cli install --skip-config

# 强制重装并跳过配置引导
npx -y @wecom/wecom-openclaw-cli install --force --skip-config
```

| 参数 | 说明 |
|------|------|
| `--version <version>` | 安装指定版本的插件 |
| `--package-name <packageName>` | 指定 npm 包名（默认 `@wecom/wecom-openclaw-plugin`） |
| `--force` | 强制重新安装，清除已有插件目录和配置 |
| `--skip-config` | 跳过 channels 配置引导（有历史配置则自动恢复，无则跳过） |

### `update` — 更新插件

将已安装的企业微信插件更新到最新版本。更新完成后会自动运行 `doctor` 诊断检查。

```bash
# 更新默认插件（wecom-openclaw-plugin）
npx -y @wecom/wecom-openclaw-cli update

# 指定插件名进行更新
npx -y @wecom/wecom-openclaw-cli update wecom-openclaw-plugin
```

| 参数 | 说明 |
|------|------|
| `[pluginName]` | 可选，指定要更新的插件名（默认 `wecom-openclaw-plugin`） |

### `info` — 查看信息

显示 CLI 版本、OpenClaw 版本、插件版本和环境信息。

```bash
npx -y @wecom/wecom-openclaw-cli info

# 显示完整详情（含配置状态、冲突插件检测、npm 源连通性及脱敏后的配置内容）
npx -y @wecom/wecom-openclaw-cli info --all
```

| 参数 | 说明 |
|------|------|
| `--all` | 显示所有详细信息，包括配置状态、冲突插件、npm 源连通性和脱敏配置 |

### `doctor` — 诊断问题

全面检查插件安装状态，包括：插件目录、依赖完整性、配置正确性、冲突插件检测和 channel 配置。

```bash
npx -y @wecom/wecom-openclaw-cli doctor

# 自动修复检测到的问题
npx -y @wecom/wecom-openclaw-cli doctor --fix
```

| 参数 | 说明 |
|------|------|
| `--fix` | 尝试自动修复检测到的问题（如安装依赖、移除冲突插件、补全配置等） |

## 其他

### 版本查看

```bash
npx -y @wecom/wecom-openclaw-cli -V
# 或
npx -y @wecom/wecom-openclaw-cli --cli-version
```

## License

ISC
