---
name: media-strategy
description: Track RSS/news hotspots, verify source packs, and produce editorial plans through the Media Strategy plugin.
risk_class: readonly_search
---

# SKILL: 融媒智策 Media Strategy

Use this skill when the user asks to track media hotspots, RSS news,
policy/current-affairs signals, Taiwan Strait coverage, verification
packs, editorial plans, interview plans, shooting plans, or viral-topic
remix plans from the `media-strategy` plugin.

## 1. 触发场景

- “给我推几条今天值得做的选题” / “Top 5 热点” → `media_strategy_top_topics`（默认输出 5 条，按「多源覆盖 + 权威加权」排序）。
- “拉一下今天台海热点全量列表” → `media_strategy_hot_radar`，必要时先 `media_strategy_ingest`。
- “让 AI 分析一下今天最值得做的热点” → `media_strategy_ai_analyze_topics`（先规则筛选 Top N 热点簇，再批量调用主程序大模型）。
- “我只想订阅时政/经济” → `media_strategy_subscribe_package`。
- “添加这个 RSS 源” → `media_strategy_add_feed`，必须让工具做 URL 安全校验。
- “这条新闻靠谱吗” → `media_strategy_verify_pack`。
- “把第 3 条做成采访/拍摄/短视频计划” → `media_strategy_replicate_plan`。
- “生成早报/午报/晚报” → `media_strategy_daily_brief`。

RSS 是网站公开订阅格式，适合稳定获取标题、摘要、发布时间和原文链接；它不是事实核验系统。回复时必须提醒用户打开来源链接复核。

## 2. 工具使用顺序

### A. 选题推荐（默认入口）

1. 调 `media_strategy_top_topics`，参数如：

```json
{"package_id": "taiwan", "since_hours": 24, "limit": 5, "min_coverage": 1, "compact": true}
```

2. 该工具按「**多家媒体同时报道 + 媒体权威性**」加权排序，输出 Top 5（用户可自定义 1–20）。
   返回字段是精简的 `title / url / sources / sources_count / weighted_score / risk_level`，
   **仅返回标题与原文链接**，避免把全文/摘要塞回上下文导致 Token 浪费。

3. 用户想看更多就把 `limit` 调到 10；想严格只看「至少 2 家媒体同时报道」的选题就把 `min_coverage` 调到 2。

4. 回复时只罗列标题 + 原文链接（再加风险等级和报道家数），引导用户点开链接读原文，**不要让大模型把每条新闻摘要重写一遍**——这是图2 中明确要省下的 Token 和成本。

5. 如果返回 `total_clusters=0` 或 `total_candidates=0`，先调 `media_strategy_ingest` 再来一次。

### B. 拉热点全量榜（编辑/排版场景）

1. 调 `media_strategy_hot_radar`，参数如：

```json
{"package_id": "taiwan", "since_hours": 24, "limit": 20, "compact": true}
```

不需要聚合时把 `cluster` 留为 false；想直接复用聚合排序就把 `cluster` 设为 true，行为和 `media_strategy_top_topics` 一致。

2. 如果返回条目很少，先调：

```json
{"package_ids": ["taiwan"], "limit_sources": 0}
```

对应工具是 `media_strategy_ingest`，然后再次拉雷达。

3. 回复时列出标题、来源、分数、风险等级和链接，不要替用户断言真实性。

### C. AI 选题分析（成本受控）

调用：

```json
{"package_id": "taiwan", "since_hours": 24, "limit": 10, "min_coverage": 1, "evidence_limit": 5}
```

对应工具是 `media_strategy_ai_analyze_topics`。它不是逐条新闻调用大模型，而是先按“多源覆盖 + 权威加权”筛出 Top N 热点簇，再把每簇 3-5 条证据打包给主程序大模型分析。输出要关注“为什么值得做、证据够不够、风险在哪里、下一步采编动作是什么”。

如果用户担心成本，建议把 `limit` 设为 5-10；如果想更严格，只看多源交叉印证，建议把 `min_coverage` 设为 2。

### D. 复核热点

调用：

```json
{"article_ids": ["ms-a-..."], "topic": "用户关心的主题"}
```

对应工具是 `media_strategy_verify_pack`。输出要突出“已有来源”“缺什么证据”“下一步查什么”。

### E. 策研采编

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

内置源已覆盖：

- 央视国内/国际/财经/港澳台、新闻联播
- 新华网（时政/国际/台湾）、人民网（时政/国际/台湾）、中国新闻网台湾、中国日报
- 澎湃、第一财经、财新、华尔街见闻、36 氪、虎嗅、机器之心、量子位、IT 之家、观察者网、环球时报评论
- BBC 中文/World、德国之声中文、法广中文、美国之音中文、联合早报、Taiwan Info、The Diplomat、Reuters
- 联合新闻网兩岸、中时新闻网、ETToday 中國大陸、今日新闻网政治（默认关闭，用户按需开启）
- RSSHub 微博热搜/知乎热榜/抖音/B 站等平台热榜

用户需要确认源状态时先调用 `media_strategy_list_sources`，不要凭记忆猜测某个源是否启用。

## 4. 没 RSS 怎么接

部分台海主流网站没有提供公开 RSS，本插件用以下顺序兜底：

| 信源 | 接入方式 | 说明 |
|---|---|---|
| 中国台湾网 (taiwan.cn) | 内置 HTML 抓取源 `taiwancn-jsbg` | 直接解析 `https://www.taiwan.cn/jsbg/` 即时报道列表 |
| 东南网台海频道 | 内置 HTML 抓取源 `fjsen-taihai` | 解析 `https://taihai.fjsen.com/` 首页 |
| 台海网 | 内置 HTML 抓取源 `taihainet-twxw` | 解析 `https://www.taihainet.com/news/twxw/` 列表 |
| 凤凰网台湾频道 | 用户自建 RSSHub + `media_strategy_add_feed` | 凤凰用 React 动态渲染，需要 `/ifeng/c/<topicId>` 路由，topic ID 自查后填入 |
| 华人头条 / 银河新闻记者 | 用户自建 RSSHub + `media_strategy_add_feed` | 一般是微信公众号体裁，路由 `/wechat/officialaccount/<id>`，需 Sogou 反爬可达 |

HTML 抓取的提示：
- 选择器解析不到内容时会自动回退到「全锚点启发式」扫描，不需要时刻紧盯各家站点改版。
- 用户反馈某 HTML 源拉空时，先 `media_strategy_ingest` 看错误日志，再考虑通过 `update_source` 改 `selectors.item` 的 CSS 选择器（例如换成 `.list_news a`、`ul.l01 li a` 等更精准的锚定）。
- 网站重定向到 https 或换域名要让用户在 UI 里 PATCH `url`，不要让 Agent 越权改源。

用户问「能不能把凤凰资讯接进来」时，回复模板：
> 凤凰网近年没有公开 RSS，建议你本机跑一份 RSSHub（`docker run -d -p 1200:1200 diygod/rsshub`），然后用 `media_strategy_add_feed` 把 `http://<内网可达地址>:1200/ifeng/c/<topicId>` 加进来。本插件的 URL 安全校验会拒绝 `localhost`，请部署在内网或局域网另一台机器。

## 5. 回复规范

- 每条推荐必须带来源链接或 article_id。
- 对 `risk_level=high` 的内容，明确说“仅作线索，需复核”。
- 对台海、政策、冲突、金融市场内容，避免煽动性或未证实表达。
- 工具返回 `ok=false` 时，原样解释 `error` 和 `hint`，不要编造原因。
- 如果没有新闻，建议扩大 `since_hours` 或先运行 `media_strategy_ingest`。
- **Token 节流**：选题推荐场景下不要让大模型把工具返回的标题/摘要重写或扩写成解读稿，输出形式保持「序号 + 标题 + 原文链接 + 报道家数 + 风险等级」，由用户点击链接自行浏览原文，避免二次生成成本。需要更深加工时，让用户先选 article_id 再调用 `media_strategy_verify_pack` 或 `media_strategy_replicate_plan`。

## 6. IM 通道注意

- 不自动推送到群聊；只有用户明确给出 channel/chat_id 或当前会话已绑定推送目标时，才可以建议推送。
- 面向 IM 的摘要保持短：先 3 条最重要热点，再给“复核/采编下一步”。
- 需要深入计划时，先让用户选择一个 article_id 或热点序号，再调用复刻计划工具。
