# OpenAkita 用户交互全面重构 - 综合测试计划

- **基于计划**: 用户交互全面重构 (73 项改进, 11 批次)
- **测试规约**: ai-exploratory-testing.mdc
- **前置条件**: 后端服务运行中, 用户提供 PID 和端口地址

---

## 执行目标与结论（先回答核心问题）

本计划目标是让 AI 可以自动化发现真实对话中的 bug（不仅是关键词断言），并覆盖两种运行形态：

- **形态 A（CLI 安装态）**: 通过 `pipx/pip` 安装并运行 `openakita`，重点验证核心后端能力、CLI 交互、SSE 对话质量。
- **形态 B（Desktop EXE 打包态）**: 通过 Setup Center/打包后的 Desktop 运行同一套后端接口与前端联调，重点验证打包环境差异（进程拉起、路径、配置、权限、跨端通信）。

**结论**:
- CLI 安装完成后，**可以完成绝大多数后端 API + AI 探索性对话测试**（阶段 0/1/5/6/7 的主体）。
- 但它**不能替代 EXE 打包态验证**，尤其是 Desktop 端与打包运行时相关问题（阶段 3/4 以及打包场景下的阶段 0/6/7 复跑）。
- 推荐执行策略：**先 CLI 跑通主链路并修核心 bug，再在 EXE 打包态做回归与环境差异验证**。

---

## 测试总览

本测试计划覆盖 73 项用户交互重构改动，按 **8 个阶段 + 2 种运行形态** 组织：

### 拆分文档（执行入口）

- Agent 自动执行入口: `tests/e2e/agent-auto-test-runbook.md`
- 人工执行入口: `tests/e2e/manual-test-checklist.md`
- Windows 打包与安装顺序: `tests/e2e/windows-packaging-install-test-order.md`

| 阶段 | 名称 | 主要方式 | CLI 安装态 | EXE 打包态 | 预计耗时 |
|------|------|----------|------------|-------------|----------|
| 0 | 环境就绪验证 | Agent 自动 API | 必跑 | 必跑（重新记录 PID/端口） | 2 分钟/形态 |
| 1 | 后端基础设施验证 | Agent 自动 API + 代码审查 | 必跑 | 抽样复核（关键接口） | 10 分钟 |
| 2 | CLI 终端功能验证 | 人工操作 + Agent 审查 | 必跑 | 跳过 | 20 分钟 |
| 3 | Desktop 前端核心功能 | 人工操作 + Agent 审查 | 跳过 | 必跑 | 40 分钟 |
| 4 | 前端架构与性能验证 | 代码审查 + 人工体感 | 跳过 | 必跑 | 15 分钟 |
| 5 | IM 通道功能验证 | Agent API + 代码审查 | 必跑 | 建议复跑 | 10 分钟 |
| 6 | AI 探索性多轮对话测试 | Agent 自动（20+轮） | 必跑（完整 20+ 轮） | 必跑（最少 8~10 轮回归） | 30+15 分钟 |
| 7 | 日志审计与最终报告 | Agent 自动 | 必跑 | 必跑（单独报告） | 10 分钟/形态 |

### 推荐执行顺序

1. **先跑 CLI 安装态**：阶段 0 -> 1 -> 2 -> 5 -> 6 -> 7  
2. **再跑 EXE 打包态**：阶段 0 -> 3 -> 4 -> 6（回归子集）-> 7  
3. 对两份报告做差异对比，优先处理“仅在 EXE 打包态出现”的问题

---

## 阶段 0: 环境就绪验证

> Agent 自动执行, 无需人工介入

### 0.1 健康检查

```
GET /api/health
```

**验证项**:
- `status` 为 `ok`
- `agent_initialized` 为 `true`
- 记录 PID 和版本号

### 0.2 LLM 端点连通性

```
POST /api/health/check
Content-Type: application/json
{"dry_run": true}
```

**验证项**:
- 至少一个端点 `healthy: true`
- 记录模型名和延迟

### 0.3 会话系统就绪

```
GET /api/sessions
```

**验证项**:
- `ready` 为 `true`

### 0.4 命令注册表 (Batch 1: 3.1/1.3)

```
GET /api/commands?scope=desktop
```

**验证项**:
- 返回命令列表非空
- 每条命令含 `name`, `description`, `scope` 字段
- 包含 `/help`, `/clear`, `/model`, `/thinking` 等核心命令
- `scope` 过滤生效 (desktop 命令不含 cli-only 命令)

### 0.5 /clear 端点 (Batch 1: 3.3)

```
POST /api/chat/clear
Content-Type: application/json
{"conversation_id": "test_env_check"}
```

**验证项**:
- 返回成功状态码 (200/204)
- 不存在的 conversation_id 应优雅处理 (不报 500)

---

## 阶段 1: 后端基础设施验证

> Agent 自动执行

### 1.1 事件类型统一 (Batch 1: 3.1)

**代码审查验证**:
- `src/openakita/events.py` 的 `StreamEventType` 枚举包含所有 22 种事件
- `apps/setup-center/src/streamEvents.ts` 与 Python 端 1:1 同步
- 枚举值类型为 `str, Enum` (可直接字符串比较)

**API 验证** (通过一次聊天请求的 SSE 流):

```
POST /api/chat
Content-Type: application/json
{"message": "你好，请简单介绍一下你自己", "conversation_id": "test_events"}
```

**验证项**:
- SSE 流中包含 `iteration_start`, `text_delta`, `done` 事件
- `done` 事件中的 `type` 字段值与 `StreamEventType.DONE` 一致
- 无未知/拼写错误的事件类型

### 1.2 错误处理统一 (Batch 1: 3.2)

**代码审查验证**:
- `src/openakita/utils/errors.py` 存在且导出 `classify_error()` 和 `format_user_friendly_error()`
- 错误分类覆盖: content_filter, auth, quota, timeout, network, server, unknown
- CLI (`stream_renderer.py`) 和 gateway 均引用此模块

### 1.3 后端 Policy 健壮性 (Batch 7: 8.5)

**代码审查验证**:
- `src/openakita/core/policy.py` 中 `_pending_ui_confirms` 有 TTL 清理机制
- 存在 `_cleanup_expired_confirms()` 方法
- `cleanup_session()` 方法存在, 可在 session 结束时调用
- `timeout_seconds` / `default_on_timeout` 配置已实现

### 1.4 后端 Session 安全 (Batch 7: 8.6)

**代码审查验证**:
- `sessions/manager.py` 使用 `Path.replace()` 或 `atomic_json_write` (非 `Path.rename()`)
- 后台保存任务有注册/追踪机制
- cancel 端点有 generation 检查逻辑

### 1.5 后端 SSE 事件保障 (Batch 7: 8.10)

**代码审查验证**:
- `chat_with_session_stream` 初始化异常时补发 `error` + `done` 事件
- `reason_stream` 异常路径调用 `_finalize_session`
- `chat_insert` 的 stop 路径调用 `finish()` 释放 busy 锁
- pool 模式下缺 `conversation_id` 返回 400

### 1.6 设置变更重启提示后端 (Batch 10: 9.11)

```
POST /api/config/env
Content-Type: application/json
{"entries": {"OPENAI_API_KEY": "test-key-placeholder"}, "delete_keys": []}
```

**验证项**:
- 响应包含 `restart_required` 和 `hot_reloadable` 字段
- LLM 类 key (OPENAI_*) 返回 `restart_required: false`, `hot_reloadable: true`
- IM 类 key (TELEGRAM_*) 返回 `restart_required: true`

**注意**: 测试后需还原配置, 避免污染环境

---

## 阶段 2: CLI 终端功能验证

> **需要人工操作**, Agent 提供指令, 人工在终端执行并反馈结果

### 2.1 CLI 流式输出 (Batch 1: 1.1)

**人工操作步骤**:

1. 打开新终端窗口
2. 运行 `openakita` 进入交互模式
3. 输入: `你好，请简单介绍一下你自己`
4. **观察并报告**:
   - [ ] 是否看到逐字流式输出 (而非等待后一次性显示)
   - [ ] 是否显示 "思考中 (轮次 N)..." 状态
   - [ ] 思考结束后是否显示耗时 (如 "思考 2.3s")
   - [ ] 工具调用是否有实时展示 (如 "⎿ search_memory ...")
   - [ ] 完成后文本渲染是否完整 (Markdown 格式)

5. 输入一个会触发工具调用的问题: `现在几点了`
6. **观察并报告**:
   - [ ] `tool_call_start` 是否实时显示工具名
   - [ ] `tool_call_end` 是否显示 done/failed 状态
   - [ ] 多次迭代是否有轮次计数显示

### 2.2 CLI 输入系统 (Batch 1: 1.2)

**人工操作步骤**:

1. 在交互模式中:
   - [ ] 按上箭头 - 是否显示上一条输入历史
   - [ ] 按下箭头 - 是否前进历史
   - [ ] 输入 `/` - 是否弹出命令补全列表
   - [ ] 输入 `/he` - 是否过滤显示 `/help`
   - [ ] 按 Tab - 是否补全选中命令
   - [ ] 按 Alt+Enter 或 Shift+Enter - 是否换行 (多行输入)
   - [ ] 按 Ctrl+C 一次 - 是否清空当前行 (而非退出)
   - [ ] 按 Ctrl+C 两次 - 是否退出

2. 退出后重新进入:
   - [ ] 上箭头能否找到上次会话的历史输入

### 2.3 CLI 斜杠命令 (Batch 1: 1.3)

**人工操作步骤**:

1. 输入 `/help`
   - [ ] 是否显示完整的命令列表
   - [ ] 列表是否包含描述信息
   - [ ] 是否与 Desktop 端命令一致 (可对比 GET /api/commands 结果)

2. 输入 `/status`
   - [ ] 是否显示 Agent 状态信息

3. 输入 `/clear`
   - [ ] 是否清空对话上下文
   - [ ] 清空后输入 "刚才我说了什么" - Agent 是否确认无历史

4. 输入 `/model`
   - [ ] 是否显示可用模型列表或当前模型

### 2.4 CLI 安全确认 (Batch 1: 1.4)

**人工操作步骤**:

1. 输入一个会触发文件写入的指令: `在当前目录创建一个 test_security.txt 文件，内容为 hello`
2. **观察**:
   - [ ] 是否弹出安全确认面板 (彩色边框 Panel)
   - [ ] 面板是否显示工具名、原因、风险等级
   - [ ] 是否提示 y/n/sandbox 选项
3. 输入 `n` 拒绝
   - [ ] 是否正确拒绝并继续对话
4. 重新发送相同指令, 输入 `y` 允许
   - [ ] 文件是否创建成功

### 2.5 CLI 快速配置模式 (Batch 10: 9.12)

**人工操作步骤**:

1. 运行 `openakita init --quick` (或 `-q`)
   - [ ] 是否显示 "Quick Setup Mode" 面板
   - [ ] 是否只有 3 步: Provider, API Key, Model
   - [ ] 风险须知是否使用 y/N 确认 (非精确文字匹配)
2. 故意输入错误的 API Key, 测试连接失败后:
   - [ ] 是否提供 "重试/修改配置/跳过" 选项菜单
3. 按 Ctrl+C 取消
   - [ ] 是否优雅退出 (显示 "安装已取消")

### 2.6 CLI 向导返回导航 (Batch 8: 1.8)

**人工操作步骤**:

1. 运行 `openakita init` (完整模式)
   - [ ] 欢迎页和风险确认是否纳入步骤计数 (如 "Step 1/N")
   - [ ] 风险确认页是否用 Confirm.ask 而非精确文字
2. 进入 LLM 配置步骤后:
   - [ ] 是否有返回上一步的选项/提示

---

## 阶段 3: Desktop 前端核心功能验证

> **需要人工操作** Desktop 应用, Agent 提供检查清单

### 3.1 错误展示 (Batch 2: 2.2)

**人工操作**: 在 Desktop 聊天界面

1. 触发一个错误 (如故意使用无效 API Key 发送消息, 或断网后发送)
   - [ ] 错误是否显示为结构化卡片 (非 Markdown 内嵌文本)
   - [ ] 卡片是否有颜色边框 (按严重度)
   - [ ] 是否有"重试"按钮
   - [ ] 是否有"复制详情"按钮
   - [ ] 点击复制 - 剪贴板是否包含错误详情

### 3.2 骨架屏与加载状态 (Batch 4: 2.3)

**人工操作**:

1. 创建一个有多条消息的会话, 然后切换到另一个会话再切回来
   - [ ] 切换时消息区是否显示骨架屏动画 (脉冲效果)
   - [ ] 骨架屏是否模拟消息气泡形态
   - [ ] 加载完成后骨架屏是否消失

### 3.3 消息搜索 (Batch 4: 2.9)

**人工操作**:

1. 在有多条消息的会话中按 Ctrl+F
   - [ ] 是否弹出搜索栏
   - [ ] 输入关键词后是否实时高亮匹配
   - [ ] 按 Enter 是否跳转到下一个匹配
   - [ ] 按 Shift+Enter 是否跳转到上一个匹配
   - [ ] 按 Esc 是否关闭搜索栏

### 3.4 快捷键面板 (Batch 2: 2.6)

**人工操作**:

1. 按 Ctrl+/ (或查看 placeholder 提示)
   - [ ] 是否弹出快捷键面板
   - [ ] 面板是否列出所有可用快捷键
   - [ ] 按 Esc 是否关闭面板

2. 输入框 placeholder:
   - [ ] 空闲时是否显示 "Enter 发送, Shift+Enter 换行"
   - [ ] 流式中是否显示 "Enter 排队, Esc 停止"

### 3.5 斜杠命令 (Batch 2: 2.5)

**人工操作**:

1. 在输入框输入 `/`
   - [ ] 是否弹出命令面板
   - [ ] 输入 `/he` 是否过滤显示 `/help`
   - [ ] 选择 `/help` 是否显示命令列表
2. 输入 `/clear`
   - [ ] 是否清空当前会话消息
   - [ ] 是否同步清除后端 session (可通过再次对话验证 Agent 无历史记忆)
3. 输入一个不存在的命令如 `/xyz`
   - [ ] 是否显示 "无匹配命令" 提示 (非静默消失)

### 3.6 消息编辑与重新生成 (Batch 2: 2.18)

**人工操作**:

1. 发送一条消息, 等待 AI 回复
2. 鼠标悬停在用户消息上:
   - [ ] 是否显示"编辑"按钮
3. 点击编辑:
   - [ ] 消息内容是否回填到输入框
   - [ ] 该消息及之后的消息是否被删除
4. 鼠标悬停在 AI 回复上:
   - [ ] 是否显示"重新生成"按钮
5. 点击重新生成:
   - [ ] 是否删除该回复并重新请求

### 3.7 对话回卷 (Batch 6: 5.4)

**人工操作**:

1. 进行 5+ 轮对话后, 鼠标悬停在第 3 条消息上
   - [ ] 是否显示"回退到此处"按钮
2. 点击回退:
   - [ ] 是否弹出确认弹窗
   - [ ] 确认后第 3 条之后的消息是否全部删除

### 3.8 语音输入 (Batch 4: 2.12)

**人工操作**:

1. 点击麦克风按钮开始录音
   - [ ] 是否显示录制中动画 (脉冲效果)
   - [ ] 是否显示已录制时长
2. 停止录音
   - [ ] 是否正常结束并发送

如果没有麦克风:
   - [ ] 是否显示 "未找到录音设备" 提示 (区别于"权限被拒绝")

### 3.9 附件上传 (Batch 4: 2.13)

**人工操作**:

1. 点击附件按钮选择文件
   - [ ] 是否显示上传进度
2. 选择同名的两个不同文件
   - [ ] 是否都正确显示 (不互相覆盖)
3. 拖拽文件到聊天区域
   - [ ] 是否触发上传 (HTML5 drag-and-drop)
4. 如果上传失败 (如文件过大):
   - [ ] 是否弹出 toast 错误提示

### 3.10 右键菜单 (Batch 4: 2.8)

**人工操作**:

1. 右键点击会话列表项
   - [ ] 是否弹出上下文菜单
   - [ ] 按上下箭头是否能导航菜单项
   - [ ] 按 Enter 是否选中
   - [ ] 菜单是否钳制在视口内 (在屏幕边缘右键试)

### 3.11 Lightbox 图片预览 (Batch 4: 2.7)

**人工操作** (需要会话中有图片):

1. 点击图片打开预览
   - [ ] 是否全屏显示
2. 滚动鼠标滚轮
   - [ ] 是否缩放 (0.2x - 10x)
   - [ ] 是否显示缩放百分比
3. 拖拽图片
   - [ ] 是否平移
4. 按 +/- 键
   - [ ] 是否缩放
5. 按 0
   - [ ] 是否重置缩放
6. 按 Esc
   - [ ] 是否关闭预览

### 3.12 空状态引导 (Batch 3: 2.16)

**人工操作**:

1. 新建一个会话
   - [ ] 是否显示 3-4 张快速开始卡片
   - [ ] 卡片内容是否为实际生产场景 (如 "做一份PPT大纲", "打开百度")
2. 点击一张卡片
   - [ ] 文字是否填入输入框 (不自动发送)
   - [ ] 可以修改后再发送
3. 发送第一条消息后
   - [ ] 卡片是否消失

### 3.13 上下文用量可视化 (Batch 3: 2.17)

**人工操作**:

1. 进行几轮对话后, 查看输入区或顶栏
   - [ ] 是否有 context window 使用量指示器
   - [ ] 指示器是否随对话增长而变化

### 3.14 安全确认弹窗 (Batch 3: 2.11)

**人工操作** (触发文件操作让 Agent 请求确认):

1. 观察确认弹窗:
   - [ ] 超时是否为 120 秒 (非 60 秒)
   - [ ] 鼠标悬停/点击后倒计时是否暂停
   - [ ] 最后 10 秒是否显示红色警告
   - [ ] 是否有"延长时间"按钮

### 3.15 消息操作菜单 (Batch 6: 6.3)

**人工操作**:

1. 鼠标悬停在消息上
   - [ ] 是否显示操作栏 (复制/编辑/回退/重新生成)
2. 点击"复制"
   - [ ] 消息文本是否复制到剪贴板

### 3.16 @ Agent 联想 (Batch 6: 6.1)

**人工操作**:

1. 在输入框输入 `@`
   - [ ] 是否弹出联想面板
   - [ ] 面板是否显示已配置的 Agent 列表
2. 继续输入几个字母过滤
   - [ ] 是否实时过滤
3. 按上下键选择, 按 Tab/Enter 确认
   - [ ] 是否插入完整的 Agent 引用格式

### 3.17 输入 Undo 栈 (Batch 8: 6.2)

**人工操作**:

1. 在输入框输入一段文字, 等待 1 秒
2. 删除部分内容, 再输入新内容
3. 按 Ctrl+Z
   - [ ] 是否恢复上一步的输入内容
4. 按 Ctrl+Shift+Z
   - [ ] 是否重做

### 3.18 大文本粘贴检测 (Batch 8: 6.4)

**人工操作**:

1. 复制一段超过 800 字符的文本
2. 在输入框粘贴
   - [ ] 是否折叠显示 (而非全部展开)
   - [ ] 是否显示字符数/行数摘要
   - [ ] 是否有"展开预览"按钮

### 3.19 加载状态轮播提示 (Batch 8: 6.6)

**人工操作**:

1. 发送一条需要较长处理时间的消息
2. 观察 Spinner 区域
   - [ ] 是否每 8-10 秒轮换一条提示 (快捷键、命令等)
   - [ ] 提示文字是否为 dim 样式

### 3.20 长闲置回归检测 (Batch 8: 6.7)

**说明**: 此功能需要 token 超阈值 + 空闲超 75 分钟。实际测试中难以等待, 可通过代码审查验证逻辑。

**代码审查验证**:
- `IDLE_THRESHOLD_MS = 75 * 60 * 1000`
- `IDLE_TOKEN_THRESHOLD = 50_000`
- 弹出提示包含: 继续/清空/新建 选项

### 3.21 对话导出 (Batch 8: 7.1)

**人工操作**:

1. 在有多条消息的会话的右键/菜单中找到"导出"
2. 选择 Markdown 格式导出
   - [ ] 是否触发文件下载
   - [ ] 文件内容是否包含完整对话 (含工具调用)
3. 选择 JSON 格式导出
   - [ ] JSON 结构是否完整

### 3.22 限流专用 UX (Batch 6: 6.5)

**说明**: 需要实际触发 429/529 错误。可通过代码审查验证。

**代码审查验证**:
- 限流错误显示为结构化卡片 (非纯文本)
- 前 3 次静默重试逻辑存在
- 重试倒计时 UI 存在

### 3.23 Token 用量/成本提示 (Batch 6: 5.1)

**人工操作**:

1. 进行多轮对话后查看顶栏/输入区
   - [ ] 是否显示 token 消耗信息
   - [ ] 长会话空闲后是否提示 "/clear 可节省 token"

### 3.24 首次引导步骤名 (Batch 10: 9.7)

**说明**: 需要全新环境或重置 onboarding 状态。

**代码审查验证**:
- Onboarding 步骤圆点旁有步骤名称标签
- 已完成步骤显示勾选 + 名称

### 3.25 全局快捷键 (Batch 10: 9.9) — 已移除

全局快捷键 (Ctrl+Shift+A 唤起窗口) 已随 `tauri-plugin-global-shortcut` 插件移除
(该插件被定位为 WebView2 事件循环重入崩溃的回归来源, 引入于 v1.27.9)。此测试项作废。

### 3.26 系统托盘 (Batch 10: 9.8)

**人工操作** (Desktop 应用):

1. 关闭 OpenAkita 主窗口
   - [ ] 是否最小化到托盘 (而非退出)
2. 查看托盘图标
   - [ ] 图标颜色是否反映服务状态 (绿/黄/红)
3. 右键托盘图标
   - [ ] 是否显示菜单 (打开/服务状态/退出)
4. 左键单击托盘图标
   - [ ] 是否打开主窗口

### 3.27 设置变更重启提示 (Batch 10: 9.11)

**人工操作**:

1. 打开设置页面, 修改一个 LLM 相关配置并保存
   - [ ] 是否显示绿色 Toast "已保存, 立即生效"
2. 修改一个 IM 相关配置并保存
   - [ ] 是否显示黄色 Toast "已保存, 需要重启服务才能生效"
   - [ ] 是否有"立即重启"按钮

### 3.28 跨视图集成 (Batch 10: 9.13)

**人工操作**:

1. 在聊天中输入 `/memory`
   - [ ] 是否显示记忆条目列表
2. 在聊天中输入 `/skills`
   - [ ] 是否显示已安装技能列表

### 3.29 IM 通道掉线告警 (Batch 9: 9.4)

**说明**: 需要有活跃的 IM 通道。如无 IM 通道, 通过代码审查验证。

**代码审查验证**:
- `ChatView.tsx` 中 `imChannelAlerts` 状态和 `im:channel_status` WebSocket 监听
- 掉线时顶部显示横幅
- `notifyError` 调用存在

### 3.30 工具可视化增强 (Batch 3: 2.10)

**人工操作**:

1. 发送一个会触发工具调用的请求
   - [ ] `tool_call_start` 时是否立即显示 loading 卡片
   - [ ] `tool_call_end` 时是否平滑过渡为结果
   - [ ] 卡片是否有稳定的 key (无闪烁)

### 3.31 子 Agent 卡片 (Batch 4: 2.14)

**说明**: 需要 multiAgentEnabled=true 且触发子 Agent 调用。

**代码审查验证**:
- 完成后保留 30 秒 (非 5 秒)
- `multiAgentEnabled=false` 时不启动轮询

### 3.32 队列与附件联动 (Batch 4: 2.15)

**人工操作**:

1. 发送一条消息让 AI 开始回复 (流式中)
2. 添加一个附件, 输入新消息按 Enter
   - [ ] 消息是否进入排队
   - [ ] 队列面板是否显示附件计数

---

## 阶段 4: 前端架构与性能验证

> Agent 代码审查 + 人工体感测试

### 4.1 ChatView 拆分验证

**代码审查**:
- `apps/setup-center/src/views/ChatView.tsx` 行数应显著减少 (目标 < 4500 行, 当前 4271 行)
- `views/chat/hooks/` 目录包含: `useMessages.ts`, `useQueryGuard.ts`, `useMdModules.ts`
- `views/chat/components/` 目录包含 16+ 个子组件文件
- `views/chat/utils/` 包含 `chatHelpers.ts`, `chatTypes.ts`
- `views/chat/index.ts` 正确 re-export

### 4.2 useReducer 消息状态 (Batch 7: 8.4)

**代码审查**:
- `hooks/useMessages.ts` 使用 `useReducer` 管理消息状态
- 定义了明确的 action 类型 (STREAM_APPEND, HYDRATE, RECOVERY_PATCH 等)
- 原有分散的 `setMessages` 调用已统一为 `dispatch`

### 4.3 QueryGuard (Batch 7: 8.1)

**代码审查**:
- `hooks/useQueryGuard.ts` 实现三状态机 (idle/querying/cancelling)
- generation 计数器防止过期回调覆盖
- `isStale(gen)` 检查在 SSE 回调中使用

**人工快速测试**:
1. 快速连续发送两条消息
   - [ ] 第一条是否被取消/排队
   - [ ] 不出现两个并行的流式回复

### 4.4 虚拟滚动 (Batch 5: 4.2)

**代码审查**:
- `components/MessageList.tsx` 使用 `react-virtuoso` 的 `Virtuoso` 组件
- `followOutput` 属性确保流式时自动滚动到底部

**人工性能体感**:
1. 在有 100+ 条消息的会话中滚动
   - [ ] 是否流畅 (无卡顿)
   - [ ] 快速滚动到顶部是否正常
   - [ ] 滚动到底部后新消息是否自动跟随

### 4.5 ErrorBoundary (Batch 5: 4.6)

**代码审查**:
- 消息渲染区域被 `<ErrorBoundary>` 包裹
- 单条消息渲染崩溃不导致整页白屏
- 降级 UI 显示 "此消息渲染失败"

### 4.6 代码分割 (Batch 5: 4.7)

**代码审查**:
- `App.tsx` 中非核心视图使用 `React.lazy`
- `Suspense` 包裹 lazy 组件
- PixelOfficeView, MemoryGraph3D 等重型视图懒加载

### 4.7 Toast 通知系统 (Batch 5: 4.5)

**代码审查**:
- 基于 `sonner` 的统一 toast 调用
- 错误、警告、信息三种级别
- 上传失败、SSE 错误等场景使用 toast 反馈

### 4.8 localStorage 性能 (Batch 5: 4.4)

**代码审查**:
- 使用 `requestIdleCallback` 或异步保存
- 流式期间不阻塞主线程

### 4.9 流式渲染优化 (Batch 5: 4.3)

**代码审查**:
- `remarkPlugins` 引用稳定化 (模块级, 非每次渲染新建)
- `MessageBubble`/`FlatMessageItem` 使用 `React.memo`

### 4.10 闭包 BUG 修复验证 (Batch 7: 8.2)

**代码审查**:
- `handleFileSelect` 的 `useCallback` 依赖包含 `apiBase`
- `toggleRecording` 的 `useCallback` 依赖包含 `apiBase`
- `stopStreaming` 使用 `activeConvIdRef.current` (非闭包 state)

### 4.11 内存安全修复 (Batch 7: 8.3)

**代码审查**:
- Recovery 轮询有取消机制 (AbortSignal 或 cancelled ref)
- Blob URL 在使用完毕后 `revokeObjectURL`
- 所有 `setTimeout`/`setInterval` 在 cleanup 中清理

### 4.12 防御性反序列化 (Batch 7: 8.7)

**代码审查**:
- `loadMessagesFromStorage` 有验证管道
- 过滤残缺消息、空白消息、孤立 thinking 块

### 4.13 前端数据完整性 (Batch 7: 8.9)

**代码审查**:
- 会话删除先调 API 再删 localStorage (原子化)
- `chat:title_update` 检查 `titleManuallySet` 标记
- `@org:` 未匹配时 toast 提示
- `chat:busy` 检查 `d.client_id` 存在性

### 4.14 i18n 完整性 (Batch 8: 7.2)

**代码审查**:
- ChatView 中搜索硬编码中文字符串
- 所有用户可见文案通过 `t()` 函数

### 4.15 无障碍 (Batch 8: 7.3)

**代码审查**:
- 消息列表有 `aria-live="polite"`
- 会话列表项有 `tabIndex`, `role`, `onKeyDown`
- 模式切换按钮有 `aria-label`

---

## 阶段 5: IM 通道功能验证

> Agent 代码审查 (大部分 IM 功能需要真实 IM 通道, 此阶段以代码验证为主)

### 5.1 消息分片序号 (Batch 9: 9.15)

**代码审查**:
- `text_splitter.py` 中 `add_fragment_numbers()` 函数存在
- 分片数 > 1 时添加 `[1/N]` 序号
- `estimate_number_prefix_len()` 预留序号长度

### 5.2 Smart 群聊反应 (Batch 9: 9.2)

**代码审查**:
- `gateway.py` 中 `_try_smart_reaction()` 方法存在
- 受 `SMART_REACTION_ENABLED` 环境变量控制
- `not in` 运算符使用正确 (非 `not ... in`)
- `base.py` 中 `add_reaction` capability 已声明
- `feishu.py` 和 `telegram.py` 实现了 `add_reaction` 且签名匹配 base

### 5.3 StreamPresenter (Batch 9: 9.3)

**代码审查**:
- `stream_presenter.py` 定义 `StreamPresenter` ABC
- `start()`, `update()`, `finalize()` 三段生命周期
- `NullStreamPresenter` 降级实现存在
- 共享节流逻辑 (`min_interval_ms`)

### 5.4 群聊上下文缓冲可见性 (Batch 9: 9.14)

**代码审查**:
- `_format_group_context()` 包含消息计数
- 格式: "基于最近 N 条群聊消息"

### 5.5 IM 跨平台格式 (Batch 9: 9.1)

**代码审查**:
- `markdown_to_plaintext()` 保留代码缩进和链接 URL
- 分片发送失败时的 `_fail_hint` 包含已成功段数

---

## 阶段 6: AI 探索性多轮对话测试

> Agent 自动执行, 按 ai-exploratory-testing.mdc 规范

### 6.0 运行形态要求

- **CLI 安装态**: 执行完整 20+ 轮探索性对话（作为主样本）。
- **EXE 打包态**: 至少复跑 8~10 轮，必须覆盖：
  - 事实记忆 + 信息纠正
  - 话题跳转 + 远距离回溯
  - 至少 1 次工具调用场景
- 两种形态的结果需在阶段 7 报告中分开记录，不混写。

### 6.1 测试设计

**会话 ID**: `test_interaction_refactor_YYYYMMDD`

**轮次规划** (至少 20 轮, 覆盖 7 个维度):

| 轮次 | 维度 | 测试内容 | 额外验证 |
|------|------|----------|----------|
| 1 | 事实记忆 | 告知一个虚构项目信息 | SSE 事件类型正确 |
| 2 | 事实记忆 | 追问细节 | 无不必要工具调用 |
| 3 | 事实记忆 | 要求复述全部 | 回复完整准确 |
| 4 | 计算追问 | 给出数字要求计算 | 工具调用效率 |
| 5 | 计算追问 | 在结果上追加计算 | 上下文连贯 |
| 6 | 话题跳转 | 突然问无关话题 | 不丢失原上下文 |
| 7 | 话题跳转 | 再跳回来 | 正确回忆早期信息 |
| 8 | 信息纠正 | 更正之前的事实 | 记忆更新 |
| 9 | 信息纠正 | 验证更正是否生效 | 不出现旧数据 |
| 10 | /clear 测试 | 执行 /clear 后提问 | Agent 无历史 |
| 11 | 新会话开始 | 新 conversation_id | token 重置 |
| 12 | 远距离回溯 | 隔轮追问 | 正确回忆 |
| 13 | 远距离回溯 | 交叉引用 | 无幻觉 |
| 14 | 故意混淆 | 给出错误信息 | 识别矛盾 |
| 15 | 故意混淆 | 坚持错误 | 不被欺骗 |
| 16 | 工具触发 | 要求创建文件 | 安全确认事件 |
| 17 | 工具触发 | 要求搜索网页 | tool_call 事件完整 |
| 18 | 多轮上下文 | 复杂指令 | 迭代/thinking 事件 |
| 19 | 综合指令 | "总结我们的对话" | 完整准确 |
| 20 | 压力测试 | 长文本输入 | 不超时不报错 |

### 6.2 每轮验证项

每轮 SSE 流需验证:
- `type` 字段值为合法的 `StreamEventType` 枚举值
- 流以 `done` 事件正常结束
- 无连续解析失败
- 回复内容与问题相关
- 工具调用合理性

### 6.3 /clear 端点集成测试 (轮次 10)

在轮次 10 中:
1. 先调 `POST /api/chat/clear` 清除会话
2. 再发一条消息问 "刚才我说了什么"
3. 验证 Agent 回复表示无历史记忆

---

## 阶段 7: 日志审计与最终报告

> Agent 自动执行

### 7.1 LLM Debug 日志审计

**检查位置**: `data/llm_debug/llm_request_*.json`

| 检查项 | 预期 |
|--------|------|
| 会话元数据 | session_id, 通道, 消息数正确 |
| 动态模型名 | `powered by {实际模型名}`, 非占位符 |
| 对话上下文约定 | 完整存在 |
| 记忆优先级 | 三级: 对话历史 > 系统注入记忆 > 记忆搜索工具 |
| 无"仅供参考" | 全文搜索无匹配 |
| 时间戳注入 | 历史消息带 `[HH:MM]` 前缀 |
| [最新消息]标记 | 最后一条 user 消息有标记 |
| 无双重时间戳 | 正则 `\[\d{2}:\d{2}\]\s*\[\d{2}:\d{2}\]` 无匹配 |
| 工具定义 | `get_session_context`, `delegate_to_agent` 存在 |

### 7.2 报告模板

```markdown
## 对话表现
- 总轮次: N
- 上下文保持: [是否有遗忘/混淆]
- 工具使用合理性: [是否有不必要的工具调用]
- 纠正响应: [信息更新后是否正确反映]
- /clear 同步: [前后端同步是否生效]

## System Prompt 审计
- [ ] 会话元数据
- [ ] 动态模型名
- [ ] 对话上下文约定
- [ ] 记忆优先级
- [ ] 无"仅供参考"

## Messages 结构审计
- [ ] 时间戳注入
- [ ] [最新消息]标记
- [ ] 无双重时间戳

## 前端架构验证
- [ ] ChatView 拆分完成 (hooks/components/utils)
- [ ] useReducer 消息状态
- [ ] QueryGuard 并发保护
- [ ] 虚拟滚动
- [ ] ErrorBoundary
- [ ] 代码分割

## 发现的问题
| # | 问题 | 严重度 | 位置 | 建议修复 |
|---|------|--------|------|----------|
| 1 | ... | P0/P1/P2 | ... | ... |
```

---

## 阶段 3 补充: Pixel Office 交互增强 (Batch 11)

> 需要人工操作 Pixel Office 视图

### PO.1 Agent 点击详情

**人工操作**:

1. 切换到 Pixel Office 视图
2. 左键点击一个 Agent 角色
   - [ ] 是否弹出详情面板
   - [ ] 面板包含: 名称、ID、部门、状态
   - [ ] 是否有"分配任务"和"聚焦"按钮

### PO.2 Agent 右键菜单

1. 右键点击一个 Agent 角色
   - [ ] 是否弹出上下文菜单 (非浏览器默认菜单)
   - [ ] 菜单包含: 分配任务、聚焦、查看详情

### PO.3 Agent Tooltip

1. 鼠标悬停在 Agent 角色上
   - [ ] 是否显示 tooltip (名称、状态、当前任务)
   - [ ] 移开鼠标后 tooltip 是否消失

---

## 阶段 3 补充: Org 协调可视化 (Batch 11)

> 需要 Org 模式和多 Agent 环境

### ORG.1 Org 流程面板

**人工操作** (需要 Org 模式可用):

1. 在聊天中触发 Org 命令 (如 `@org:组织名 执行某任务`)
2. 观察聊天区域上方:
   - [ ] 是否浮出实时状态面板
   - [ ] 面板是否可折叠
   - [ ] 节点是否根据状态着色 (灰/绿/蓝/红)
   - [ ] 任务委托是否显示箭头连线
3. 执行完毕后:
   - [ ] 面板是否自动收起

---

## 附录: 人工操作检查清单汇总

供测试时打印/对照使用:

**CLI 测试 (阶段 2)**:
- [ ] 2.1 流式输出正常
- [ ] 2.2 输入历史/补全/多行/Ctrl+C
- [ ] 2.3 斜杠命令 /help /status /clear /model
- [ ] 2.4 安全确认 y/n 流程
- [ ] 2.5 --quick 快速配置模式
- [ ] 2.6 向导返回导航

**Desktop 测试 (阶段 3)**:
- [ ] 3.1 错误卡片 (非 Markdown)
- [ ] 3.2 骨架屏动画
- [ ] 3.3 Ctrl+F 搜索
- [ ] 3.4 Ctrl+/ 快捷键面板
- [ ] 3.5 斜杠命令 + /clear 同步
- [ ] 3.6 消息编辑/重新生成
- [ ] 3.7 对话回卷
- [ ] 3.8 语音输入
- [ ] 3.9 附件上传 + 拖拽
- [ ] 3.10 右键菜单键盘导航
- [ ] 3.11 Lightbox 缩放/拖拽
- [ ] 3.12 空状态引导卡片
- [ ] 3.13 上下文用量指示器
- [ ] 3.14 安全确认弹窗 120s
- [ ] 3.15 消息操作菜单
- [ ] 3.16 @ Agent 联想
- [ ] 3.17 Ctrl+Z Undo
- [ ] 3.18 大文本粘贴折叠
- [ ] 3.19 Spinner 轮播提示
- [ ] 3.21 对话导出
- [x] 3.25 Ctrl+Shift+A 全局快捷键 (已移除, 作废)
- [ ] 3.26 系统托盘
- [ ] 3.27 设置重启提示
- [ ] 3.28 /memory /skills 命令
- [ ] 3.30 工具 loading 卡片

**Pixel Office (补充)**:
- [ ] PO.1 Agent 点击详情
- [ ] PO.2 Agent 右键菜单
- [ ] PO.3 Agent Tooltip

**Org 模式 (补充)**:
- [ ] ORG.1 Org 流程面板
