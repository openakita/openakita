---
name: fin-pulse
description: Track finance headlines, daily market radar, and source-backed financial news through Fin Pulse.
risk_class: readonly_search
---

# fin-pulse — finance news radar for the OpenAkita Brain

Use this skill whenever the user asks about **finance headlines, daily
briefs, hot-topic keyword alerts, or scheduled IM pushes** from the
fin-pulse plugin. The plugin exposes seven agent tools that let you
create ingest / daily_brief / hot_radar tasks, inspect their status,
paginate tasks, cancel a running job, read / write the plugin config,
and search the article index by keyword / source / `days` window.

---

## 1. Triggers

Call fin-pulse tools when the user says (or implies):

- 「帮我看下今天美股 / 美联储 / 财经的大事」 → `fin_pulse_search_news`
- 「生成今天的早报 / 午报 / 晚报」→ `fin_pulse_create(mode="daily_brief", session=...)`
- 「把早报 / 雷达告警推到飞书 / 企微」→ `fin_pulse_create(mode="hot_radar", ...)`
  + `targets=[{"channel":"feishu","chat_id":"..."}]`
- 「关键词 X 有什么新闻」→ `fin_pulse_search_news(q="X")`
- 「抓一下 fin-pulse 的 8 个源」→ `fin_pulse_create(mode="ingest")`
- 「查一下任务 Y 跑完了吗」→ `fin_pulse_status(task_id="Y")`
- 「fin-pulse 最近都跑了哪些」→ `fin_pulse_list`
- 「把 radar_rules 改成 +美联储 +降息」→ `fin_pulse_settings_set`
- 「fin-pulse 当前是怎么配的」→ `fin_pulse_settings_get`

Do **not** call these tools when the user only wants general finance
commentary; prefer web search for commentary and fin-pulse only for
*their* plugin state.

---

## 2. Tool reference

### `fin_pulse_create`

Creates + runs a task synchronously (returns when done). `mode` is
required and must be one of `ingest` / `daily_brief` / `hot_radar`.
Pass extra params either nested under `params` or flat — both work.

Canonical shapes:

```json
{"mode": "ingest", "since_hours": 24}
{"mode": "daily_brief", "session": "morning", "since_hours": 12, "top_k": 20, "lang": "zh"}
{"mode": "hot_radar", "rules_text": "+美联储\n+降息\n!传闻",
 "targets": [{"channel": "feishu", "chat_id": "oc_xxx"}],
 "since_hours": 24, "cooldown_s": 600}
```

- `since_hours`: int in `[1, 72]` for ingest / daily_brief,
  `[1, 168]` for hot_radar — out of range values clamp silently.
- `top_k`: int in `[1, 60]`, defaults to 20.
- `session`: `morning` / `noon` / `evening`. Reject anything else
  instead of guessing.
- `min_score`: float in `[0, 10]`. Optional.

The returned envelope always has `ok: bool` and either a typed
payload (`task_id`, `digest`, `result`) or an `error` / `hint`.
Never retry on `invalid_mode` / `missing_rules` — surface to the user.

### `fin_pulse_status(task_id)` / `fin_pulse_cancel(task_id)`

- `status` returns the full task row (params + progress +
  `error_kind` if failed).
- `cancel` is idempotent and safe to call twice.

### `fin_pulse_list`

`limit` clamps to `[1, 200]` default 50. Filter by `mode` / `status`
as needed. Use it to *confirm* state before suggesting a fresh
ingest.

### `fin_pulse_settings_get` / `fin_pulse_settings_set`

`get` returns a **redacted** map — any key containing `api_key`,
`token`, `webhook`, `secret`, or `password` is masked as `***`.
Never surface the `***` value to the user as real data.

`set` takes `{"updates": {k: v}}` where values are stringified on
store. Don't write large JSON blobs through this tool.

### `fin_pulse_search_news`

The daily-driver search tool:

```json
{"q": "美联储", "days": 1, "min_score": 6, "limit": 20}
{"source_id": "wallstreetcn", "days": 7, "sort": "score"}
```

- `q` supports plain LIKE matching today; the `+must` / `!exclude`
  DSL is **evaluated by hot_radar, not by this search** — don't
  promise that syntax here.
- `days` clamps to `[1, 90]`.
- Returns `items` sorted by time (default) or AI score
  (`sort=score`). Each item has `title`, `url`, `summary`,
  `source_id`, `ai_score`, `fetched_at`, `published_at`.

---

## 3. Response etiquette

- When a tool returns `ok=false`, quote the `error` kind plus any
  `hint` — don't invent explanations.
- When the tool returns no items, say so explicitly and suggest
  the user either run `fin_pulse_create(mode="ingest")` first or
  widen the `days` window.
- Daily briefs come back with `digest.stats` (`total_selected`,
  `source_breakdown`). Surface the count so the user knows whether
  the digest is meaningful.
- Every task_id follows `fp-<uuid>`; include it in your reply so
  the user can reference it later.

---

## 4. Cross-tool recipes

### Recipe A — "What's moving the Fed today?"

1. `fin_pulse_search_news(q="美联储", days=1, min_score=5, limit=10)`.
2. If `items.length < 3`, follow up with
   `fin_pulse_create(mode="ingest", since_hours=6)` and retry.
3. Summarize the top 5 in bullet points with source_id tags.

### Recipe B — "Generate + push the morning brief to Feishu"

1. `fin_pulse_create(mode="daily_brief", session="morning",
   since_hours=12, top_k=20, lang="zh")` → get `task_id` + `digest`.
2. Quote the resulting `digest.title` and article count so the user
   can confirm.
3. If they want it on IM, say: "你可以在插件 Digests Tab 点**Resend**
   选飞书 / 企微，或把 `channel` + `chat_id` 告诉我我再帮你补
   发 hot_radar 任务推一次。" (Do **not** try to call
   `dispatch/send` from the tool surface — the REST surface is the
   right entry point and requires an explicit `chat_id`.)

### Recipe C — "Turn on a radar rule"

1. `fin_pulse_settings_set({"updates": {"radar_rules":
   "+美联储\n+降息\n!传闻"}})`.
2. Remind the user that radar *runs* only via a scheduled task
   (`POST /schedules`) or an explicit
   `fin_pulse_create(mode="hot_radar", ...)` with targets.

---

## 5. Safety rails

- Never send `dispatch/send` or `hot_radar/run` without an explicit
  `chat_id` from the user — IM pushes are user-visible and spamming
  them is a support incident.
- If `fin_pulse_settings_get` reveals `newsnow.mode=off`, don't
  pretend NewsNow data is present.
- Respect the redaction — if a user asks "what's my Feishu webhook",
  reply that it's redacted at the tool surface and point them to
  the plugin Settings tab.
