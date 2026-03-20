# OpenAkita 常见问题解答（FAQ）

## 安装与配置

### Q: OpenAkita 支持哪些操作系统？

**A**: OpenAkita 支持：
- Windows 10/11 (x86_64)
- macOS 12+ (Intel/Apple Silicon)
- Linux (x86_64，包括 Ubuntu、Debian、CentOS 等)

### Q: 我需要安装 Python 吗？

**A**: 取决于安装方式：
- **桌面客户端**：不需要，内置 Python 运行环境
- **pip 安装**：需要 Python 3.11+
- **源码安装**：需要 Python 3.11+

### Q: 如何获取 LLM API 密钥？

**A**: 不同服务商的申请方式：
- **Anthropic Claude**: https://console.anthropic.com/
- **阿里云通义千问**: https://dashscope.console.aliyun.com/
- **DeepSeek**: https://platform.deepseek.com/
- **Kimi (月之暗面)**: https://platform.moonshot.cn/
- **智谱 AI**: https://open.bigmodel.cn/

### Q: 可以使用本地模型吗？

**A**: 可以！支持 Ollama 和 LM Studio：
```bash
# Ollama 配置
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
DEFAULT_MODEL=llama3.2

# LM Studio 配置
OPENAI_BASE_URL=http://localhost:1234/v1
OPENAI_API_KEY=lm-studio
```

### Q: 配置后如何验证是否成功？

**A**: 运行以下命令：
```bash
openakita status          # 查看 Agent 状态
openakita config show     # 显示当前配置
openakita config validate # 验证配置
```

---

## 使用问题

### Q: OpenAkita 能做什么？

**A**: OpenAkita 可以：
- 📝 编写和调试代码
- 🔍 上网搜索信息
- 📁 管理文件和目录
- 🌐 浏览器自动化
- 💬 接入 IM 平台（Telegram/飞书/钉钉等）
- ⏰ 定时执行任务
- 🧠 学习你的偏好和习惯
- 🤖 多 Agent 协作完成复杂任务

### Q: 如何让 OpenAkita 记住我的偏好？

**A**: OpenAkita 会自动从对话中学习并记忆：
- 直接在对话中告诉它你的偏好
- 多次重复后会自动存入长期记忆
- 通过 `search_memory` 命令查看已记住的内容

### Q: 如何切换人格？

**A**: 在对话中直接说：
```
切换到技术专家人格
切换到女友人格
切换到 Jarvis
```

或在配置中设置：
```bash
PERSONA_NAME=tech_expert
```

### Q: 什么是计划模式？

**A**: 复杂任务会自动进入计划模式：
- 自动拆解为多个步骤
- 每步独立追踪进度
- 失败时自动回退换方案
- 可视化进度条展示

### Q: 如何查看 AI 的思考过程？

**A**: 启用 Thinking 模式：
- 在对话中说"开启深度思考"
- 或在配置中使用带 `-thinking` 后缀的模型
- 思考过程会实时流式展示

---

## IM 通道接入

### Q: 支持哪些 IM 平台？

**A**: 支持 6 大平台：
- Telegram
- 飞书 (Lark)
- 企业微信
- 钉钉
- QQ 官方机器人
- OneBot (兼容 NapCat/Lagrange/go-cqhttp)

### Q: 如何在群聊中使用？

**A**: 不同平台策略：
- **默认**: @Bot 时回复，不@则安静围观
- **Telegram**: 可配置 `TELEGRAM_GROUP_RESPONSE_MODE`
  - `all`: 所有消息都回复
  - `mention_only`: 仅@时回复
  - `mention_or_reply`: @或回复 Bot 时回复

### Q: 发送图片/语音会被处理吗？

**A**: 会！OpenAkita 支持：
- 📷 图片理解（多模态）
- 🎤 语音识别（自动转文字）
- 📎 文件交付（AI 生成的文件直接推送）

### Q: 为什么我的 Bot 不回复？

**A**: 检查以下几点：
1. IM 通道是否启用：`TELEGRAM_ENABLED=true`
2. Token/密钥是否正确
3. 网络是否通畅（某些平台需要公网 IP）
4. 查看日志：`LOG_LEVEL=DEBUG`

---

## 技能系统

### Q: 什么是技能？

**A**: 技能是 OpenAkita 的可扩展能力模块：
- 封装特定领域的功能
- 可在线搜索安装
- 支持 GitHub 直装
- AI 可现场生成新技能

### Q: 如何安装技能？

**A**: 多种方式：
```bash
# 从技能市场搜索安装
openakita skill search "markdown"
openakita skill install skill-name

# 从 GitHub 安装
openakita skill install-from-github https://github.com/user/repo

# AI 现场生成
让 AI 帮你创建一个新技能
```

### Q: 如何创建自己的技能？

**A**: 参考 [技能系统文档](skills.md)：
1. 创建 `SKILL.md` 定义技能元数据
2. 编写脚本文件（Python）
3. 放入 `skills/your-skill/` 目录
4. 加载技能：`openakita skill load your-skill`

### Q: 技能安装后不生效怎么办？

**A**: 尝试：
1. 检查技能是否启用：`openakita skill list`
2. 重新加载技能：`openakita skill reload your-skill`
3. 查看日志排查错误
4. 重启 Agent

---

## 多 Agent 协作

### Q: 什么是多 Agent 协作？

**A**: OpenAkita 内置多 Agent 编排系统：
- 不同 Agent 擅长不同领域
- 自动匹配最合适的 Agent
- 多个 Agent 并行工作
- 一个搞不定自动接力

### Q: 如何创建自定义 Agent？

**A**: 在对话中直接说：
```
创建一个擅长写作的 Agent
创建一个数据分析专家 Agent
```

或在 Agent Dashboard 中可视化创建。

### Q: Agent 委派深度有限制吗？

**A**: 有，最大 5 层委派深度，防止递归失控。

---

## 故障排查

### Q: 遇到 "API key not found" 错误

**A**: 解决方案：
1. 检查 `.env` 文件是否存在
2. 确认包含正确的 API 密钥变量名
3. 重启 Agent 使配置生效

### Q: 遇到 "Connection timeout" 错误

**A**: 解决方案：
1. 检查网络连接
2. 使用代理或国内镜像
3. 增加超时设置：`REQUEST_TIMEOUT=60`

### Q: 遇到 "Python version error" 错误

**A**: 解决方案：
1. 检查 Python 版本：`python --version`
2. 确保版本 ≥ 3.11
3. 升级或重新安装 Python

### Q: 浏览器自动化报错 "Chromium not found"

**A**: 解决方案：
```bash
# 手动安装 Playwright 浏览器
playwright install chromium

# 或设置环境变量
PLAYWRIGHT_BROWSERS_PATH=/path/to/browsers
```

### Q: 对话被提前中止 (Aborted)

**A**: 可能原因：
1. SSE 流超时断开
2. 前端 abort controller 误触发
3. 后端迭代上限

解决方案：
1. 查看日志排查具体原因
2. 增加 `MAX_ITERATIONS` 限制
3. 检查网络连接稳定性

---

## 性能与优化

### Q: 如何提高响应速度？

**A**: 优化建议：
1. 使用更快的模型（如 `claude-sonnet` 而非 `claude-opus`）
2. 减少 `MAX_TOKENS` 默认值
3. 启用国内镜像加速
4. 降低推理复杂度（关闭 Thinking 模式）

### Q: 如何降低使用成本？

**A**: 节省 Token：
1. 使用性价比更高的模型（如 DeepSeek）
2. 设置 `MAX_TOKENS` 限制
3. 启用资源预算控制
4. 避免过长的上下文

### Q: 内存占用过高怎么办？

**A**: 优化方案：
1. 定期清理记忆：`consolidate_memories`
2. 限制 `MAX_ITERATIONS`
3. 关闭不需要的功能（如表情包）
4. 重启 Agent 释放资源

---

## 安全与隐私

### Q: 我的数据存在哪里？

**A**: 所有数据本地存储：
- 记忆：`data/agent.db` (SQLite)
- 配置：工作区 `.env` 文件
- 对话历史：本地文件
- **不会上传到任何云端**

### Q: 如何备份我的数据？

**A**: 备份以下目录：
```bash
~/.openakita/workspaces/    # 工作区配置
~/.openakita/data/          # 记忆和对话历史
```

### Q: 如何重置所有配置？

**A**: 删除工作区目录：
```bash
rm -rf ~/.openakita/workspaces/default
openakita init  # 重新初始化
```

---

## 贡献与社区

### Q: 如何为 OpenAkita 做贡献？

**A**: 参考 [贡献指南](CONTRIBUTING-zh.md)：
1. Fork 仓库
2. 创建功能分支
3. 提交代码
4. 发起 Pull Request

### Q: 在哪里可以获得帮助？

**A**: 社区渠道：
- 📖 [文档](docs/)
- 💬 [GitHub Discussions](https://github.com/openakita/openakita/discussions)
- 🐛 [Issue Tracker](https://github.com/openakita/openakita/issues)
- 📧 Email: zacon365@gmail.com

### Q: 有中文社区吗？

**A**: 有！加入方式：
- 微信公众号：扫码关注
- 微信群：扫码加入（7 天更新）
- QQ 群：854429727

---

## 其他问题

### Q: OpenAkita 是免费的吗？

**A**: 是的！OpenAkita 是开源软件（Apache 2.0 许可证），完全免费使用。但 LLM API 调用可能产生费用（取决于你选择的服务商）。

### Q: 可以商用吗？

**A**: 可以！Apache 2.0 许可证允许商业用途。

### Q: 如何保持更新？

**A**: 
- **桌面客户端**: 自动检测并提示更新
- **pip 安装**: `pip install -U openakita`
- **源码安装**: `git pull upstream main`

### Q: 发现 Bug 怎么办？

**A**: 请在 GitHub 提交 Issue：
1. 使用 Bug 报告模板
2. 提供详细复现步骤
3. 附上环境信息和日志
4. 如可能提供修复建议

---

## 还需要帮助？

如果以上 FAQ 没有解答你的问题：

1. 查看完整 [文档](docs/)
2. 搜索 [GitHub Issues](https://github.com/openakita/openakita/issues)
3. 在 [Discussions](https://github.com/openakita/openakita/discussions) 提问
4. 加入社区群聊寻求帮助
