我已经仔细阅读了 mockup 和全部相关代码。以下是我发现的 6 个核心差距：

  ---
  Gap 1: TaskProgressCard 不可见 (严重)

  Mockup: 一开始执行就显示进度条 ● 1.市场调研 ── ● 2.数据分析 ── ● 3.报告生成
  实际: 每次点击"开始执行"/"进入下一步"时，addUserMessage() 创建全新 currentReply（bpProgress: null），TaskProgressCard 要等到 bp_progress 事件到达才显示。而 bp_progress
  只在子任务完成后才 yield。
  原因: engine.advance() 没有在开始执行时 yield 初始 bp_progress；且新 reply 丢失了上一个 reply 的 BP 状态。

  Gap 2: Reply 碎片化 (严重)

  Mockup: 整个 BP 执行是一个连续的对话流
  实际: 每次"进入下一步"都创建新的 user→assistant 消息对，导致 BP 执行被切成多个独立的 reply 块，每个块的 stepCards/bpProgress 都是独立的。

  Gap 3: BP 触发决策 UI 不一致

  Mockup: 自定义灯泡图标 + BP名称 + 子任务链 + "自由模式/最佳实践模式"按钮
  实际: 两条路径产生不同 UI —— seecrab.py 的关键词匹配发 generic ask_user 事件，而 LLM 调用 bp_start 发 bp_instance_created 事件。

  Gap 4: 右侧面板缺失

  Mockup: 有两种右侧面板模式 —— Step Detail（显示单个工具调用的输入/输出/子调用）和 SubtaskOutputPanel（可编辑的子任务输出）
  实际: uiStore.openSubtaskOutput() 被调用了，但右侧面板组件似乎没有完整实现。

  Gap 5: 委派卡片层级缺失

  Mockup: 委派 research-agent 卡片内嵌套显示子步骤（搜索、浏览、分析），有缩进和不同背景色
  实际: StepCardList 平铺渲染所有 step_card，没有区分委派卡片和子步骤卡片的层级关系。

  Gap 6: 实时计时器缺失

  Mockup: 每个步骤卡片右侧有实时计时器 285.3s
  实际: duration 只在步骤完成时由后端设置，执行中无客户端侧计时。

  ---

⏺ User answered Claude's questions:
  ⎿  · 在以上 6 个差距中，你认为哪些是当前最需要优先解决的？ → 全部6个

⏺ 好的，全部6个都做。让我提出具体的方案。

⏺ User answered Claude's questions:
  ⎿  · 对于 Reply 碎片化问题 (Gap 2)，你倾向哪种方案？ → 多 reply + 状态继承

⏺ User answered Claude's questions:
  ⎿  · Gap 3: BP 触发流程统一方案。你说 MasterAgent 只做意图识别，那关键词匹配这一层应该怎么处理？ → 统一为 bp_offer 事件 (推荐)

⏺ User answered Claude's questions:
  ⎿  · Gap 4: 右侧面板实现程度？ → 完整实现 (推荐)

⏺ 好的。所有决策都明确了，让我整理完整设计方案。

⏺ 方案设计

  基于 6 个差距和你的选择，以下是完整方案：

  ---
  架构决策

  ┌────────────┬──────────────────────┐
  │    决策    │         方案         │
  ├────────────┼──────────────────────┤
  │ Reply 模型 │ 多 reply + 状态继承  │
  ├────────────┼──────────────────────┤
  │ BP 触发    │ 统一为 bp_offer 事件 │
  ├────────────┼──────────────────────┤
  │ 右侧面板   │ 完整实现两种模式     │
  ├────────────┼──────────────────────┤
  │ 委派卡片   │ 层级嵌套显示         │
  ├────────────┼──────────────────────┤
  │ 实时计时   │ 客户端侧计时         │
  └────────────┴──────────────────────┘

  ---
  Gap 1+2: TaskProgressCard 可见性 + Reply 状态继承

  后端改动:
  - engine.advance() 在执行第一个子任务之前 yield 一个 bp_progress 事件（初始状态）
  - 确保每轮 /api/bp/next 调用开始时就发送 progress

  前端改动:
  - handleBpStart()/handleContinue() 中，addUserMessage() 之后，立即从 bpStore.activeInstance 设置新 reply 的 bpProgress
  - BotReply.vue 中 TaskProgressCard 的数据源改为响应式计算：优先用 reply.bpProgress，fallback 到 bpStore.activeInstance

  Gap 3: BP 触发统一

  后端改动:
  - seecrab.py 中将 ask_user 事件类型改为 bp_offer
  - 携带完整的 BP 信息：bp_id, bp_name, subtasks[], default_run_mode

  前端改动:
  - 新增 BPOfferBlock.vue 组件（mockup 中的灯泡 + 决策按钮样式）
  - dispatchEvent 处理 bp_offer 事件 → 设置 reply.bpOffer
  - 用户点击"最佳实践模式" → 调用 /api/bp/start
  - 用户点击"自由模式" → 调用 /api/seecrab/chat 正常对话

  Gap 4: 右侧面板

  新增组件:
  - SubtaskOutputPanel.vue — 按 output_schema 渲染可编辑字段（text, textarea, array with +/-）
    - 顶部：BP 名称 + 子任务进度标签
    - 中间：字段编辑区
    - 底部：确认更新按钮 → 调用 PUT /api/bp/edit-output
  - StepDetailPanel.vue — 单个工具调用的详情
    - 状态 badge + 耗时 + token 统计
    - 输入 JSON viewer
    - 输出 text/JSON viewer
    - 子调用列表

  uiStore 改动:
  - 新增 rightPanel 状态：{ mode: 'none' | 'step_detail' | 'subtask_output', data: {...} }
  - openSubtaskOutput(instanceId, subtaskId) — 打开输出面板
  - openStepDetail(stepId) — 打开步骤详情

  Gap 5: 委派卡片层级

  后端改动:
  - engine._run_subtask_stream() 在开始委派时 yield 一个 step_card（card_type: "delegate"）
  - SubAgent 的 step_card 事件自动带 parent_step_id 字段（engine 注入）

  前端改动:
  - StepCardList.vue 识别 card_type === 'delegate' 的卡片作为父级
  - 后续 step_card 如果有 parent_step_id 则缩进显示
  - 委派卡片用不同背景色和图标

  Gap 6: 实时计时器

  前端改动:
  - StepCard 组件中，当 status === 'running' 时启动客户端 requestAnimationFrame 计时器
  - 记录 startTime，每帧更新显示的耗时
  - 当 status 变为 completed 时停止，使用后端返回的精确 duration

  ---
  这个方案你觉得合理吗？我可以根据你的反馈调整后开始写设计文档。