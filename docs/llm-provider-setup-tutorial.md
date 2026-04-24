# LLM 服务商配置教程

> 本教程详细介绍如何为 OpenAkita 配置 LLM（大语言模型）服务商，包含各平台的 API Key 申请流程、端点配置方法，以及多端点 Failover 策略。

---

## 目录

- [概览](#概览)
- [核心概念](#核心概念)
- [三种配置方式](#三种配置方式)
- [一、通义千问（DashScope）](#一通义千问dashscope)
- [二、DeepSeek](#二deepseek)
- [三、月之暗面（Kimi）](#三月之暗面kimi)
- [四、智谱 AI（GLM）](#四智谱-aiglm)
- [五、MiniMax](#五minimax)
- [六、OpenAI](#六openai)
- [七、Anthropic Claude](#七anthropic-claude)
- [八、Google Gemini](#八google-gemini)
- [九、其他服务商](#九其他服务商)
- [十、多端点与 Failover](#十多端点与-failover)
- [十一、编译器端点（Prompt Compiler）](#十一编译器端点prompt-compiler)
- [十二、常见问题](#十二常见问题)

---

## 概览

OpenAkita 支持多种 LLM 服务商，所有服务商通过统一的端点配置系统管理。你至少需要配置 **1 个 LLM 端点** 才能使用 OpenAkita。

### 支持的服务商一览

**国内服务商：**

| 服务商 | API 类型 | 默认 Base URL | 推荐模型 | 特点 |
|--------|----------|--------------|---------|------|
| 通义千问（DashScope） | openai | `https://dashscope.aliyuncs.com/compatible-mode/v1` | qwen3-max | 国内首选，模型全 |
| DeepSeek | openai | `https://api.deepseek.com/v1` | deepseek-v3 | 性价比高 |
| 月之暗面（Kimi） | openai | `https://api.moonshot.cn/v1` | kimi-k2.5 | 长文本强 |
| 智谱 AI | openai | `https://open.bigmodel.cn/api/paas/v4` | glm-5 | 国产旗舰 |
| MiniMax | openai | `https://api.minimaxi.com/v1` | MiniMax-M2.1 | 多模态 |
| 字节豆包（火山引擎） | openai | `https://ark.cn-beijing.volces.com/api/v3` | doubao-* | 字节生态 |
| SiliconFlow | openai | `https://api.siliconflow.cn/v1` | 多种开源模型 | 开源模型聚合 |

**国际服务商：**

| 服务商 | API 类型 | 默认 Base URL | 推荐模型 | 特点 |
|--------|----------|--------------|---------|------|
| OpenAI | openai | `https://api.openai.com/v1` | gpt-4o | 行业标杆 |
| Anthropic | anthropic | `https://api.anthropic.com` | claude-sonnet-4 | 编码最强 |
| Google Gemini | openai | `https://generativelanguage.googleapis.com/v1beta/openai` | gemini-2.5-pro | 超长上下文 |
| Groq | openai | `https://api.groq.com/openai/v1` | llama-* | 推理速度极快 |
| Mistral | openai | `https://api.mistral.ai/v1` | mistral-large | 欧洲开源 |
| OpenRouter | openai | `https://openrouter.ai/api/v1` | 聚合多家 | 一个 Key 用所有模型 |

### 模型能力标签

| 能力 | 说明 | 示例模型 |
|------|------|---------|
| `text` | 文本对话 | 所有模型 |
| `tools` | 工具调用（Function Calling） | gpt-4o、claude-sonnet-4、qwen3-max |
| `thinking` | 深度推理 / 扩展思考 | claude-opus-4.5-thinking、deepseek-r1、qwq-plus |
| `vision` | 图像理解 | gpt-4o、claude-sonnet-4、qwen-vl-max |
| `video` | 视频理解 | gemini-2.5-pro、qwen-vl-max |

---

## 核心概念

### 端点（Endpoint）

一个端点 = 一个服务商 + 一个模型 + 一套凭证。OpenAkita 通过 `data/llm_endpoints.json` 文件管理所有端点。

```json
{
  "name": "dashscope-qwen3-max",
  "provider": "dashscope",
  "api_type": "openai",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_env": "DASHSCOPE_API_KEY",
  "model": "qwen3-max",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "thinking"]
}
```

### 关键字段说明

| 字段 | 说明 |
|------|------|
| `name` | 端点唯一名称，用于标识和日志 |
| `provider` | 服务商标识（影响能力推断和特殊参数处理） |
| `api_type` | API 协议类型：`openai`（绝大多数）或 `anthropic` |
| `base_url` | API 接口地址 |
| `api_key_env` | API Key 在 `.env` 文件中的环境变量名 |
| `model` | 模型 ID |
| `priority` | 优先级，数值越小越优先 |
| `max_tokens` | 最大输出 token 数 |
| `timeout` | 请求超时（秒） |
| `capabilities` | 模型能力列表 |
| `extra_params` | 额外参数（如 DashScope 的 `enable_thinking`） |

---

## 三种配置方式

### 方式一：OpenAkita Desktop 桌面程序（推荐）

通过可视化界面添加和管理 LLM 端点，是最简单直观的方式。

1. 打开 OpenAkita Desktop
2. 进入 **「LLM 端点」** 配置步骤（快速配置或完整配置均可）
3. 点击 **「+ 添加端点」**
4. 在对话框中：
   - 选择**服务商**（下拉框会自动填充 Base URL）
   - 输入 **API Key**
   - 选择或输入**模型名称**（支持在线拉取模型列表）
   - 确认**能力标签**（自动推断，可手动调整）
5. 点击 **「保存」**

<!-- 📸 配图：OpenAkita Desktop — LLM 端点配置页面全貌 -->
> **[配图位]** OpenAkita Desktop — LLM 端点配置页面

<!-- 📸 配图：OpenAkita Desktop — 添加端点对话框（选择服务商、填 API Key、选模型） -->
> **[配图位]** OpenAkita Desktop — 添加端点对话框

<!-- 📸 配图：OpenAkita Desktop — 端点列表，显示已添加的多个端点 -->
> **[配图位]** OpenAkita Desktop — 已添加的端点列表

<!-- 📸 配图：OpenAkita Desktop — 服务商下拉列表（展示国内+国际全部服务商） -->
> **[配图位]** OpenAkita Desktop — 服务商下拉选择

<!-- 📸 配图：OpenAkita Desktop — 模型在线拉取列表 -->
> **[配图位]** OpenAkita Desktop — 在线拉取可用模型列表

<!-- 📸 配图：OpenAkita Desktop — 能力标签勾选（text/tools/thinking/vision） -->
> **[配图位]** OpenAkita Desktop — 能力标签勾选

> **提示**：添加端点后可在状态页面进行**健康检查**，验证 API Key 和端点是否正常。

<!-- 📸 配图：OpenAkita Desktop — 状态页面的端点健康检查结果 -->
> **[配图位]** OpenAkita Desktop — 端点健康检查

### 方式二：CLI 交互式向导

```bash
openakita setup
```

在 Step 3（Configure LLM API）中按提示操作：

```
Which LLM API would you like to use?

  [1] Anthropic Claude (recommended)
  [2] OpenAI-compatible API
  [3] Other provider

Select option [1]:
```

选择后输入 API Key 和 Base URL，向导会自动写入配置。

> **注意**：CLI 向导适合快速配置单个端点。如需配置多端点和 Failover，建议使用 OpenAkita Desktop 或手动编辑配置文件。

### 方式三：手动编辑配置文件

LLM 端点通过两个文件配置：

1. **`data/llm_endpoints.json`** — 端点列表（服务商、模型、优先级等）
2. **`.env`** — API Key 实际值（被 `api_key_env` 引用）

```bash
# 从模板复制端点配置
cp data/llm_endpoints.json.example data/llm_endpoints.json

# 编辑端点配置
code data/llm_endpoints.json

# 在 .env 中填入 API Key
code .env
```

---

## 一、通义千问（DashScope）

> 阿里云旗下，国内服务稳定，模型种类丰富，**推荐作为国内用户首选**。

### 1.1 申请 API Key

1. 打开浏览器，访问 [DashScope 控制台](https://dashscope.console.aliyun.com/)
2. 如果没有阿里云账号，需要先注册（支持支付宝快捷注册）

<!-- 📸 配图：DashScope 控制台登录/注册页面 -->
> **[配图位]** DashScope 控制台登录页面

3. 登录后，进入 [API Key 管理页面](https://dashscope.console.aliyun.com/apiKey)
4. 点击 **「创建新的 API Key」**
5. 复制生成的 API Key（格式如 `sk-xxxxxxxxxxxxxxxxxxxxxxxx`）

<!-- 📸 配图：DashScope API Key 管理页面，标注「创建」按钮和 Key 复制位置 -->
> **[配图位]** DashScope — 创建并复制 API Key

⚠️ **注意**：API Key 只在创建时显示一次，请立即保存。如果丢失需要重新创建。

6. （可选）在 [模型广场](https://dashscope.console.aliyun.com/model) 查看可用模型和价格

<!-- 📸 配图：DashScope 模型广场页面 -->
> **[配图位]** DashScope — 模型广场

### 1.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `qwen3-max` | text, tools, thinking | 旗舰模型，推荐 |
| `qwen3-plus` | text, tools, thinking | 平衡性价比 |
| `qwen3-turbo` | text, tools | 快速模型，适合编译器端点 |
| `qwq-plus` | text, thinking | 深度推理专用 |
| `qwen-vl-max` | text, vision, video | 多模态（图片+视频理解） |
| `qwen-vl-plus` | text, vision | 多模态（图片理解） |

### 1.3 配置方式

#### OpenAkita Desktop

在端点配置对话框中：
- **服务商**：选择 `通义千问 (DashScope)`
- **API Key**：粘贴你的 DashScope API Key
- **模型**：从列表选择（如 `qwen3-max`）或手动输入

<!-- 📸 配图：OpenAkita Desktop — 选择通义千问服务商后的配置界面 -->
> **[配图位]** OpenAkita Desktop — 通义千问端点配置

#### 手动配置

`.env` 文件：

```bash
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

`data/llm_endpoints.json` 中添加端点：

```json
{
  "name": "dashscope-qwen3-max",
  "provider": "dashscope",
  "api_type": "openai",
  "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "api_key_env": "DASHSCOPE_API_KEY",
  "model": "qwen3-max",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "thinking"]
}
```

> **思考模式**：如果使用 qwen3 系列的思考能力，需在 `extra_params` 中添加 `"enable_thinking": true`：
>
> ```json
> "extra_params": { "enable_thinking": true }
> ```

---

## 二、DeepSeek

> 国产模型性价比之王，深度推理能力强，API 价格极低。

### 2.1 申请 API Key

1. 访问 [DeepSeek 开放平台](https://platform.deepseek.com/)
2. 注册账号并登录

<!-- 📸 配图：DeepSeek 开放平台首页 -->
> **[配图位]** DeepSeek 开放平台首页

3. 进入 [API Keys 页面](https://platform.deepseek.com/api_keys)
4. 点击 **「创建 API Key」**
5. 复制 API Key

<!-- 📸 配图：DeepSeek API Keys 管理页面 -->
> **[配图位]** DeepSeek — 创建并复制 API Key

6. （可选）在 [充值页面](https://platform.deepseek.com/top_up) 充值余额（新用户通常有免费额度）

### 2.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `deepseek-chat` | text, tools | 通用对话（v3） |
| `deepseek-reasoner` | text, thinking | 深度推理（R1） |

### 2.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `DeepSeek`
- **API Key**：粘贴 DeepSeek API Key
- **模型**：选择 `deepseek-chat` 或 `deepseek-reasoner`

<!-- 📸 配图：OpenAkita Desktop — DeepSeek 端点配置 -->
> **[配图位]** OpenAkita Desktop — DeepSeek 端点配置

#### 手动配置

```bash
# .env
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "deepseek-chat",
  "provider": "deepseek",
  "api_type": "openai",
  "base_url": "https://api.deepseek.com/v1",
  "api_key_env": "DEEPSEEK_API_KEY",
  "model": "deepseek-chat",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools"]
}
```

---

## 三、月之暗面（Kimi）

> Moonshot AI 出品，长文本处理能力突出。

### 3.1 申请 API Key

1. 访问 [Moonshot AI 开放平台](https://platform.moonshot.cn/console)（中国区）
   - 国际区：[https://platform.moonshot.ai/console/api-keys](https://platform.moonshot.ai/console/api-keys)
2. 注册并登录

<!-- 📸 配图：Moonshot AI 开放平台首页 -->
> **[配图位]** Moonshot AI 开放平台

3. 进入 **「API Key 管理」** 页面
4. 点击 **「新建」**，复制生成的 API Key

<!-- 📸 配图：Moonshot AI API Key 管理页面 -->
> **[配图位]** Moonshot AI — 创建并复制 API Key

### 3.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `kimi-k2.5` | text, tools, thinking | 最新旗舰 |
| `kimi-k2` | text, tools | 上一代旗舰 |
| `moonshot-v1-128k` | text | 128K 超长上下文 |

### 3.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `Kimi (月之暗面)` 或 `Kimi (国际)`
- **API Key**：粘贴 Kimi API Key
- **模型**：选择 `kimi-k2.5`

<!-- 📸 配图：OpenAkita Desktop — Kimi 端点配置 -->
> **[配图位]** OpenAkita Desktop — Kimi 端点配置

#### 手动配置

```bash
# .env
KIMI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "kimi-k2.5",
  "provider": "kimi-cn",
  "api_type": "openai",
  "base_url": "https://api.moonshot.cn/v1",
  "api_key_env": "KIMI_API_KEY",
  "model": "kimi-k2.5",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "thinking"]
}
```

---

## 四、智谱 AI（GLM）

> 清华系大模型，国产旗舰，提供国内区和国际区（Z.AI）两个独立平台。

### 4.1 申请 API Key

**国内区：**

1. 访问 [智谱 AI 开放平台](https://open.bigmodel.cn/)
2. 注册并登录
3. 进入 [API Keys 页面](https://open.bigmodel.cn/usercenter/apikeys)
4. 创建并复制 API Key

<!-- 📸 配图：智谱 AI 开放平台 API Key 管理页面 -->
> **[配图位]** 智谱 AI（国内区）— API Key 管理

**国际区（Z.AI）：**

1. 访问 [Z.AI 平台](https://z.ai/)
2. 注册并登录
3. 进入 [API Key 管理页面](https://z.ai/manage-apikey/apikey-list)
4. 创建并复制 API Key

<!-- 📸 配图：Z.AI 平台 API Key 管理页面 -->
> **[配图位]** 智谱 AI（国际区 Z.AI）— API Key 管理

### 4.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `glm-5` | text, tools, thinking | 最新旗舰 |
| `glm-4-plus` | text, tools | 上一代旗舰 |
| `glm-4v-plus` | text, vision | 多模态 |

### 4.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `智谱 AI (国内)` 或 `智谱 AI (国际/Z.AI)`
- **API Key**：粘贴对应平台的 API Key
- **模型**：选择 `glm-5`

<!-- 📸 配图：OpenAkita Desktop — 智谱 AI 端点配置 -->
> **[配图位]** OpenAkita Desktop — 智谱 AI 端点配置

#### 手动配置

```bash
# .env
ZHIPU_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "zhipu-glm5",
  "provider": "zhipu-cn",
  "api_type": "openai",
  "base_url": "https://open.bigmodel.cn/api/paas/v4",
  "api_key_env": "ZHIPU_API_KEY",
  "model": "glm-5",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "thinking"]
}
```

> 国际区将 `provider` 改为 `zhipu-int`，`base_url` 改为 `https://api.z.ai/api/paas/v4`。

---

## 五、MiniMax

> 多模态能力强，提供中国区和国际区两个平台。

### 5.1 申请 API Key

**中国区：**

1. 访问 [MiniMax 开放平台](https://platform.minimaxi.com/)
2. 注册并登录
3. 进入 [API Key 管理页面](https://platform.minimaxi.com/user-center/basic-information/interface-key)
4. 创建并复制 API Key

<!-- 📸 配图：MiniMax 开放平台 API Key 页面 -->
> **[配图位]** MiniMax（中国区）— API Key 管理

**国际区：**

1. 访问 [MiniMax 国际平台](https://platform.minimax.io/)
2. 进入 [API Key 管理页面](https://platform.minimax.io/user-center/basic-information/interface-key)

### 5.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `MiniMax-M2.1` | text, tools | 最新旗舰 |
| `abab6.5s-chat` | text, tools | 上一代 |

### 5.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `MiniMax (国内)` 或 `MiniMax (国际)`
- **API Key**：粘贴 MiniMax API Key
- **模型**：选择 `MiniMax-M2.1`

<!-- 📸 配图：OpenAkita Desktop — MiniMax 端点配置 -->
> **[配图位]** OpenAkita Desktop — MiniMax 端点配置

#### 手动配置

```bash
# .env
MINIMAX_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "minimax-m2.1",
  "provider": "minimax-cn",
  "api_type": "openai",
  "base_url": "https://api.minimaxi.com/v1",
  "api_key_env": "MINIMAX_API_KEY",
  "model": "MiniMax-M2.1",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools"]
}
```

---

## 六、OpenAI

> 行业标杆，GPT 系列模型，需要海外网络环境或代理访问。

### 6.1 申请 API Key

1. 访问 [OpenAI Platform](https://platform.openai.com/)
2. 注册账号（需要海外手机号或 Google 账号）

<!-- 📸 配图：OpenAI Platform 首页 -->
> **[配图位]** OpenAI Platform 首页

3. 登录后进入 [API Keys 页面](https://platform.openai.com/api-keys)
4. 点击 **「Create new secret key」**
5. 为 Key 命名（如 `openakita`），选择权限，点击创建
6. 复制 API Key（格式如 `sk-proj-xxxxxxxx`）

<!-- 📸 配图：OpenAI API Keys 页面，创建新 Key 的对话框 -->
> **[配图位]** OpenAI — 创建 API Key

7. 在 [Billing 页面](https://platform.openai.com/settings/organization/billing/overview) 确认已充值余额

<!-- 📸 配图：OpenAI Billing 页面 -->
> **[配图位]** OpenAI — Billing 充值页面

⚠️ **国内用户注意**：OpenAI API 需要海外网络环境。如果使用代理，在 `.env` 中配置：
```bash
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
```

### 6.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `gpt-4o` | text, tools, vision | 旗舰多模态 |
| `gpt-4o-mini` | text, tools, vision | 高性价比，适合编译器端点 |
| `o1` | text, thinking | 深度推理 |
| `o3-mini` | text, thinking, tools | 推理 + 工具 |
| `gpt-5` | text, tools, vision, thinking | 最新旗舰 |

### 6.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `OpenAI`
- **API Key**：粘贴 OpenAI API Key
- **模型**：选择 `gpt-4o`

<!-- 📸 配图：OpenAkita Desktop — OpenAI 端点配置 -->
> **[配图位]** OpenAkita Desktop — OpenAI 端点配置

#### 手动配置

```bash
# .env
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "openai-gpt4o",
  "provider": "openai",
  "api_type": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_key_env": "OPENAI_API_KEY",
  "model": "gpt-4o",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "vision"]
}
```

---

## 七、Anthropic Claude

> 编码和推理能力一流，Claude 系列模型。需海外网络环境。

### 7.1 申请 API Key

1. 访问 [Anthropic Console](https://console.anthropic.com/)
2. 注册账号并登录

<!-- 📸 配图：Anthropic Console 首页 -->
> **[配图位]** Anthropic Console 首页

3. 进入 [API Keys 页面](https://console.anthropic.com/settings/keys)
4. 点击 **「Create Key」**
5. 复制 API Key（格式如 `sk-ant-api03-xxxxxxxx`）

<!-- 📸 配图：Anthropic Console API Keys 页面 -->
> **[配图位]** Anthropic — 创建 API Key

6. 在 [Plans & Billing](https://console.anthropic.com/settings/plans) 页面确认已激活付费计划

### 7.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `claude-sonnet-4-20250514` | text, tools, vision | 平衡性价比，推荐 |
| `claude-opus-4-5-20250514` | text, tools, vision | 最强能力 |
| `claude-opus-4-5-20251101-thinking` | text, tools, vision, thinking | 扩展思考版 |
| `claude-haiku-3-5-20241022` | text, tools | 快速低成本 |

### 7.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `Anthropic`
- **API Key**：粘贴 Anthropic API Key
- **模型**：选择 `claude-sonnet-4-20250514`

<!-- 📸 配图：OpenAkita Desktop — Anthropic 端点配置 -->
> **[配图位]** OpenAkita Desktop — Anthropic Claude 端点配置

> **注意**：Anthropic 使用独有的 API 协议，`api_type` 必须选择 `anthropic`（而非 `openai`）。OpenAkita Desktop 会自动处理。

#### 手动配置

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "anthropic-claude-sonnet",
  "provider": "anthropic",
  "api_type": "anthropic",
  "base_url": "https://api.anthropic.com",
  "api_key_env": "ANTHROPIC_API_KEY",
  "model": "claude-sonnet-4-20250514",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "vision"]
}
```

---

## 八、Google Gemini

> 超长上下文窗口（100 万 token），多模态能力强。需海外网络环境。

### 8.1 申请 API Key

1. 访问 [Google AI Studio](https://aistudio.google.com/)
2. 使用 Google 账号登录

<!-- 📸 配图：Google AI Studio 首页 -->
> **[配图位]** Google AI Studio 首页

3. 点击左侧 **「Get API Key」**
4. 点击 **「Create API key」**，选择一个 Google Cloud 项目（或创建新项目）
5. 复制生成的 API Key

<!-- 📸 配图：Google AI Studio API Key 页面 -->
> **[配图位]** Google AI Studio — 创建 API Key

### 8.2 可用模型

| 模型 | 能力 | 说明 |
|------|------|------|
| `gemini-2.5-pro` | text, tools, vision, video, thinking | 最新旗舰 |
| `gemini-2.5-flash` | text, tools, vision, thinking | 高速版 |
| `gemini-2.0-flash` | text, tools, vision | 上一代高速 |

### 8.3 配置方式

#### OpenAkita Desktop

- **服务商**：选择 `Google Gemini`
- **API Key**：粘贴 Google AI API Key
- **模型**：选择 `gemini-2.5-pro`

<!-- 📸 配图：OpenAkita Desktop — Google Gemini 端点配置 -->
> **[配图位]** OpenAkita Desktop — Google Gemini 端点配置

#### 手动配置

```bash
# .env
GOOGLE_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxxx
```

```json
{
  "name": "google-gemini-2.5-pro",
  "provider": "google",
  "api_type": "openai",
  "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
  "api_key_env": "GOOGLE_API_KEY",
  "model": "gemini-2.5-pro",
  "priority": 1,
  "max_tokens": 8192,
  "timeout": 180,
  "capabilities": ["text", "tools", "vision", "video", "thinking"]
}
```

---

## 九、其他服务商

### SiliconFlow（开源模型聚合）

- **申请**：[https://siliconflow.cn/](https://siliconflow.cn/)
- **Base URL**：`https://api.siliconflow.cn/v1`
- **特点**：一个 Key 可调用多种开源模型（Llama、Mistral、Qwen 等）

### OpenRouter（全球模型聚合）

- **申请**：[https://openrouter.ai/](https://openrouter.ai/)
- **Base URL**：`https://openrouter.ai/api/v1`
- **特点**：一个 Key 可调用几乎所有主流模型，按用量计费

### 字节豆包（火山引擎）

- **申请**：[https://console.volcengine.com/ark](https://console.volcengine.com/ark)
- **Base URL**：`https://ark.cn-beijing.volces.com/api/v3`
- **特点**：字节生态，Doubao 系列模型

### Groq（极速推理）

- **申请**：[https://console.groq.com/](https://console.groq.com/)
- **Base URL**：`https://api.groq.com/openai/v1`
- **特点**：推理速度极快，开源模型

### Mistral

- **申请**：[https://console.mistral.ai/](https://console.mistral.ai/)
- **Base URL**：`https://api.mistral.ai/v1`
- **特点**：欧洲 AI 公司，Mistral Large 系列

> 以上服务商在 OpenAkita Desktop 的服务商下拉列表中均可直接选择。

---

## 十、多端点与 Failover

OpenAkita 的核心优势之一是支持**多端点配置与自动故障转移**。强烈建议至少配置 2 个端点。

### 10.1 工作原理

```
用户请求 → 选择最高优先级的可用端点 → 调用成功 → 返回结果
                                        ↓ 调用失败
                                  自动切换到下一个端点 → 重试
                                        ↓ 全部失败
                                  返回错误，进入冷却期
```

### 10.2 调度策略

| 机制 | 说明 |
|------|------|
| **优先级调度** | 优先使用 `priority` 值最小的端点 |
| **自动降级** | 主端点不可用时自动切换到备用端点 |
| **健康检查** | 后台定期（默认 60 秒）检测端点可用性 |
| **冷却机制** | 连续失败的端点会被临时冷却，避免反复重试 |
| **能力匹配** | 需要视觉能力时只选择标记了 `vision` 的端点 |

### 10.3 配置示例：双端点 Failover

```json
{
  "endpoints": [
    {
      "name": "primary-dashscope",
      "provider": "dashscope",
      "api_type": "openai",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key_env": "DASHSCOPE_API_KEY",
      "model": "qwen3-max",
      "priority": 1,
      "max_tokens": 8192,
      "timeout": 180,
      "capabilities": ["text", "tools", "thinking"]
    },
    {
      "name": "backup-deepseek",
      "provider": "deepseek",
      "api_type": "openai",
      "base_url": "https://api.deepseek.com/v1",
      "api_key_env": "DEEPSEEK_API_KEY",
      "model": "deepseek-chat",
      "priority": 2,
      "max_tokens": 8192,
      "timeout": 180,
      "capabilities": ["text", "tools"]
    }
  ],
  "settings": {
    "retry_count": 2,
    "retry_delay_seconds": 2,
    "health_check_interval": 60,
    "fallback_on_error": true
  }
}
```

### 10.4 Settings 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `retry_count` | `2` | 同一端点重试次数 |
| `retry_delay_seconds` | `2` | 重试间隔（秒） |
| `health_check_interval` | `60` | 健康检查间隔（秒） |
| `fallback_on_error` | `true` | 错误时是否自动降级到备用端点 |
| `allow_failover_with_tool_context` | `false` | 工具上下文中是否允许跨端点降级 |

### 10.5 在 OpenAkita Desktop 中管理多端点

<!-- 📸 配图：OpenAkita Desktop — 多端点列表，显示优先级和状态 -->
> **[配图位]** OpenAkita Desktop — 多端点管理，显示优先级排序和健康状态

<!-- 📸 配图：OpenAkita Desktop — 编辑端点优先级 -->
> **[配图位]** OpenAkita Desktop — 编辑端点优先级

> **提示**：在状态页面点击「健康检查」可批量验证所有端点的连通性。

<!-- 📸 配图：OpenAkita Desktop — 批量健康检查结果（全部绿色 / 部分红色） -->
> **[配图位]** OpenAkita Desktop — 批量端点健康检查结果

---

## 十一、编译器端点（Prompt Compiler）

OpenAkita 内置了 **Prompt Compiler**（提示词编译器），用于对用户指令做预处理（意图识别、指令优化等）。编译器使用快速小模型即可，**不需要思考能力**，可大幅降低整体响应延迟。

### 11.1 推荐模型

| 模型 | 服务商 | 说明 |
|------|--------|------|
| `qwen-turbo-latest` | DashScope | **推荐**，国内最快 |
| `gpt-4o-mini` | OpenAI | 快速可靠 |
| `deepseek-chat` | DeepSeek | 性价比高 |

### 11.2 配置方式

#### OpenAkita Desktop

在 LLM 端点配置页面，找到 **「编译器端点」** 区域，按与主端点相同的方式添加。

<!-- 📸 配图：OpenAkita Desktop — 编译器端点配置区域 -->
> **[配图位]** OpenAkita Desktop — 编译器端点配置

#### 手动配置

在 `data/llm_endpoints.json` 中添加 `compiler_endpoints`：

```json
{
  "compiler_endpoints": [
    {
      "name": "compiler-dashscope",
      "provider": "dashscope",
      "api_type": "openai",
      "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
      "api_key_env": "DASHSCOPE_API_KEY",
      "model": "qwen-turbo-latest",
      "priority": 1,
      "max_tokens": 2048,
      "timeout": 30,
      "capabilities": ["text"]
    }
  ]
}
```

> 如果不配置编译器端点，系统会回退到主端点（但会较慢）。

---

## 十二、常见问题

### Q1：最少需要配置几个端点？

至少 **1 个**。建议配置 **2 个**（不同服务商），以获得故障自动转移能力。

### Q2：国内用户推荐哪个服务商？

推荐组合：**通义千问（主）+ DeepSeek（备）**。两者都无需翻墙，价格合理，能力互补。

### Q3：API Key 存在哪里？安全吗？

API Key 存储在工作区的 `.env` 文件中（纯本地，不上传）。`llm_endpoints.json` 只存环境变量名（如 `DASHSCOPE_API_KEY`），不存明文 Key。

### Q4：如何切换正在使用的模型？

- **OpenAkita Desktop**：在端点列表中调整优先级，或禁用/启用端点
- **对话中**：使用 `/model` 命令切换
- **手动**：编辑 `llm_endpoints.json` 中的 `priority` 值

### Q5：`api_type` 选 `openai` 还是 `anthropic`？

除了 Anthropic 官方 API 使用 `anthropic` 类型外，几乎所有其他服务商（包括国内服务商）都使用 `openai` 兼容类型。OpenAkita Desktop 会自动选择。

### Q6：什么是 `extra_params`？

传递给 API 的额外参数。最常见的用途是 DashScope 的思考模式：

```json
"extra_params": { "enable_thinking": true }
```

### Q7：端点健康检查怎么用？

- **OpenAkita Desktop**：在状态页面点击「健康检查」按钮
- **API**：`POST /api/health/check` 可指定端点名称

<!-- 📸 配图：OpenAkita Desktop — 单个端点健康检查详情 -->
> **[配图位]** OpenAkita Desktop — 端点健康检查详情

### Q8：配置修改后需要重启吗？

- **OpenAkita Desktop**：保存后可点击「应用并重启」按钮，或在状态页面重启服务
- **CLI / 手动**：修改 `.env` 或 `llm_endpoints.json` 后需要重启服务
- **API**：`POST /api/config/reload` 可热重载端点配置（无需完全重启）

---

## 附录：完整 .env API Key 模板

```bash
# ========== LLM API Keys ==========

# Anthropic
ANTHROPIC_API_KEY=
ANTHROPIC_BASE_URL=https://api.anthropic.com

# 通义千问 (DashScope)
# 申请：https://dashscope.console.aliyun.com/
DASHSCOPE_API_KEY=

# DeepSeek
# 申请：https://platform.deepseek.com/
DEEPSEEK_API_KEY=

# 月之暗面 (Kimi)
# 中国区：https://platform.moonshot.cn/console
# 国际区：https://platform.moonshot.ai/console/api-keys
KIMI_API_KEY=

# 智谱 AI
# 国内区：https://open.bigmodel.cn/usercenter/apikeys
# 国际区：https://z.ai/manage-apikey/apikey-list
ZHIPU_API_KEY=

# MiniMax
# 中国区：https://platform.minimaxi.com/user-center/basic-information/interface-key
# 国际区：https://platform.minimax.io/user-center/basic-information/interface-key
MINIMAX_API_KEY=

# OpenAI
# 申请：https://platform.openai.com/api-keys
OPENAI_API_KEY=

# OpenRouter
# 申请：https://openrouter.ai/
OPENROUTER_API_KEY=

# SiliconFlow
# 申请：https://siliconflow.cn/
SILICONFLOW_API_KEY=
```

---

> **文档版本**：v1.0
> **最后更新**：2026-02-13
> **适用版本**：OpenAkita v0.x+

