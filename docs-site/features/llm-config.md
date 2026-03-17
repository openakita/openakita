# LLM 端点配置

## 什么是 LLM 端点

大语言模型（LLM）就像 AI 的大脑——OpenAkita 本身是"身体"，负责思考、使用工具、管理记忆，而 LLM 提供"智力"。

**端点（Endpoint）** 是连接 LLM 服务的入口。你需要至少配置一个端点，Agent 才能开始工作。每个端点由三部分组成：

- **Provider**：服务提供商（如 Anthropic、OpenAI、DeepSeek 等）
- **API Key**：访问凭证，从服务商处获取
- **Base URL**：API 地址，大多数提供商有默认值

## 支持的服务商

OpenAkita 通过 OpenAI 兼容协议支持 **30+ 家 LLM 服务商**：

| 分类 | 服务商 |
|------|--------|
| **国际主流** | Anthropic Claude、OpenAI（GPT-4o / o3）、Google Gemini |
| **国内主流** | 通义千问（DashScope）、DeepSeek、Kimi（Moonshot）、智谱 GLM |
| **聚合平台** | SiliconFlow（硅基流动）、OpenRouter、Together AI |
| **本地部署** | Ollama、LM Studio、vLLM、LocalAI |

## 配置步骤

[打开 LLM 配置](/web/#/config/llm)

### 1. 添加端点

点击「添加端点」，填写以下信息：

| 字段 | 说明 |
|------|------|
| **提供商** | 从下拉列表选择，或选择"自定义"填入兼容 API |
| **API Key** | 你的 API 密钥（本地存储，不会上传） |
| **Base URL** | API 基础地址，选择提供商后自动填入 |
| **模型** | 选择或手动输入模型名称（如 `claude-sonnet-4-20250514`） |

### 2. 高级参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| **优先级** | 数字越小越优先，Agent 优先使用高优先级端点 | `0` |
| **最大输出** | 单次回复的最大 token 数 | 模型默认 |
| **上下文窗口** | 模型可处理的最大上下文长度 | 自动检测 |
| **超时时间** | 请求超时秒数 | `120` |
| **RPM 限制** | 每分钟最大请求数，防止触发速率限制 | 不限制 |

### 3. 专用端点（可选）

- **Coding Plan 端点**：用于代码规划任务的专用模型
- **Compiler 端点**：身份编译等内部任务使用的模型
- **STT 端点**：语音转文字（Speech-to-Text）服务配置

## 多端点与故障转移

配置多个端点后，OpenAkita 会自动管理：

- **优先级调度**：按优先级从高到低选择可用端点
- **健康检查**：定期检测端点可用性
- **自动切换**：当前端点失败时，自动 fallback 到下一个端点
- **能力路由**：根据任务需要的能力（text / vision / video / tools / thinking）选择最合适的端点

```
请求 → 能力匹配 → 优先级排序 → 健康检查 → 发送到最优端点
                                    ↓ 失败
                              自动切换下一个端点
```

## 模型切换命令

在对话中随时切换模型：

| 命令 | 说明 |
|------|------|
| `/model` | 查看当前使用的模型与端点信息 |
| `/switch deepseek` | 临时切换到 DeepSeek（按名称模糊匹配） |
| `/priority` | 查看端点优先级列表 |
| `/restore` | 恢复为默认模型配置 |

::: info 说明
`/switch` 仅影响当前会话。重启或新建会话后恢复默认配置。
:::

## 常见问题

**Q: 填入 API Key 后提示连接失败？**
检查：① API Key 是否正确 ② Base URL 是否正确 ③ 网络是否需要代理 ④ 账户余额是否充足

**Q: 本地模型（Ollama）怎么配？**
Provider 选 Ollama，Base URL 填 `http://127.0.0.1:11434/v1`，API Key 填任意值即可。

## 相关页面

- [聊天对话](/features/chat) — 在对话中使用模型切换命令
- [配置向导详解](/advanced/wizard) — 初次使用的完整配置流程
- [技能管理](/features/skills) — 部分技能需要特定模型能力
- [高级设置](/advanced/advanced) — 更多 LLM 行为调优选项
