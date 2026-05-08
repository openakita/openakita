# SKILL: 融媒智策 Media Strategy

Use this skill when the user asks to track media hotspots, RSS news,
policy/current-affairs signals, Taiwan Strait coverage, verification
packs, editorial plans, interview plans, shooting plans, or viral-topic
remix plans from the `media-strategy` plugin.

## 1. 触发场景

- “拉一下今天台海热点” → `media_strategy_hot_radar`，必要时先 `media_strategy_ingest`。
- “我只想订阅时政/经济” → `media_strategy_subscribe_package`。
- “添加这个 RSS 源” → `media_strategy_add_feed`，必须让工具做 URL 安全校验。
- “这条新闻靠谱吗” → `media_strategy_verify_pack`。
- “把第 3 条做成采访/拍摄/短视频计划” → `media_strategy_replicate_plan`。
- “生成早报/午报/晚报” → `media_strategy_daily_brief`。

RSS 是网站公开订阅格式，适合稳定获取标题、摘要、发布时间和原文链接；它不是事实核验系统。回复时必须提醒用户打开来源链接复核。

## 2. 工具使用顺序

### A. 拉热点

1. 调 `media_strategy_hot_radar`，参数如：

```json
{"package_id": "taiwan", "since_hours": 24, "limit": 20}
```

2. 如果返回条目很少，先调：

```json
{"package_ids": ["taiwan"], "limit_sources": 0}
```

对应工具是 `media_strategy_ingest`，然后再次拉雷达。

3. 回复时列出标题、来源、分数、风险等级和链接，不要替用户断言真实性。

### B. 复核热点

调用：

```json
{"article_ids": ["ms-a-..."], "topic": "用户关心的主题"}
```

对应工具是 `media_strategy_verify_pack`。输出要突出“已有来源”“缺什么证据”“下一步查什么”。

### C. 策研采编

调用：

```json
{
  "article_ids": ["ms-a-..."],
  "topic": "热点主题",
  "target_format": "short_video",
  "tone": "稳健客观"
}
```

对应工具是 `media_strategy_replicate_plan`。复刻只指复用选题角度、叙事结构、采访和拍摄方法，不能要求照搬标题、文案或视频内容。

## 3. 套餐 ID

- `policy`：时政政策
- `taiwan`：台海观察
- `economy`：经济财经
- `world`：国际局势
- `tech`：科技产业
- `platform`：平台热点

用户说“只看台海”就启用 `taiwan`，必要时关闭其它套餐；用户说“时政和经济都要”就分别启用 `policy` 和 `economy`。

## 4. 回复规范

- 每条推荐必须带来源链接或 article_id。
- 对 `risk_level=high` 的内容，明确说“仅作线索，需复核”。
- 对台海、政策、冲突、金融市场内容，避免煽动性或未证实表达。
- 工具返回 `ok=false` 时，原样解释 `error` 和 `hint`，不要编造原因。
- 如果没有新闻，建议扩大 `since_hours` 或先运行 `media_strategy_ingest`。

## 5. IM 通道注意

- 不自动推送到群聊；只有用户明确给出 channel/chat_id 或当前会话已绑定推送目标时，才可以建议推送。
- 面向 IM 的摘要保持短：先 3 条最重要热点，再给“复核/采编下一步”。
- 需要深入计划时，先让用户选择一个 article_id 或热点序号，再调用复刻计划工具。
