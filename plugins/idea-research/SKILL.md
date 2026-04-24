# SKILL: idea-research 选题研析室

> 给后续 AI agent 提供修改 skill — 10 节，每节是一个独立"为什么/怎么改"小问答。
> 阅读顺序建议：1 → 2 → 3（实操类）→ 4-9（按需查）→ 10（兜底）。

---

## 1 插件定位与适用场景

**当用户说什么时该想到 idea-research？**

| 用户原话 | 触发的 mode | 备注 |
|---|---|---|
| 「拉一下今天 B站 / 抖音的 AI 热门视频」 | `radar_pull` | 默认引擎 A 即可 |
| 「帮我看看 https://... 这条爆款是怎么火的」 | `breakdown_url` | 8 步 pipeline |
| 「给我对比 [@张三, @李四, @王五] 三个号最近的内容」 | `compare_accounts` | 跨账号 hook 分布 |
| 「把这条选题改成我的小红书脚本」 | `script_remix` | 注入 persona + MDRM |
| 「帮我每天早上 9 点推 5 条选题」 | `idea_subscribe` 工具 + IM 通道 handoff | 见 §6 |

**不适用的场景**（应改用其他插件）：

- 视频 ASR + 字幕生成 → `subtitle-craft`
- 视频剪辑 / 转码 / 拼接 → `clip-sense`
- 数字人配音 → `avatar-studio`
- 静态图片设计 → `tongyi-image` / `seedance-video`

---

## 2 4 mode 触发条件与示例 prompt

### `radar_pull`

```
{
  "platforms": ["bilibili", "youtube", "douyin"],
  "keywords": ["AI", "智能体"],
  "time_window": "24h",         // 24h / 7d / 30d
  "engine": "auto",             // a / b / auto
  "limit": 20,
  "mdrm_weighting": true        // 默认开
}
```

### `breakdown_url`

```
{
  "url": "https://www.bilibili.com/video/BV...",
  "persona": "B站知识博主",
  "enable_comments": true,
  "asr_backend": "auto",        // auto / local / cloud
  "frame_strategy": "hybrid",   // keyframe / fixed_1.5s / hybrid
  "write_to_mdrm": true
}
```

### `compare_accounts`

```
{
  "account_urls": ["https://space.bilibili.com/12345", "..."],
  "window": "30d",              // 7d / 30d / 90d
  "max_videos_per_account": 20
}
```

### `script_remix`

```
{
  "trend_item_id": "<id>",
  "my_persona": "小红书运营专家",
  "my_brand_keywords": ["国货", "护肤"],
  "target_duration_seconds": 60,
  "num_variants": 3,
  "target_platform": "xhs",     // douyin / xhs / bilibili / youtube / kuaishou / weibo
  "use_mdrm_hints": true
}
```

---

## 3 双引擎选择决策树

用户问「该用 A 还是 B」时按这棵树回答：

```
target 平台是 B站 / YouTube？
  ├─ 是 → 引擎 A（永远首选，零风险）
  └─ 否（抖音 / 小红书 / 快手 / 微博 / B站登录态）→
      用户能容忍数据质量差？
        ├─ 能 → 引擎 A 走 RSS Hub（无互动数据）
        └─ 不能 → 用户已配 cookies 并勾免责？
            ├─ 是 → 引擎 B
            └─ 否 → 提示「请先去 Settings → 数据源 配置 cookies」
```

**默认值**：`engine: "auto"` → 自动按上表决策。

---

## 4 12 PERSONAS 选择指南

| 想让选题/脚本风格偏向哪种？ | 选哪个 persona |
|---|---|
| 25-35 女性 / 笔记体 / 情绪共鸣 | 1 小红书运营专家 / 5 视频号情感博主 |
| 强钩子 / 短平快 | 2 抖音爆款编导 |
| 深度 / 长视频 / 知识浓度 | 3 B站知识博主 / 6 知识付费课程主理人 |
| 海外英文 / SEO | 4 YouTube SEO 专家 |
| 卖货 / 销售话术 | 7 电商带货主播 |
| 育儿 / 家庭场景 | 8 母婴亲子博主 |
| 美妆护肤 / 成分党 | 9 美妆护肤测评师 |
| 数码评测 / 横评 | 10 数码科技博主 |
| 探店打卡 / 视觉化 | 11 美食探店博主 |
| 财经 / 数据分析 | 12 财经投资评论员 |

每个 persona 的完整 system_prompt 在 `idea_models.PERSONAS` 中，按统一模板填空（详见 plan §13.1.B）。

---

## 5 MDRM 记忆图谱使用建议

**让推荐越用越准的 3 个动作**：

1. **首次安装就授权**：到 Plugin Manager 把 brain.access / vector.access / memory.write 三权限全勾。
2. **breakdown 时勾 `write_to_mdrm`**（默认 true）：每完成 1 条拆解，hook 模式就被记住。
3. **script_remix 时勾 `use_mdrm_hints`**（默认 true）：自动注入历史 top 3 相似成功 hook 给 LLM。

**手动管理**：

- `POST /mdrm/clear` — 清空所有学习记录
- `POST /mdrm/reindex` — 从历史 done 任务重新提取 hook 写入 MDRM（用于刚授权后回填）
- `GET /mdrm/stats` — 看 caps + hook_count + missing_perms

---

## 6 跨插件 handoff 路径

```
idea-research.script_remix
  ├── handoff_payload.to == "clip-sense"      → 自动剪辑（用脚本 b_roll 提示）
  ├── handoff_payload.to == "avatar-studio"   → 数字人配音版
  └── handoff_payload.to == "channel"         → IM 早 9 点推送

idea-research.breakdown_url 完成
  └── api.broadcast_ui_event("idea.task.done", {...})
       └── MDRM 写入 → vector + memory_manager（其他插件可读）
```

`handoff_target` 字段在 `POST /tasks` body 中可指定，`finalize` step 调 SDK `assets_bus` 写入。

---

## 7 错误码 11 类排障

| 报错 | 大概率原因 | 排障第一步 |
|---|---|---|
| `network` | 代理 / 防火墙 / 平台 region block | `curl -I {url}` 看连通性 |
| `timeout` | 视频太长 / RSS Hub 慢 | 减小 limit 或换 RSS Hub 实例 |
| `auth` | API key 错 / cookies 过期 | Settings 重填 |
| `quota` | DashScope 余额没了 | 充值或换 key |
| `dependency` | yt-dlp / ffmpeg / playwright 缺 | 按 hint 给的命令装 |
| `cookies_expired` | 浏览器爬虫 cookies 失效 | Settings → 数据源 → 重新导入 |
| `crawler_blocked` | 引擎 B 被风控 | 换 cookies 或临时切回引擎 A |
| `format` | URL 解析不出 / LLM 返回非 JSON | 检查 URL；LLM 错走三层 fallback |

---

## 8 cookies 失效问题修复步骤

1. UI Settings → 数据源 → 找到失效平台的 cookies 卡片（红色 ✗）
2. 浏览器打开对应平台 → F12 / 装 Cookie-Editor 扩展
3. 复制全部 cookies 为 JSON
4. 粘贴到卡片 textarea → 勾「我已知晓风险」→ 点「保存 cookies」
5. 点「测试连通性」→ 出现绿色 ✓ 即修复成功
6. 如果反复失效 → 大概率帐号被风控了，换帐号

---

## 9 API Cheatsheet

```
# 创建任务（4 mode 通用）
POST /api/plugins/idea-research/tasks
  body: { mode, input, persona?, handoff_target? }

# 查任务列表（带过滤）
GET /api/plugins/idea-research/tasks?mode=breakdown_url&status=done&limit=50

# MDRM 状态
GET /api/plugins/idea-research/mdrm/stats

# 工具调用（SDK）
await ctx.call_tool("idea_radar_pull", {...})
await ctx.call_tool("idea_breakdown_url", {...})
await ctx.call_tool("idea_compare_accounts", {...})
await ctx.call_tool("idea_script_remix", {...})
```

完整 26 routes / 9 tools 清单见 `README.md` §6。

---

## 10 已知限制与路线图

### 已知限制

- 引擎 B 单次抓取上限：抖音 50 / 小红书 30 / 快手 30 / B站 100 / 微博 50
- ASR 单条视频上限：本地 Faster-Whisper ≤ 10min；> 10min 自动走 DashScope Paraformer 异步任务
- VLM 帧描述并发：受 DashScope 帐号 QPS 限制，默认 5 并发
- MDRM 检索硬超时：2s（超时不阻塞主流程，仅不加权）

### 1.x 路线图

- 1.0.0（首发）✓ — 4 mode + 26 routes + 9 tools + MDRM 双轨
- 1.1.0 — 引擎 B 第 6 平台（YouTube 登录态，访问 community posts）
- 1.2.0 — 推荐流"今日精选" — MDRM 反向推荐（基于历史成功 hook 主动找新选题）
- 1.3.0 — A/B 测试模式：同选题 2 版脚本并行投放，反馈数据回写 MDRM
- 1.4.0 — 内置 12 PERSONAS → 用户可自定义 100+
