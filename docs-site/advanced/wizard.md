# 配置向导详解

## 概述

配置向导将 OpenAkita 的初始化分为 **5 个步骤**，从 LLM 接入到高级配置逐步完成。首次启动时自动弹出，之后可随时通过侧边栏进入对应配置页。

| 步骤 | 页面 | 说明 |
|------|------|------|
| 1 | [LLM 端点](/web/#/config/llm) | 配置 AI 模型接口 |
| 2 | [IM 通道](/web/#/config/im) | 接入聊天平台 |
| 3 | [工具与技能](/web/#/config/tools) | 管理技能和工具 |
| 4 | [灵魂与意志](/web/#/config/agent) | 设置 Agent 个性和行为 |
| 5 | [高级配置](/web/#/config/advanced) | 网络、安全、数据管理 |

## 步骤 1：LLM 端点

[打开 LLM 端点配置](/web/#/config/llm)

配置 Agent 使用的大语言模型。支持 30+ 供应商的 OpenAI 兼容接口。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **端点名称** | string | — | 自定义名称（如"Claude 主力"） |
| **供应商** | select | openai | 选择 API 供应商（OpenAI / Anthropic / Azure 等） |
| **API Key** | password | — | 供应商颁发的密钥 |
| **Base URL** | url | 按供应商预填 | API 基础地址，自建代理时修改 |
| **模型** | string | — | 模型标识（如 `claude-sonnet-4-20250514`） |
| **优先级** | number | 0 | 数值越大优先级越高，多端点时按此排序 |
| **最大 Token** | number | 4096 | 单次回复最大 Token 数 |
| **上下文窗口** | number | 按模型预填 | 模型上下文长度 |
| **超时** | number | 120 | 请求超时秒数 |
| **RPM 限制** | number | 60 | 每分钟最大请求数 |
| **编码规划** | boolean | false | 是否启用 coding plan 模式 |
| **编译器模型** | string | — | 用于 Identity 编译的模型 |
| **STT 模型** | string | whisper-1 | 语音转文字模型 |

::: tip 多端点策略
可配置多个端点，系统按优先级自动选择。主端点不可用时自动切换到备用端点。
:::

## 步骤 2：IM 通道

[打开 IM 通道配置](/web/#/config/im)

为每个平台配置连接凭证。不使用的平台可跳过。

### 通用字段

| 字段 | 类型 | 说明 |
|------|------|------|
| **启用** | boolean | 是否启用该通道 |
| **代理** | url | 网络代理地址（如 Telegram 在大陆需要） |
| **配对码** | string | 安全绑定用的一次性验证码 |

### 各平台特有字段

| 平台 | 特有字段 |
|------|---------|
| **Telegram** | Bot Token |
| **飞书** | App ID, App Secret, Encrypt Key, Verification Token |
| **钉钉** | Client ID, Client Secret |
| **企业微信** | Corp ID, Agent ID, Secret, Token, AES Key, 通信模式 |
| **QQ 官方** | App ID, Token |
| **OneBot** | WebSocket URL |

详见 [消息通道（IM）](/features/im-channels) 获取各平台的完整配置教程。

## 步骤 3：工具与技能

[打开工具与技能配置](/web/#/config/tools)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **技能启用/禁用** | toggle 列表 | 全部启用 | 逐个控制每项技能的可用状态 |
| **Whisper 模型** | string | whisper-1 | 语音识别使用的模型 |
| **网络代理** | url | — | 工具访问外网时使用的代理 |
| **强制 IPv4** | boolean | false | 部分网络下需开启以避免 IPv6 连接问题 |
| **并行执行数** | number | 3 | 同时执行的工具调用数上限 |
| **MCP 服务器** | 列表 | — | 配置外部 MCP 服务器连接 |
| **桌面自动化** | boolean | false | 允许 Agent 控制鼠标键盘 |
| **视觉模型** | string | — | 截图分析使用的视觉模型 |
| **幻觉保护** | boolean | true | 对工具返回结果进行事实核查 |
| **追问次数** | number | 2 | 工具结果不明确时允许的最大追问轮数 |

## 步骤 4：灵魂与意志

[打开灵魂与意志配置](/web/#/config/agent)

### 个性与身份

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **个性描述** | textarea | — | Agent 的性格描述，影响回复风格 |
| **自我意识** | textarea | — | Agent 对自身角色的认知 |
| **Agent 名称** | string | OpenAkita | 对外显示的名称 |
| **最大迭代次数** | number | 25 | 单次任务最大推理-行动循环数 |
| **思考模式** | select | auto | 是否启用扩展思考（off / auto / always） |
| **快速模型** | string | — | 用于轻量判断的小模型 |

### 交互行为

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **自动确认** | boolean | true | 执行操作前是否自动确认 |
| **主动性** | select | medium | 主动沟通程度（low / medium / high） |
| **活跃度** | select | normal | 消息频率（quiet / normal / chatty） |
| **表情 / 贴纸** | boolean | true | 回复中是否使用 emoji 和贴纸 |
| **每日消息上限** | number | 0 | 0=无限制 |
| **免打扰时段** | string | — | 格式：`23:00-07:00` |
| **桌面通知** | boolean | true | 是否推送桌面通知 |

### 系统配置

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **计划任务** | boolean | false | 启用 Cron 定时任务调度器 |
| **时区** | string | 系统时区 | 用于计划任务的时区 |
| **最大并发** | number | 3 | 同时处理的任务数上限 |
| **记忆管理** | select | auto | 记忆写入策略 |
| **上下文管理** | select | smart | 长对话时的上下文压缩策略 |
| **会话管理** | boolean | true | 启用多会话支持 |
| **日志级别** | select | info | debug / info / warning / error |

## 步骤 5：高级配置

[打开高级配置](/web/#/config/advanced)

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| **API_HOST** | string | 127.0.0.1 | API 监听地址 |
| **API_PORT** | number | 18900 | API 监听端口 |
| **TRUST_PROXY** | boolean | false | 信任反向代理 Headers |
| **CORS_ORIGINS** | string | — | 允许跨域访问的源 |
| **Web 访问密码** | password | — | Web 界面的访问密码 |
| **环境变量** | key-value | — | 自定义环境变量 |
| **数据备份** | button | — | 导出工作区数据 |
| **数据还原** | button | — | 从备份恢复数据 |
| **工作区迁移** | button | — | 切换工作区目录 |
| **存储清理** | button | — | 清理缓存和临时文件 |
| **诊断导出** | button | — | 导出系统诊断信息 |
| **系统重置** | button | — | 恢复出厂设置（不可逆） |

详见 [高级设置](/advanced/advanced) 获取每项配置的深入说明。

## 相关页面

- [聊天对话](/features/chat) — 配置完成后开始对话
- [消息通道（IM）](/features/im-channels) — IM 平台的详细接入教程
- [高级设置](/advanced/advanced) — 网络、安全等高级配置详解
- [CLI 命令参考](/advanced/cli) — 命令行方式管理配置
