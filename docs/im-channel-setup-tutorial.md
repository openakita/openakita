# IM 通道配置教程

> 本教程详细介绍如何为 OpenAkita 配置各 IM 通道（Telegram、飞书、钉钉、企业微信、QQ 官方机器人、OneBot），包含平台端完整的申请流程和配置步骤。

---

## 目录

- [平台概览](#平台概览)
- [三种配置方式](#三种配置方式)（OpenAkita Desktop / CLI 向导 / 手动 .env）
- [一、Telegram 配置教程](#一telegram-配置教程)
- [二、飞书（Lark）配置教程](#二飞书lark配置教程)
- [三、钉钉配置教程](#三钉钉配置教程)
- [四、企业微信配置教程](#四企业微信配置教程)
- [五、QQ 官方机器人配置教程](#五qq-官方机器人配置教程)
- [六、OneBot（通用协议）配置教程](#六onebot通用协议配置教程)
- [七、语音识别（Whisper）配置](#七语音识别whisper配置)
- [八、常见问题汇总](#八常见问题汇总)

---

## 平台概览

| 平台 | 状态 | 接入方式 | 需要公网 IP | 安装命令 | 配置难度 |
|------|------|---------|------------|---------|---------|
| Telegram | ✅ 稳定 | Long Polling | ❌ 不需要 | 默认包含 | ⭐ 最简单 |
| 飞书 | ✅ 稳定 | WebSocket 长连接 | ❌ 不需要 | `pip install openakita[feishu]` | ⭐⭐ 简单 |
| 钉钉 | ✅ 稳定 | Stream 模式 (WebSocket) | ❌ 不需要 | `pip install openakita[dingtalk]` | ⭐⭐ 简单 |
| 企业微信 | ✅ 稳定 | HTTP 回调（智能机器人） | ⚠️ 需要公网 IP | `pip install openakita[wework]` | ⭐⭐⭐ 中等 |
| QQ 官方机器人 | ✅ 稳定 | QQ 开放平台 API (WebSocket) | ❌ 不需要 | `pip install openakita[qqbot]` | ⭐⭐ 简单 |
| OneBot | ✅ 稳定 | OneBot v11 (WebSocket) | ❌ 不需要 | `pip install openakita[onebot]` | ⭐⭐⭐ 中等 |

### 媒体类型支持一览

**接收消息（用户 → 机器人）**

| 类型 | Telegram | 飞书 | 钉钉 | 企业微信 | QQ 官方机器人 | OneBot |
|------|----------|------|------|---------|-------------|--------|
| 文字 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 图片 | ✅ | ✅ | ✅ | ⚠️ 仅单聊 | ✅ | ✅ |
| 语音 | ✅ (Whisper转写) | ✅ (Whisper转写) | ✅ (Whisper转写) | ❌ 不支持 | ✅ | ✅ (Whisper转写) |
| 文件 | ✅ | ✅ | ✅ | ❌ 不支持 | ✅ | ✅ |
| 视频 | ✅ | ✅ | ✅ | ❌ 不支持 | ✅ | ✅ |

**发送消息（机器人 → 用户）**

| 类型 | Telegram | 飞书 | 钉钉 | 企业微信 | QQ 官方机器人 | OneBot |
|------|----------|------|------|---------|-------------|--------|
| 文字 | ✅ | ✅ | ✅ | ✅ (stream 被动回复) | ✅ | ✅ |
| 图片 | ✅ | ✅ | ✅ | ✅ (stream msg_item) | ✅ (需公网URL) | ✅ |
| 语音 | ✅ | ✅ | ⚠️ 降级为文件 | ❌ 不支持 | ⚠️ 仅silk+URL | ✅ |
| 文件 | ✅ | ✅ | ⚠️ 降级为链接 | ❌ 降级为文本 | ❌ 暂未开放 | ✅ |
| 视频 | ✅ | ✅ | ✅ | ❌ 不支持 | ⚠️ 需公网URL | ✅ |

> **企业微信限制说明**：智能机器人通过 stream 流式被动回复发送文本和图片（JPG/PNG，≤10MB，单条最多 10 张）。**不支持语音、文件和视频的收发**。接收端仅支持文字、图文混排和图片（图片仅单聊）。
>
> **QQ 官方机器人限制说明**：群聊和单聊的富媒体发送（图片/语音/视频）需要先将文件上传到 QQ 服务器获取 `file_info`，且**仅支持公网 URL 上传**（`file_data` 本地文件上传暂未开放）。语音需要 silk 格式。文件发送（`file_type=4`）暂未开放。频道消息限制较少，支持直接图片 URL。

### 快速安装

> **提示**：如果你使用 **OpenAkita Desktop** 桌面程序安装，依赖会自动处理，无需手动执行 pip 命令。以下仅适用于手动 pip 部署的用户。

```bash
# 安装所有 IM 通道依赖（一步到位）
pip install openakita[all]

# 或按需安装
pip install openakita[feishu]      # 飞书
pip install openakita[dingtalk]    # 钉钉
pip install openakita[wework]      # 企业微信
pip install openakita[qqbot]       # QQ 官方机器人
pip install openakita[onebot]      # OneBot（通用协议）
pip install openakita[whisper]     # 语音识别

# 组合安装
pip install openakita[feishu,dingtalk,qqbot,whisper]
```

---

## 三种配置方式

OpenAkita 提供了三种方式来配置 IM 通道，你可以选择最适合自己的方式：

### 方式一：桌面终端程序（OpenAkita Desktop）— 推荐新手

OpenAkita Desktop 是 OpenAkita 提供的可视化桌面安装程序（基于 Tauri），提供图形化界面完成所有配置，无需手动编辑文件。

<!-- 📸 配图：OpenAkita Desktop 主界面全貌截图 -->
> **[配图位]** OpenAkita Desktop 桌面程序主界面

**特点**：
- 🖱️ 可视化表单，点选操作
- ✅ 实时状态检测（通道在线/离线/未配置）
- 🔄 配置完成后可一键重启服务
- 📦 集成了依赖安装、环境检测、端点管理等完整流程

**使用方式**：
1. 启动 OpenAkita Desktop 桌面程序
2. 在左侧导航栏找到 **「IM 通道」** 配置步骤
3. 按平台分组，填入对应的凭证信息（如 Token、App ID 等）
4. 开关 `*_ENABLED` 启用对应通道
5. 保存后自动写入 `.env`，可一键重启服务生效

<!-- 📸 配图：OpenAkita Desktop 中 IM 通道配置的步骤页面截图 -->
> **[配图位]** OpenAkita Desktop — IM 通道配置步骤页面

<!-- 📸 配图：OpenAkita Desktop 中 Telegram 配置表单的截图 -->
> **[配图位]** OpenAkita Desktop — Telegram 配置表单示例

<!-- 📸 配图：OpenAkita Desktop 状态页面中 IM 通道健康检查的截图 -->
> **[配图位]** OpenAkita Desktop — 状态页面的 IM 通道健康检查

> **提示**：OpenAkita Desktop 的状态页面会显示每个通道的实时连接状态（🟢 在线 / 🔴 离线 / ⚪ 未启用），可以随时检查配置是否正确。

### 方式二：CLI 交互式向导 — 适合命令行用户

通过命令行运行安装向导，交互式完成配置：

```bash
openakita setup
```

向导会引导你逐步完成：
1. 选择语言/地区
2. 配置 LLM API
3. **配置 IM 通道**（Step 4）
4. 配置记忆系统和其他选项

在 IM 通道步骤中，向导会显示可用通道列表，选择后按提示输入凭证：

```
Available channels:

  [1] Telegram (recommended)
  [2] Feishu (Lark)
  [3] WeCom (企业微信)
  [4] DingTalk (钉钉)
  [5] QQ 官方机器人
  [6] OneBot（通用协议）
  [7] Skip

Select channel [7]:
```

向导完成后自动生成 `.env` 文件。

### 方式三：手动编辑 .env 文件 — 适合高级用户

直接编辑项目根目录下的 `.env` 文件，精确控制每个配置项：

```bash
# 从模板复制
cp examples/.env.example .env

# 用你喜欢的编辑器编辑
code .env    # VS Code
vim .env     # Vim
```

> **提示**：三种方式最终都是写入同一个 `.env` 文件，可以随时切换使用。例如先用 OpenAkita Desktop 完成基本配置，再手动编辑 `.env` 微调参数。

---

## 一、Telegram 配置教程

> Telegram 是最容易配置的 IM 通道，只需一个 Bot Token 即可，无需公网 IP。

### 1.1 前置条件

- 一个 Telegram 账号（手机号注册即可）
- 能访问 Telegram（大陆环境需要代理）

### 1.2 平台端申请步骤

#### 第一步：打开 BotFather

在 Telegram 搜索栏中搜索 `@BotFather`，点击进入对话。BotFather 是 Telegram 官方的机器人管理工具。

<!-- 📸 配图：Telegram 搜索 BotFather 的界面截图 -->
> **[配图位]** 在 Telegram 中搜索 @BotFather 并打开对话

#### 第二步：创建新机器人

1. 向 BotFather 发送 `/newbot` 命令
2. BotFather 会要求你为机器人设置一个**显示名称**（name），例如 `My OpenAkita Bot`
3. 接着要求设置一个**用户名**（username），必须以 `bot` 结尾，例如 `my_openakita_bot`

<!-- 📸 配图：与 BotFather 对话创建机器人的完整流程截图 -->
> **[配图位]** 与 BotFather 对话，创建机器人的完整流程

#### 第三步：获取 Bot Token

创建完成后，BotFather 会返回一条消息，其中包含你的 **Bot Token**，格式类似：

```
123456789:ABCDefGH-ijklMNOPqrstUVWxyz1234567
```

⚠️ **重要**：妥善保管你的 Bot Token，不要泄露给他人。拥有 Token 就能完全控制你的机器人。

<!-- 📸 配图：BotFather 返回 Bot Token 的消息截图（Token 部分打码） -->
> **[配图位]** BotFather 返回的 Bot Token 消息

#### 第四步：（可选）设置机器人头像和描述

- 发送 `/setuserpic` — 设置机器人头像
- 发送 `/setdescription` — 设置机器人简介（用户首次打开对话时显示）
- 发送 `/setabouttext` — 设置"关于"信息
- 发送 `/setcommands` — 设置命令菜单（如 `/start` - 开始对话）

<!-- 📸 配图：设置机器人头像和描述的界面截图 -->
> **[配图位]** 设置机器人基本信息

#### 第五步：（可选）配置机器人隐私

默认情况下，机器人在群聊中只能收到 `/command` 格式的消息和 @机器人 的消息。如果需要接收群聊中的所有消息：

1. 向 BotFather 发送 `/setprivacy`
2. 选择你的机器人
3. 选择 `Disable`（关闭隐私模式）

> **注意**：关闭隐私模式后，机器人会收到群聊中的所有消息，请根据实际需要决定。

### 1.3 OpenAkita 配置

在平台端获取 Bot Token 后，通过以下任一方式配置 OpenAkita：

#### 方式 A：OpenAkita Desktop 桌面程序（推荐）

1. 打开 OpenAkita Desktop
2. 进入 **「IM 通道」** 配置步骤（或在配置页面切换到 IM 标签）
3. 在 **Telegram** 区域：
   - 将 `TELEGRAM_ENABLED` 开关打开
   - 在 `TELEGRAM_BOT_TOKEN` 输入框中粘贴 Bot Token
   - 如需代理，在 `TELEGRAM_PROXY` 中填写代理地址
4. 点击 **「保存」**

<!-- 📸 配图：OpenAkita Desktop 中 Telegram 配置表单的截图，标注 Token 和 Proxy 输入框 -->
> **[配图位]** OpenAkita Desktop — Telegram 配置表单，填入 Bot Token 和代理地址

<!-- 📸 配图：OpenAkita Desktop 状态页面显示 Telegram 通道在线状态 -->
> **[配图位]** OpenAkita Desktop — 保存后状态页面显示 Telegram 在线 🟢

#### 方式 B：CLI 交互式向导

```bash
openakita setup
```

在 Step 4（IM Channels）中选择 `[1] Telegram`，按提示输入：

```
Telegram Bot Configuration

To create a bot, message @BotFather on Telegram and use /newbot

Enter your Bot Token: ********
Require pairing code for new users? [Y/n]: y
Use a proxy for Telegram? (recommended in mainland China) [y/N]: y
Enter proxy URL [http://127.0.0.1:7890]:
```

#### 方式 C：手动编辑 .env 文件

在项目根目录的 `.env` 文件中添加以下配置：

```bash
# --- Telegram ---
TELEGRAM_ENABLED=true
TELEGRAM_BOT_TOKEN=你的Bot Token

# 代理设置（大陆环境需要）
TELEGRAM_PROXY=http://127.0.0.1:7890
# 或 socks5 代理
# TELEGRAM_PROXY=socks5://127.0.0.1:1080
```

#### 可选配置项

```bash
# 配对安全机制（防止未授权用户访问）
TELEGRAM_REQUIRE_PAIRING=true
TELEGRAM_PAIRING_CODE=your-secret-code

# Webhook 模式（需要公网 HTTPS URL，不推荐新手使用）
# TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhook/telegram
```

### 1.4 部署模式

| 模式 | 需要公网 IP | 说明 |
|------|------------|------|
| Long Polling（默认） | ❌ | 适配器主动轮询 Telegram 服务器，适合绝大多数场景 |
| Webhook | ✅ | 需要公网 HTTPS URL，适合高并发场景 |

### 1.5 验证与测试

1. 启动 OpenAkita
2. 在 Telegram 中搜索你的机器人用户名，点击进入对话
3. 点击 **Start** 按钮（或发送 `/start`）
4. 如果启用了配对机制，按提示输入配对码
5. 发送一条测试消息（如"你好"），等待机器人回复

<!-- 📸 配图：在 Telegram 中与机器人对话的效果截图 -->
> **[配图位]** 机器人正常回复消息的效果展示

### 1.6 常见问题

| 问题 | 解决方案 |
|------|---------|
| 无法连接 Telegram | 检查代理设置，确认 `TELEGRAM_PROXY` 配置正确 |
| 机器人不回复 | 检查日志输出，确认 Token 正确，代理可用 |
| 群聊中收不到消息 | 检查是否需要关闭隐私模式（通过 BotFather `/setprivacy`） |
| 提示"配对码错误" | 确认 `TELEGRAM_PAIRING_CODE` 与输入一致 |

---

## 二、飞书（Lark）配置教程

> 飞书通过 WebSocket 长连接接入，无需公网 IP，适合企业内部使用。

### 2.1 前置条件

- 一个**企业飞书账号**（个人版不支持创建自建应用）
- 飞书管理员或具有应用开发权限的账号

### 2.2 平台端申请步骤

#### 第一步：登录飞书开发者后台

打开浏览器，访问 [飞书开放平台](https://open.feishu.cn/)，使用你的企业飞书账号登录。

<!-- 📸 配图：飞书开放平台首页截图 -->
> **[配图位]** 飞书开放平台首页

#### 第二步：创建企业自建应用

1. 点击页面顶部的 **「开发者后台」**（或直接访问 https://open.feishu.cn/app）
2. 点击 **「创建企业自建应用」** 按钮
3. 填写应用信息：
   - **应用名称**：如 `OpenAkita Bot`
   - **应用描述**：如 `AI 智能助手`
   - **应用图标**：上传一个合适的图标（可选）
4. 点击 **「创建」**

<!-- 📸 配图：创建企业自建应用的界面截图 -->
> **[配图位]** 创建企业自建应用

<!-- 📸 配图：填写应用信息的表单截图 -->
> **[配图位]** 填写应用名称、描述和图标

#### 第三步：获取应用凭证

1. 创建完成后，进入应用详情页
2. 在左侧菜单中找到 **「凭证与基础信息」**
3. 记录以下信息：
   - **App ID**：格式如 `cli_a5xxxxxxxxxxxxx`
   - **App Secret**：点击"显示"后复制

<!-- 📸 配图：凭证与基础信息页面，标注 App ID 和 App Secret 位置 -->
> **[配图位]** 获取 App ID 和 App Secret

⚠️ **重要**：App Secret 是敏感信息，不要泄露。如果泄露，请在此页面重新生成。

#### 第四步：添加机器人能力

1. 在左侧菜单中找到 **「应用能力」→「添加应用能力」**
2. 找到 **「机器人」** 能力，点击 **「添加」**

<!-- 📸 配图：添加机器人能力的界面截图 -->
> **[配图位]** 为应用添加机器人能力

#### 第五步：配置应用权限

在左侧菜单中找到 **「权限管理」**，搜索并开启以下权限：

| 权限标识 | 权限名称 | 用途 |
|---------|---------|------|
| `im:message` | 获取与发送消息 | 接收和发送聊天消息 |
| `im:message.create_v1` | 以应用身份发消息 | 机器人主动发送消息 |
| `im:resource` | 获取消息中的资源文件 | 接收图片、语音等媒体 |
| `im:file` | 上传/下载文件 | 文件传输能力 |

操作步骤：
1. 点击 **「权限管理」**
2. 在搜索框中输入权限标识（如 `im:message`）
3. 找到对应权限，点击 **「开通」**
4. 重复以上步骤，开通所有必要权限

<!-- 📸 配图：权限管理页面，展示搜索和开通权限的操作 -->
> **[配图位]** 搜索并开通所需权限

#### 第六步：配置事件订阅（关键步骤！）

1. 在左侧菜单中找到 **「事件与回调」**
2. ⚠️ **在「事件配置方式」中，选择「使用长连接接收事件」**
   - 这是关键步骤！选择长连接模式后，无需公网 IP
   - 不要选择「将事件发送至开发者服务器（Webhook）」
3. 在「添加事件」中搜索并添加：
   - **im.message.receive_v1** — 接收消息

<!-- 📸 配图：事件与回调页面，标注"使用长连接接收事件"选项 -->
> **[配图位]** 选择"使用长连接接收事件"（而非 Webhook）

<!-- 📸 配图：添加 im.message.receive_v1 事件的操作截图 -->
> **[配图位]** 添加"接收消息"事件

#### 第七步：创建版本并发布

1. 在左侧菜单中找到 **「版本管理与发布」**
2. 点击 **「创建版本」**
3. 填写版本号（如 `1.0.0`）和更新说明
4. 设置 **可用范围**：选择允许使用该应用的部门或人员
5. 点击 **「保存」**，然后 **「申请发布」**
6. 等待管理员审批（如果你就是管理员，可以直接在管理后台通过）

<!-- 📸 配图：创建版本并发布的界面截图 -->
> **[配图位]** 创建版本并发布应用

<!-- 📸 配图：设置可用范围的界面截图 -->
> **[配图位]** 设置应用可用范围

> **提示**：如果你的飞书组织较小或你是管理员，审批通常可以立即通过。如果遇到审批问题，请联系组织的飞书管理员。

### 2.3 OpenAkita 配置

先安装飞书依赖（OpenAkita Desktop 用户可跳过，依赖会自动安装）：

```bash
pip install openakita[feishu]
```

然后通过以下任一方式填入凭证：

#### 方式 A：OpenAkita Desktop 桌面程序（推荐）

1. 打开 OpenAkita Desktop
2. 进入 **「IM 通道」** 配置步骤
3. 在 **飞书** 区域：
   - 将 `FEISHU_ENABLED` 开关打开
   - 在 `FEISHU_APP_ID` 中粘贴 App ID
   - 在 `FEISHU_APP_SECRET` 中粘贴 App Secret
4. 点击 **「保存」**

<!-- 📸 配图：OpenAkita Desktop 中飞书配置表单的截图，标注 App ID 和 App Secret 输入框 -->
> **[配图位]** OpenAkita Desktop — 飞书配置表单

<!-- 📸 配图：OpenAkita Desktop 状态页面显示飞书通道在线状态 -->
> **[配图位]** OpenAkita Desktop — 保存后状态页面显示飞书在线 🟢

#### 方式 B：CLI 交互式向导

```bash
openakita setup
```

在 Step 4（IM Channels）中选择 `[2] Feishu (Lark)`，按提示输入：

```
Feishu (Lark) Configuration

Enter App ID: cli_a5xxxxxxxxxxxxx
Enter App Secret: ********
```

#### 方式 C：手动编辑 .env 文件

在 `.env` 文件中添加：

```bash
# --- 飞书 ---
FEISHU_ENABLED=true
FEISHU_APP_ID=cli_a5xxxxxxxxxxxxx    # 替换为你的 App ID
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxx  # 替换为你的 App Secret
```

### 2.4 验证与测试

1. 启动 OpenAkita，观察日志输出，应出现：
   ```
   Feishu adapter: WebSocket started in background
   ```
2. 打开飞书，在搜索栏中搜索你创建的应用名称（如 `OpenAkita Bot`）
3. 点击进入对话，发送一条消息
4. 观察日志和机器人回复

<!-- 📸 配图：飞书中与机器人对话的效果截图 -->
> **[配图位]** 飞书机器人正常回复消息的效果展示

### 2.5 常见问题

| 问题 | 解决方案 |
|------|---------|
| 搜索不到机器人 | 确认应用已发布且你在可用范围内 |
| 消息收不到 | 检查是否选择了「长连接模式」（不是 Webhook） |
| 权限不足 | 确认所有必要权限已开通，且应用已重新发布 |
| 日志显示连接失败 | 检查 App ID 和 App Secret 是否正确 |
| Token 过期 | SDK 自动管理 Token 刷新，无需手动处理 |

---

## 三、钉钉配置教程

> 钉钉使用 Stream 模式（WebSocket）接入，无需公网 IP，企业内部部署友好。

### 3.1 前置条件

- 一个**企业钉钉账号**
- 钉钉管理员或具有应用开发权限的账号

### 3.2 平台端申请步骤

#### 第一步：登录钉钉开发者后台

打开浏览器，访问 [钉钉开放平台](https://open-dev.dingtalk.com/)，使用钉钉扫码或账号密码登录。

<!-- 📸 配图：钉钉开放平台首页截图 -->
> **[配图位]** 钉钉开放平台首页

#### 第二步：创建企业内部应用

1. 登录后，在顶部导航栏点击 **「应用开发」**
2. 选择 **「企业内部开发」**
3. 点击 **「创建应用」** 按钮
4. 填写应用信息：
   - **应用名称**：如 `OpenAkita Bot`
   - **应用描述**：如 `AI 智能助手`
   - **应用图标**：上传图标（可选）
5. 点击 **「确定创建」**

<!-- 📸 配图：钉钉开放平台 → 应用开发 → 企业内部开发页面截图 -->
> **[配图位]** 进入企业内部开发页面

<!-- 📸 配图：创建应用的表单截图 -->
> **[配图位]** 填写并创建企业内部应用

#### 第三步：获取应用凭证

1. 创建成功后，进入应用管理页面
2. 在左侧菜单中找到 **「基础信息」→「应用凭证」**
3. 记录以下信息：
   - **AppKey**（也叫 Client ID）：格式如 `dingxxxxxxxxxx`
   - **AppSecret**（也叫 Client Secret）：点击"显示"后复制

<!-- 📸 配图：应用凭证页面，标注 AppKey 和 AppSecret 位置 -->
> **[配图位]** 获取 AppKey（Client ID）和 AppSecret（Client Secret）

#### 第四步：开启机器人功能

1. 在左侧菜单中找到 **「应用功能」→「机器人」**
2. 点击开启 **「机器人配置」** 开关
3. 填写机器人基本信息：
   - **机器人名称**：如 `OpenAkita`
   - **机器人图标**：上传图标
   - **机器人简介**：简要描述机器人功能

<!-- 📸 配图：开启机器人配置的界面截图 -->
> **[配图位]** 开启并配置机器人功能

#### 第五步：设置消息接收模式（关键步骤！）

在机器人配置页面中：

1. 找到 **「消息接收模式」** 设置项
2. ⚠️ **选择「Stream 模式」**
   - 这是关键步骤！Stream 模式使用 WebSocket 长连接，无需公网 IP
   - **不要选择「HTTP 模式」**，HTTP 模式需要公网可访问的回调地址
3. 点击 **「发布」** 保存配置

<!-- 📸 配图：消息接收模式选择页面，标注 Stream 模式选项 -->
> **[配图位]** 选择 Stream 模式（而非 HTTP 模式）

#### 第六步：配置权限

1. 在左侧菜单中找到 **「权限管理」**
2. 搜索并申请以下权限：

| 权限名称 | 说明 |
|---------|------|
| 企业内机器人发送消息 | 允许机器人主动发送消息 |
| 读取用户信息 | 获取用户基本信息 |

操作步骤：
1. 点击 **「权限管理」**
2. 在「个人权限」和「企业权限」标签中搜索所需权限
3. 点击 **「申请权限」**

<!-- 📸 配图：权限管理页面截图 -->
> **[配图位]** 配置机器人所需权限

#### 第七步：发布应用

1. 在左侧菜单中找到 **「版本管理与发布」**
2. 点击 **「发布」** 按钮
3. 设置可见范围（选择允许使用的部门或员工）
4. 提交发布

<!-- 📸 配图：发布应用的界面截图 -->
> **[配图位]** 发布应用并设置可见范围

### 3.3 OpenAkita 配置

先安装钉钉依赖（OpenAkita Desktop 用户可跳过，依赖会自动安装）：

```bash
pip install openakita[dingtalk]
```

然后通过以下任一方式填入凭证：

#### 方式 A：OpenAkita Desktop 桌面程序（推荐）

1. 打开 OpenAkita Desktop
2. 进入 **「IM 通道」** 配置步骤
3. 在 **钉钉** 区域：
   - 将 `DINGTALK_ENABLED` 开关打开
   - 在 `DINGTALK_CLIENT_ID` 中粘贴 AppKey / Client ID
   - 在 `DINGTALK_CLIENT_SECRET` 中粘贴 AppSecret / Client Secret
4. 点击 **「保存」**

<!-- 📸 配图：OpenAkita Desktop 中钉钉配置表单的截图 -->
> **[配图位]** OpenAkita Desktop — 钉钉配置表单

<!-- 📸 配图：OpenAkita Desktop 状态页面显示钉钉通道在线状态 -->
> **[配图位]** OpenAkita Desktop — 保存后状态页面显示钉钉在线 🟢

#### 方式 B：CLI 交互式向导

```bash
openakita setup
```

在 Step 4（IM Channels）中选择 `[4] DingTalk (钉钉)`，按提示输入：

```
DingTalk Configuration

Enter App Key: dingxxxxxxxxxx
Enter App Secret: ********
```

#### 方式 C：手动编辑 .env 文件

在 `.env` 文件中添加：

```bash
# --- 钉钉 ---
DINGTALK_ENABLED=true
DINGTALK_CLIENT_ID=dingxxxxxxxxxx      # 替换为你的 AppKey / Client ID
DINGTALK_CLIENT_SECRET=xxxxxxxxxxxxxxxxxx  # 替换为你的 AppSecret / Client Secret
```

### 3.4 验证与测试

1. 启动 OpenAkita，观察日志输出，应出现：
   ```
   DingTalk Stream client starting...
   ```
2. 打开钉钉，有以下方式找到机器人：
   - **单聊**：在搜索栏搜索机器人名称，直接发消息
   - **群聊**：将机器人拉入群聊，在群中 @机器人 发消息
3. 发送一条测试消息，观察日志和回复

<!-- 📸 配图：钉钉中与机器人对话的效果截图 -->
> **[配图位]** 钉钉机器人正常回复消息的效果展示

#### 如何将机器人拉入群聊

1. 打开一个群聊（或创建新群）
2. 点击群设置（右上角 `⋮` 或群名称）
3. 找到 **「机器人」** → **「添加机器人」**
4. 在列表中找到你创建的机器人，点击添加
5. 在群中发送 `@OpenAkita 你好` 即可触发回复

<!-- 📸 配图：在群聊中添加机器人的操作截图 -->
> **[配图位]** 将机器人拉入群聊

### 3.5 常见问题

| 问题 | 解决方案 |
|------|---------|
| 收不到消息 | 确认后台已选择 Stream 模式（不是 HTTP 模式） |
| Stream 连接失败 | 检查 Client ID 和 Client Secret 是否正确 |
| 群聊中 @机器人无回复 | 确认机器人已添加到群聊中 |
| 图片/文件发送异常 | 钉钉机器人对富媒体支持有限，部分会降级为链接 |
| 应用不可见 | 检查应用是否已发布，且当前用户在可见范围内 |

---

## 四、企业微信配置教程

> 企业微信通过 HTTP 回调接入，**需要公网可访问的 URL**。配置流程需要注意顺序：先配 OpenAkita，再创建机器人。

### 4.1 前置条件

- **企业微信管理员账号**
- **公网可访问的 URL**（IP 即可，不需要备案域名）
- 如果没有公网 IP，需要使用内网穿透工具（ngrok / frp / cpolar）

### 4.2 网络准备

企业微信**不支持 WebSocket/长连接模式**，只能通过 HTTP 回调接收消息。你需要确保有一个公网可访问的地址。

#### 方案一：公网服务器（推荐）

如果你的服务器有公网 IP，直接使用即可：

```
回调 URL: http://你的公网IP:9880/callback
```

#### 方案二：路由器端口转发

如果你有公网 IP 但服务运行在内网机器上：

1. 登录路由器管理页面
2. 找到「端口转发」或「NAT 规则」
3. 将外部端口（如 9880）转发到内网机器 IP 的 9880 端口

#### 方案三：内网穿透（无公网 IP）

**使用 ngrok：**

```bash
# 安装 ngrok: https://ngrok.com/download
ngrok http 9880

# 获取公网 URL，如：
# https://abc123.ngrok-free.app
# 回调 URL: https://abc123.ngrok-free.app/callback
```

<!-- 📸 配图：ngrok 启动后显示公网 URL 的终端截图 -->
> **[配图位]** ngrok 启动成功，显示公网映射地址

**使用 cpolar（国内访问更友好）：**

```bash
# 安装 cpolar: https://www.cpolar.com/
cpolar http 9880
```

**使用 frp（自建内网穿透）：**

```bash
# 在有公网 IP 的服务器上部署 frps（服务端）
# 在本地配置 frpc（客户端）:
[wework]
type = http
local_port = 9880
custom_domains = your-domain.com
```

### 4.3 平台端申请步骤

> ⚠️ **重要顺序提醒**：企业微信创建机器人时会**立即验证回调 URL**，所以必须**先完成 4.4 节的 OpenAkita 配置并启动服务**，确保回调端口可从公网访问，**然后再执行以下步骤**。

#### 第一步：获取企业 ID（Corp ID）

1. 打开浏览器，访问 [企业微信管理后台](https://work.weixin.qq.com/)
2. 使用管理员账号登录
3. 点击左侧菜单 **「我的企业」**
4. 在 **「企业信息」** 页面底部找到 **「企业ID」**
5. 复制企业 ID（格式如 `ww1234567890abcdef`）

<!-- 📸 配图：企业微信管理后台「我的企业 → 企业信息」页面，标注企业 ID 位置 -->
> **[配图位]** 在企业微信管理后台获取企业 ID

#### 第二步：配置 OpenAkita 并启动（先于创建机器人！）

⚠️ 请先跳到 **4.4 节** 完成 `.env` 配置并启动 OpenAkita，确保回调端口已监听。

```bash
# 先在 .env 中配好参数（见 4.4 节），然后启动
python -m openakita
# 或
openakita start
```

确认日志中出现 `WeWorkBot adapter started`，端口 9880 已在监听。

#### 第三步：创建智能机器人

1. 在企业微信管理后台，点击左侧菜单 **「应用管理」**
2. 找到 **「智能机器人」** 板块
3. 点击 **「创建」** 按钮
4. 填写机器人基本信息：
   - **机器人名称**：如 `OpenAkita`
   - **机器人描述**：如 `AI 智能助手`
   - **机器人头像**：上传图标

<!-- 📸 配图：企业微信管理后台 → 应用管理 → 智能机器人 → 创建页面截图 -->
> **[配图位]** 创建智能机器人

#### 第四步：配置回调地址（关键步骤！）

在机器人配置页面，找到 **「接收消息服务器配置」**（也叫回调配置）：

1. **URL**：填写你的回调地址
   - 公网服务器：`http://你的公网IP:9880/callback`
   - ngrok：`https://abc123.ngrok-free.app/callback`
2. **Token**：系统会自动生成，也可自定义。**记下这个 Token**
3. **EncodingAESKey**：系统会自动生成，也可自定义。**记下这个 EncodingAESKey**
4. 点击 **「保存」**

<!-- 📸 配图：接收消息服务器配置界面，标注 URL、Token、EncodingAESKey 三个字段 -->
> **[配图位]** 配置回调 URL、Token 和 EncodingAESKey

> ⚠️ 点击保存时，企业微信会立即向你的 URL 发送 GET 验证请求。如果 OpenAkita 未启动或网络不通，会提示"网络失败"。

保存成功后，**将 Token 和 EncodingAESKey 更新到 `.env` 文件中**，并重启 OpenAkita。

#### 第五步：设置可见范围（必须！）

1. 在机器人配置页面，找到 **「可见范围」** 设置
2. 点击 **「添加」**
3. 选择需要使用机器人的 **部门** 或 **员工**
4. 点击 **「确定」**

<!-- 📸 配图：设置可见范围的界面截图 -->
> **[配图位]** 设置机器人可见范围

⚠️ **重要**：只有在可见范围内的企业成员才能使用机器人。企业外部人员（客户、供应商等）无法使用。

| 场景 | 是否可用 |
|------|---------|
| 可见范围内的员工单聊 | ✅ |
| 可见范围内的员工群聊 @机器人 | ✅ |
| **不在可见范围的员工** | ❌ 看不到机器人 |
| **企业外部人员** | ❌ 无法使用 |

### 4.4 OpenAkita 配置

先安装企业微信依赖（OpenAkita Desktop 用户可跳过，依赖会自动安装）：

```bash
pip install openakita[wework]
```

然后通过以下任一方式填入凭证：

> ⚠️ **注意**：企业微信需要先配好 OpenAkita 并启动，再去管理后台创建机器人（创建时会验证回调 URL），所以建议先填好 Corp ID 和端口，启动服务后再补充 Token 和 AES Key。

#### 方式 A：OpenAkita Desktop 桌面程序（推荐）

1. 打开 OpenAkita Desktop
2. 进入 **「IM 通道」** 配置步骤
3. 在 **企业微信** 区域：
   - 将 `WEWORK_ENABLED` 开关打开
   - 在 `WEWORK_CORP_ID` 中粘贴企业 ID
   - 在 `WEWORK_TOKEN` 中粘贴回调 Token（从企业微信后台获取）
   - 在 `WEWORK_ENCODING_AES_KEY` 中粘贴 AES Key（从企业微信后台获取）
   - `WEWORK_CALLBACK_PORT` 默认 9880，如有端口冲突可修改
4. 点击 **「保存」** 并启动/重启服务

<!-- 📸 配图：OpenAkita Desktop 中企业微信配置表单的截图，标注各个输入框 -->
> **[配图位]** OpenAkita Desktop — 企业微信配置表单，填入 Corp ID、Token、AES Key

<!-- 📸 配图：OpenAkita Desktop 状态页面显示企业微信通道在线状态 -->
> **[配图位]** OpenAkita Desktop — 保存后状态页面显示企业微信在线 🟢

#### 方式 B：CLI 交互式向导

```bash
openakita setup
```

在 Step 4（IM Channels）中选择 `[3] WeCom (企业微信)`，按提示输入：

```
WeCom Configuration

Note: WeCom callback requires a public URL (use ngrok/frp/cpolar)

Enter Corp ID: ww1234567890abcdef

Callback Configuration (required for Smart Bot):

Get these from WeCom admin -> Smart Bot -> Receive Messages settings

Enter callback Token: xxxxxxxxxx
Enter EncodingAESKey: xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Callback port [9880]:
```

#### 方式 C：手动编辑 .env 文件

在 `.env` 文件中添加：

```bash
# --- 企业微信 ---
WEWORK_ENABLED=true
WEWORK_CORP_ID=ww1234567890abcdef    # 替换为你的企业 ID

# 回调加解密配置（在机器人配置页获取，必填！）
WEWORK_TOKEN=your-token-here              # 替换为回调配置中的 Token
WEWORK_ENCODING_AES_KEY=your-aes-key-here # 替换为回调配置中的 EncodingAESKey
WEWORK_CALLBACK_PORT=9880                 # 回调监听端口，默认 9880
```

### 4.5 配置流程总结

企业微信的配置顺序很重要，请按以下流程操作：

```
┌─────────────────────────────────────────────────────────────┐
│ 1. 获取企业 ID（管理后台 → 我的企业 → 企业信息）              │
│                          ↓                                  │
│ 2. 在 .env 中填写 WEWORK_CORP_ID、WEWORK_ENABLED=true      │
│                          ↓                                  │
│ 3. 准备公网访问（直接公网 / ngrok / frp）                     │
│                          ↓                                  │
│ 4. 启动 OpenAkita，确认回调端口已监听                         │
│                          ↓                                  │
│ 5. 去管理后台创建智能机器人                                   │
│                          ↓                                  │
│ 6. 配置回调 URL，获取 Token 和 EncodingAESKey                │
│                          ↓                                  │
│ 7. 将 Token 和 EncodingAESKey 更新到 .env，重启 OpenAkita    │
│                          ↓                                  │
│ 8. 设置机器人可见范围                                        │
│                          ↓                                  │
│ 9. 在企业微信中测试对话                                      │
└─────────────────────────────────────────────────────────────┘
```

### 4.6 消息回复机制

企业微信智能机器人使用 **stream 流式被动回复** 机制：

```
用户发消息 → 企业微信推送加密 JSON → OpenAkita 回调接收
                                        ↓
                           创建 stream 流式会话
                                        ↓
                           Agent 处理并生成回复
                                        ↓
                           stream 返回完整内容给用户
```

**回复能力**：
- ✅ 文本/Markdown：通过 stream 发送
- ✅ 图片：通过 stream msg_item 发送（JPG/PNG，≤10MB，单条最多 10 张）
- ❌ 语音：不支持发送
- ❌ 文件：不支持发送，降级为文本描述
- ❌ 视频：不支持发送

**接收能力**：
- ✅ 文字：单聊 + 群聊（群聊需 @机器人）
- ✅ 图文混排：单聊 + 群聊（群聊需 @机器人）
- ⚠️ 图片：**仅单聊**，群聊中不支持
- ❌ 语音：不支持接收
- ❌ 文件：不支持接收
- ❌ 视频：不支持接收

**超时降级**：stream 会话超时（5.5 分钟）后，自动降级为 response_url 纯文本回复。

### 4.7 验证与测试

1. 确认日志中出现 `WeWorkBot adapter started`
2. 确保回调 URL 从公网可访问
3. 在企业微信中搜索并打开机器人对话
4. 发送一条消息，观察日志和回复
5. 也可将机器人拉入群聊，@机器人 发消息测试

<!-- 📸 配图：企业微信中与机器人对话的效果截图 -->
> **[配图位]** 企业微信机器人正常回复消息的效果展示

### 4.8 常见问题

| 问题 | 解决方案 |
|------|---------|
| 创建机器人时提示"网络失败" | 回调 URL 不通，确保 OpenAkita 已启动，端口已监听，公网可访问 |
| URL 验证失败 | 确认 Token 和 EncodingAESKey 与企业微信后台一致 |
| 签名校验失败 | 检查 Corp ID 是否正确 |
| 端口被占用 | 修改 `WEWORK_CALLBACK_PORT` 为其他端口 |
| 内网穿透不稳定 | 建议使用付费版 ngrok 或自建 frp |
| 收不到消息 | 确认机器人回调地址配置正确，且服务器端口可访问 |
| 群聊中其他人 @不回复 | 检查该员工是否在机器人的「可见范围」内 |
| 回复乱码 | 一般不会出现，适配器已处理编码问题 |
| 图片发不出来 | 确认图片为 JPG/PNG 格式且 ≤10MB，stream 会话未超时 |

---

## 五、QQ 官方机器人配置教程

> QQ 官方机器人通过 [QQ 开放平台](https://q.qq.com) 接入，使用官方 botpy SDK，支持频道、群聊和单聊消息。

### 5.1 前置条件

- 一个 QQ 开放平台账号（需前往 [q.qq.com](https://q.qq.com) 注册）
- 已创建的机器人应用，获取 **AppID** 和 **AppSecret**

### 5.2 在 QQ 开放平台创建机器人

1. 访问 [QQ 开放平台](https://q.qq.com)，登录并进入控制台
2. 点击 **「创建机器人」**，填写机器人名称、描述等基本信息
3. 创建成功后，在 **「开发设置」** 页面获取：
   - **AppID** — 机器人的唯一标识
   - **AppSecret** — 用于身份鉴权（注意保密）

<!-- 📸 配图：QQ 开放平台控制台 — 创建机器人 -->
> **[配图位]** QQ 开放平台控制台 — 机器人管理页面

4. 在 **「功能配置」** 中配置机器人的事件订阅（如消息接收等）
5. 如果需要群聊和单聊功能，需要在 **「沙箱配置」** 或上线审核后才能生效

> **沙箱模式**：开发调试阶段建议先开启沙箱模式。沙箱环境仅限沙箱频道内可用，不会影响正式环境。

### 5.3 OpenAkita 配置

先安装 QQ 官方机器人依赖（OpenAkita Desktop 用户可跳过，依赖会自动安装）：

```bash
pip install openakita[qqbot]
```

然后通过以下任一方式配置：

#### 方式 A：OpenAkita Desktop 桌面程序（推荐）

1. 打开 OpenAkita Desktop
2. 进入 **「IM 通道」** 配置步骤
3. 在 **QQ 官方机器人** 区域：
   - 将 `QQBOT_ENABLED` 开关打开
   - 填写 `QQBOT_APP_ID`（AppID）
   - 填写 `QQBOT_APP_SECRET`（AppSecret）
   - 如需沙箱测试，勾选 `QQBOT_SANDBOX`
4. 点击 **「保存」**

<!-- 📸 配图：OpenAkita Desktop 中 QQ 官方机器人配置表单的截图 -->
> **[配图位]** OpenAkita Desktop — QQ 官方机器人配置表单

#### 方式 B：CLI 交互式向导

```bash
openakita setup
```

在 Step 4（IM Channels）中选择 `[5] QQ 官方机器人`，按提示输入：

```
QQ Official Bot Configuration

请前往 https://q.qq.com 创建机器人并获取凭证

Enter AppID: 12345678
Enter AppSecret: ********
Enable sandbox mode? [y/N]: n
```

#### 方式 C：手动编辑 .env 文件

在 `.env` 文件中添加：

```bash
# --- QQ 官方机器人 ---
QQBOT_ENABLED=true
QQBOT_APP_ID=你的AppID
QQBOT_APP_SECRET=你的AppSecret
QQBOT_SANDBOX=false          # 设为 true 开启沙箱模式
```

### 5.4 验证与测试

1. 启动 OpenAkita，日志应显示：
   ```
   QQ Official Bot ready (user: 你的机器人名称)
   ```
2. 在 QQ 中 @机器人 发送消息（频道或群聊）
3. 观察日志和机器人回复

<!-- 📸 配图：QQ 频道中与机器人对话的效果截图 -->
> **[配图位]** QQ 官方机器人正常回复消息的效果展示

### 5.5 富媒体发送说明

QQ 官方 API 在不同聊天场景下的富媒体支持有差异：

| 场景 | 文字 | 图片 | 语音 | 文件 | 视频 |
|------|------|------|------|------|------|
| 频道 | ✅ | ✅ 直接 URL | ❌ | ❌ | ❌ |
| 群聊 | ✅ | ✅ 需公网 URL 上传 | ⚠️ 需 silk + 公网 URL | ❌ 暂未开放 | ⚠️ 需公网 URL |
| 单聊 | ✅ | ✅ 需公网 URL 上传 | ⚠️ 需 silk + 公网 URL | ❌ 暂未开放 | ⚠️ 需公网 URL |

> **注意**：群聊和单聊的图片/语音/视频发送需要两步操作：先通过富媒体 API 上传文件获取 `file_info`，再发送消息。**仅支持公网 URL**（`file_data` 本地文件上传暂未开放）。

### 5.6 常见问题

| 问题 | 解决方案 |
|------|---------|
| 鉴权失败 | 确认 AppID 和 AppSecret 正确，检查是否在开放平台开启了对应的事件订阅 |
| 频繁断线重连 | 适配器支持自动重连（指数退避，初始 5 秒，最大 120 秒），连接成功后重置 |
| 群聊收不到消息 | 确认机器人已上线审核通过或在沙箱环境中配置了测试群 |
| 图片发不出去 | 群聊/单聊需要公网 URL，本地文件上传暂不支持 |
| 语音发不出去 | 需要 silk 格式 + 公网 URL，目前 OpenAkita 会降级为文本提示 |
| 文件发不出去 | QQ 官方 API `file_type=4` 暂未开放，OpenAkita 会降级为文本提示 |
| 沙箱模式下无法群聊 | 沙箱仅限频道测试，群聊需要正式上线 |

---

## 六、OneBot（通用协议）配置教程

> OneBot 是一种通用的聊天机器人协议（v11），可对接任何兼容 OneBot 标准的实现端（如 NapCat、Lagrange 等），不限于 QQ 平台。

### 6.1 前置条件

- 一个 **QQ 账号**（或其他 OneBot 兼容平台的账号）
- 一台部署 OneBot 服务的机器（可以和 OpenAkita 在同一台）

### 6.2 部署 OneBot 服务器

OpenAkita 通过 OneBot v11 协议的 WebSocket 与中间层通信。你需要先部署一个 OneBot 实现。

#### 方案一：NapCat（推荐）

NapCat 是目前比较活跃的 QQ OneBot 实现。

**安装步骤：**

1. 访问 [NapCat 项目主页](https://github.com/NapNeko/NapCatQQ)
2. 根据你的操作系统下载对应的安装包
3. 解压并按照文档完成初始安装

<!-- 📸 配图：NapCat GitHub 页面或下载页面截图 -->
> **[配图位]** NapCat 项目下载页面

**配置 WebSocket：**

1. 启动 NapCat，按照引导完成 QQ 账号登录（通常需要扫码）
2. 进入 NapCat 配置页面
3. 启用 **「正向 WebSocket」**，设置地址为 `ws://127.0.0.1:8080`
4. （可选）设置 Access Token 用于连接鉴权
5. 保存配置并重启 NapCat

<!-- 📸 配图：NapCat 配置正向 WebSocket 的界面截图 -->
> **[配图位]** NapCat 配置正向 WebSocket

#### 方案二：Lagrange.OneBot

Lagrange 是另一个 OneBot 实现，使用 .NET 开发。

1. 访问 [Lagrange.Core 项目](https://github.com/LagrangeDev/Lagrange.Core)
2. 下载并安装
3. 配置正向 WebSocket，启动服务

<!-- 📸 配图：Lagrange 配置页面截图 -->
> **[配图位]** Lagrange.OneBot 配置界面

### 6.3 OpenAkita 配置

先安装 OneBot 依赖（OpenAkita Desktop 用户可跳过，依赖会自动安装）：

```bash
pip install openakita[onebot]
```

然后通过以下任一方式配置：

#### 方式 A：OpenAkita Desktop 桌面程序（推荐）

1. 打开 OpenAkita Desktop
2. 进入 **「IM 通道」** 配置步骤
3. 在 **OneBot** 区域：
   - 将 `ONEBOT_ENABLED` 开关打开
   - 在 `ONEBOT_WS_URL` 中填写 OneBot WebSocket 地址（默认 `ws://127.0.0.1:8080`）
   - （可选）在 `ONEBOT_ACCESS_TOKEN` 中填写访问令牌
4. 点击 **「保存」**

<!-- 📸 配图：OpenAkita Desktop 中 OneBot 配置表单的截图 -->
> **[配图位]** OpenAkita Desktop — OneBot 配置表单

#### 方式 B：CLI 交互式向导

```bash
openakita setup
```

在 Step 4（IM Channels）中选择 `[6] OneBot（通用协议）`，按提示输入：

```
OneBot Configuration

OneBot 通道需要先部署 NapCat 或 Lagrange 作为 OneBot 服务端

参考: https://github.com/botuniverse/onebot-11

Enter OneBot WebSocket URL [ws://127.0.0.1:8080]:
Enter Access Token (optional, press Enter to skip):
```

#### 方式 C：手动编辑 .env 文件

在 `.env` 文件中添加：

```bash
# --- OneBot（通用协议）---
ONEBOT_ENABLED=true
ONEBOT_WS_URL=ws://127.0.0.1:8080     # OneBot WebSocket 地址
ONEBOT_ACCESS_TOKEN=                    # 可选，用于连接鉴权
```

> 如果 OneBot 服务和 OpenAkita 不在同一台机器上，请将 `127.0.0.1` 替换为 OneBot 服务器的实际 IP。

### 6.4 验证与测试

1. **先启动 OneBot 服务器**（如 NapCat），确认 WebSocket 监听正常
2. 启动 OpenAkita，日志应显示：
   ```
   OneBot adapter connected to ws://127.0.0.1:8080
   ```
3. 使用另一个 QQ 号给机器人 QQ 号发消息（私聊或群聊 @机器人）
4. 观察日志和回复

<!-- 📸 配图：QQ 中与机器人对话的效果截图 -->
> **[配图位]** OneBot 机器人正常回复消息的效果展示

### 6.5 常见问题

| 问题 | 解决方案 |
|------|---------|
| 连接失败 | 确认 OneBot 服务器已启动且 WebSocket 地址正确 |
| 鉴权失败 | 检查 `ONEBOT_ACCESS_TOKEN` 是否与 OneBot 服务端配置的 Token 一致 |
| 频繁断线 | 适配器支持自动重连（指数退避策略，初始 1 秒，最大 60 秒） |
| 群/私聊消息混乱 | 适配器会自动判断，无需特别处理 |
| 文件发送失败 | 文件发送使用专用 API（`upload_group_file` / `upload_private_file`），确认 OneBot 实现支持 |

---

## 七、语音识别（Whisper）配置

> 所有 IM 通道的语音消息都会经过统一的 Whisper 语音转文字处理。

### 6.1 工作流程

```
用户发送语音 → IM 适配器接收 → Gateway 下载语音 → Whisper 转写 → 文字传给 Agent
```

### 6.2 安装 ffmpeg

Whisper 需要 ffmpeg 来解码音频文件。

```bash
# Windows
winget install FFmpeg

# macOS
brew install ffmpeg

# Linux (Ubuntu/Debian)
sudo apt install ffmpeg

# 或通过 Python 自动安装静态版
pip install static-ffmpeg
```

### 6.3 配置

```bash
# .env
WHISPER_MODEL=base
# 语言设置: zh(中文) | en(英文) | auto(自动检测)
WHISPER_LANGUAGE=zh
```

### 6.4 模型选择参考

| 模型 | 大小 | 速度 | 精度 | 推荐场景 |
|------|------|------|------|---------|
| tiny | ~39MB | 最快 | 一般 | 资源极度有限的设备 |
| base | ~74MB | 快 | 较好 | **推荐**，性价比最高 |
| small | ~244MB | 中等 | 好 | 对精度有一定要求 |
| medium | ~769MB | 慢 | 很好 | 专业场景 |
| large | ~1.5GB | 最慢 | 最好 | 最高精度要求 |

---

## 八、常见问题汇总

### Q1：如何同时启用多个 IM 通道？

在 `.env` 中将多个通道的 `*_ENABLED` 设为 `true` 即可。OpenAkita 会同时连接所有启用的通道。

```bash
TELEGRAM_ENABLED=true
FEISHU_ENABLED=true
DINGTALK_ENABLED=true
```

### Q2：不同通道的消息会互相影响吗？

不会。每个通道的会话（Session）是独立的，互不干扰。

### Q3：一个用户在多个平台都能用吗？

可以，但每个平台的会话是独立的。例如，同一个人在 Telegram 和飞书上的对话历史是分开的。

### Q4：消息处理的架构是怎样的？

```
平台消息 → Adapter (解析) → UnifiedMessage → Gateway (预处理) → Agent
                                                    ↓
Agent 回复 ← Adapter (发送) ← OutgoingMessage ← Gateway (路由)
```

- **ChannelAdapter**：各平台适配器，负责消息格式转换
- **MessageGateway**：统一消息路由、会话管理、媒体预处理
- **Agent**：AI 处理核心

### Q5：如何查看 IM 通道状态？

- **日志**：启动时会打印各通道的连接状态
- **OpenAkita Desktop**：在 OpenAkita 的 Web 管理界面中查看通道状态和会话列表
- **API**：`GET /api/im/channels` 接口返回所有通道的状态

### Q6：代理设置对哪些通道有效？

| 通道 | 代理说明 |
|------|---------|
| Telegram | 需要配置 `TELEGRAM_PROXY`（大陆环境必须） |
| 飞书 | 国内访问无需代理 |
| 钉钉 | 国内访问无需代理 |
| 企业微信 | 国内访问无需代理 |
| QQ 官方机器人 | 国内访问无需代理 |
| OneBot | 本地 OneBot 连接，无需代理 |

### Q7：有桌面安装中心吗？

有。OpenAkita 提供了 **OpenAkita Desktop** 桌面应用（基于 Tauri），可以通过图形界面完成所有 IM 通道的配置，无需手动编辑 `.env` 文件。详见本教程的 [三种配置方式](#三种配置方式) 章节。

OpenAkita Desktop 提供以下 IM 相关能力：
- **配置步骤页**：分通道的表单，开关 + 输入框，保存自动写入 `.env`
- **状态页**：实时显示每个通道的连接状态（🟢 在线 / 🔴 离线 / ⚪ 未启用），支持一键刷新
- **服务管理**：配置修改后可一键重启服务，无需回到命令行
- **IM 会话查看器**：查看各通道的活跃会话和消息历史

<!-- 📸 配图：OpenAkita Desktop 完整的 IM 通道管理界面截图（包含配置 + 状态 + 会话） -->
> **[配图位]** OpenAkita Desktop 的 IM 通道完整管理界面

### Q8：OpenAkita Desktop 和 .env 手动编辑会冲突吗？

不会。OpenAkita Desktop 的配置最终也是写入 `.env` 文件，两种方式操作的是同一份文件。你可以先用 OpenAkita Desktop 完成基本配置，再手动编辑 `.env` 微调高级参数（如 `TELEGRAM_WEBHOOK_URL`、`WEWORK_CALLBACK_PORT` 等）。修改 `.env` 后重启服务即可生效。

---

## 附录：完整 .env 配置模板

```bash
# ========== IM 通道 ==========

# --- Telegram ---
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_PROXY=
# TELEGRAM_WEBHOOK_URL=
# TELEGRAM_REQUIRE_PAIRING=true
# TELEGRAM_PAIRING_CODE=

# --- 飞书（需要 openakita[feishu]）---
FEISHU_ENABLED=false
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# --- 钉钉（需要 openakita[dingtalk]）---
DINGTALK_ENABLED=false
DINGTALK_CLIENT_ID=
DINGTALK_CLIENT_SECRET=

# --- 企业微信（需要 openakita[wework]）---
WEWORK_ENABLED=false
WEWORK_CORP_ID=
WEWORK_TOKEN=
WEWORK_ENCODING_AES_KEY=
# WEWORK_CALLBACK_PORT=9880

# --- QQ 官方机器人（需要 openakita[qqbot]）---
QQBOT_ENABLED=false
QQBOT_APP_ID=
QQBOT_APP_SECRET=
QQBOT_SANDBOX=false

# --- OneBot（需要 openakita[onebot] + NapCat/Lagrange）---
ONEBOT_ENABLED=false
ONEBOT_WS_URL=ws://127.0.0.1:8080
ONEBOT_ACCESS_TOKEN=

# --- 语音识别 ---
WHISPER_MODEL=base
WHISPER_LANGUAGE=zh
```

---

> **文档版本**：v1.0  
> **最后更新**：2026-02-13  
> **适用版本**：OpenAkita v0.x+
