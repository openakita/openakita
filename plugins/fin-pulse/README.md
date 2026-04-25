# fin-pulse · 财经脉动

> Finance news radar for OpenAkita. Three canonical modes —
> **daily_brief** (morning / noon / evening digest), **hot_radar**
> (keyword-triggered IM alerts), and **ask_news** (host Brain agent
> tools) — over eight first-party finance sources plus optional
> NewsNow aggregation.

| | |
|---|---|
| **Version** | 1.1.0 |
| **SDK range** | `>=0.7.0,<0.8.0` |
| **Plugin API** | `~2` / UI API `~1` |
| **Package contents** | Python runtime, tests, vendored `ui/dist`, icons |

---

## 1. Feature summary

| Mode | What it does | Entry points |
|------|--------------|--------------|
| `daily_brief` | Aggregate the last N hours, rank via AI score, render a markdown + HTML digest, push via host IM gateway. | `POST /digest/run`, `fin_pulse_create`, `on_schedule` hook |
| `hot_radar` | Match recent articles against keyword rules (`+must / !exclude / @alias`), fire IM dispatch with per-target cooldown. | `POST /hot_radar/run`, `POST /radar/evaluate`, `on_schedule` hook |
| `ask_news` | Seven agent tools registered on `register_tools` so the host Brain can query the article/digest index directly from chat. | `fin_pulse_*` tools |

### Data sources (V1.1)

Eight first-party fetchers plus optional **NewsNow** for social/aggregator
augmentation. All fetchers share `BaseFetcher` + `NormalizedItem` and
dedupe on a canonical URL hash; cross-source re-sightings are tracked via
`raw.also_seen_from`.

| Source id | Description | Transport |
|-----------|-------------|-----------|
| `wallstreetcn`    | 华尔街见闻 7x24 / latest   | **NewsNow 优先，直连回退** |
| `cls`             | 财联社电报                  | **NewsNow 优先，直连回退** |
| `eastmoney`       | 东方财富快讯                | **NewsNow 优先，直连回退** |
| `xueqiu`          | 雪球热帖 / 话题             | **NewsNow 优先，直连回退** |
| `pbc_omo`         | 中国人民银行货币政策         | Direct (RSS) |
| `nbs`             | 国家统计局                  | Direct (RSS) |
| `fed_fomc`        | 美联储 FOMC 日历 + 新闻    | Direct (RSS) |
| `sec_edgar`       | SEC EDGAR latest filings    | Direct (RSS) |
| `newsnow` *(opt)* | NewsNow 公共服务或自建 (非-CN 频道) | NewsNow only |

The 4 CN hot-list sources all default to calling the community-run NewsNow
aggregator first — `?id=wallstreetcn-hot` / `cls-hot` / `eastmoney` / `xueqiu-hotstock`.
When the aggregator is unreachable, returns an empty envelope, or the 300-second
public cooldown is in effect, each fetcher silently falls back to its legacy
direct scraper. Which path actually served the rows is surfaced to the Today tab
drawer as a `NewsNow / 直连 / 冷却 / 无结果` badge so you always know where the
data came from. Set `source.<id>.fallback_direct = "false"` to opt out of the
fallback for a specific source (advanced; primarily useful on hosts with weird
egress rules where the direct origin is blocked but NewsNow is not).

---

## 2. Architecture

```
┌─────────── plugin.py ───────────┐
│ on_load / on_unload              │
│  ├── FastAPI router (read+write) │
│  ├── register_tools (7 tools)    │
│  └── on_schedule hook            │
└─────────────────────────────────┘
          │
          ├── finpulse_task_manager (aiosqlite · 4 tables + assets_bus)
          ├── finpulse_fetchers     (8 sources + rss + newsnow)
          ├── finpulse_ai           (extract_tags · score_batch · dedupe)
          ├── finpulse_frequency    (+must / !exclude / @alias DSL)
          ├── finpulse_report       (markdown + HTML renderer)
          ├── finpulse_notification (line-boundary splitter)
          ├── finpulse_dispatch     (thin wrapper over api.send_message)
          ├── finpulse_services     (shared query service, see §6)
          └── finpulse_errors       (9 error_kind classifier)
```

Every LLM call goes through the host `api.get_brain()` — we do **not**
ship an IM SDK, we call `api.send_message(channel, chat_id, content)`.
Scheduled tasks run on the host `TaskScheduler`; the plugin only
registers a match predicate for `fin-pulse:` prefixed tasks.

---

## 3. Install & load

```bash
cd D:/OpenAkita/plugins/fin-pulse
# the 5-asset UI bundle ships vendored; no build step required
```

Restart the OpenAkita host and confirm in Plugin Manager that
`fin-pulse` is Active with permissions `tools.register`,
`routes.register`, `hooks.basic`, `data.own`, `channel.send`,
`brain.access`, `config.read`, `config.write` granted.

---

## 4. Configure

Open the plugin UI → **Settings** tab, in order:

1. **Channels** — one or more IM adapters must be registered in the
   host gateway (Feishu / WeCom / DingTalk / Telegram / OneBot / Email).
   If the list is empty, the top banner (`oa-config-banner`) will
   link you back here.
2. **NewsNow (enabled by default)** — ships pre-wired to the
   community-run public aggregator:
   - Step 1: Mode → `public` (default) / `self_host` / `off`.
   - Step 2: API URL → already filled with
     `https://newsnow.busiyi.world/api/s` in public mode (display-only,
     no edit needed); self-host mode lets you point at
     `http://127.0.0.1:4444/api/s`.
   - Step 3: Probe → clicks `POST /ingest/source/newsnow` and shows
     either a green pill on success or a cooldown countdown when the
     300-second floor hasn't yet elapsed.
   `newsnow.min_interval_s` is a hard floor enforced in
   `finpulse_pipeline.ingest()` — public mode can't go below 300s so
   the volunteer-run upstream never gets hammered.

   **Auto-promote:** any run that includes one of the 4 CN hot-list
   sources (`wallstreetcn` / `cls` / `eastmoney` / `xueqiu`) will lift
   `newsnow.mode` to `public` **in memory for that run only** when the
   persisted value is `off`. This gives the hybrid fetchers their
   preferred transport without forcing users to touch the wizard. The
   persisted preference is never overwritten — come back and set `off`
   whenever you want the run-once behaviour back.
3. **Schedules** — 4 template buttons (Morning / Noon / Evening brief +
   Hot Radar) open an in-page dialog that writes straight into the host
   Agent Scheduler. The row list below supports **Run now / Pause / Resume
   / Delete** without leaving the plugin — see §6 for the REST surface.
4. **AI Brain** — fin-pulse reuses the host LLM factory; configure
   provider / model / temperature in OpenAkita → Settings → Models.

---

## 5. Daily usage

- **Today tab** — live article feed with source / window / min-score
  filters, copy-to-clipboard on each item, and a **split ingest button**:
  click the primary half to run every enabled source, click the caret to
  run only the currently filtered source via `POST /ingest/source/{id}`.
  While ingest is running the button swaps to a rotating spinner and an
  indeterminate shimmer bar appears under the filter strip — no more
  "green toast with empty list". After the call resolves, an **inline
  result drawer** pops up right under the filters listing one pill per
  source:

  - colour-coded green / amber / red (rows added ↔ no-new ↔ error);
  - a `NewsNow / 直连 / 冷却 / 无结果` badge showing which transport
    actually served the rows (handy for diagnosing upstream drift);
  - an expandable `错误详情` row for failed sources that prints
    `error_kind` and the raw error message.

  A **smart toast** fires in parallel: green `新增 X · 更新 Y` when
  something landed, amber `X 源成功 · Y 源失败 · Z 源无结果` otherwise.
  The 8-second background poll is gone — the list auto-refreshes only
  after each ingest and when filters change, which both shaved CPU and
  surfaced the real bugs the poll used to hide.
  The source dropdown is hydrated from `GET /sources` (matches
  `finpulse_models.SOURCE_DEFS`), with a static fallback for the first
  paint.
- **Digests tab** — list of generated briefs; click for an iframe
  preview of the HTML blob, click **Resend** to fan out via the host
  gateway (channel dropdown is backed by the host `/api/scheduler/channels`).
- **Radar tab** — keyword rule editor + **Dry run** button; saves to
  `config["radar_rules"]` so a scheduled hot_radar can read them.
  The editor card stays compact (`.card--compact`, textarea capped at
  120–180px) so the hit-preview card always gets the rest of the
  vertical space.
- **Ask tab** — 7 agent tool cards with JSON samples and
  "copy natural-language prompt" buttons. Paste into the OpenAkita
  main chat window to invoke via Brain.
- **Settings tab** — 5 sections (Sources / IM channels / Schedules /
  NewsNow wizard / LLM note). The IM-channels card is the **only**
  place that shows the "no IM channel" warning banner — it no longer
  leaks onto every tab. A dedicated **Refresh** button next to the
  title re-pulls `GET /scheduler/channels` without a full tab reload,
  which is handy after you've added a new bot in the host. Schedules
  are created via 4 **template buttons** (Morning / Noon / Evening /
  Radar) that open a host-style dialog (task name / type / trigger /
  execution time / reminder preview / IM channel / enable toggle) and
  write directly into the host Agent Scheduler. The list below the
  buttons surfaces **Run now / Pause ↔ Resume / Delete** per row —
  the plugin never redirects you to the host SchedulerView panel.

---

## 6. API reference

> Base path: `/api/plugins/fin-pulse`

| Method | Path | Body / Query | Notes |
|--------|------|-------------|-------|
| `GET` | `/health` | — | Plugin status + db_ready + data_dir |
| `GET` | `/modes` | — | `MODES` enum (fallback inline) |
| `GET` | `/config` | — | Redacts `*_api_key`, `*_webhook`, `*_token`, `*_secret` |
| `PUT` | `/config` | `{updates: {k: v}}` | Flat string map |
| `GET` | `/tasks` | `?mode&status&offset&limit` | Clamped `limit<=200` |
| `GET` | `/tasks/{id}` | — | 404 when absent |
| `POST` | `/tasks/{id}/cancel` | — | Idempotent |
| `POST` | `/ingest` | `{sources?, since_hours?}` | Creates an `ingest` task. Returns `{ok, task_id, summary}` where `summary.by_source[id] = {fetched, inserted, updated, duration_ms, via, error?, error_kind?}` and `summary.totals` carries `{fetched, inserted, updated, failed_sources, sources_total, sources_ok}`. `via` is one of `"newsnow"` / `"direct"` / `"none"` and is what the Today-tab drawer renders as a transport pill. |
| `POST` | `/ingest/source/{source_id}` | — | Single-source probe. Same summary shape as `/ingest` (one entry under `by_source`). Wraps failures in an HTTP 500 + `error_kind` so the Today drawer can pin the red pill to the row. |
| `GET` | `/sources` | — | Serialises `finpulse_models.SOURCE_DEFS` for the Today-tab dropdown (id, display_zh, display_en, kind, default_enabled). |
| `GET` | `/articles` | `?q&source_id&since&min_score&sort&offset&limit` | |
| `GET` | `/articles/{id}` | — | Full raw_json |
| `POST` | `/digest/run` | `{session, since_hours?, top_k?, lang?}` | |
| `GET` | `/digests` | `?session&offset&limit` | Omits blobs |
| `GET` | `/digests/{id}` | — | Includes blobs |
| `GET` | `/digests/{id}/html` | — | `text/html` for iframing |
| `POST` | `/radar/evaluate` | `{rules_text, since_hours?, limit?, min_score?}` | Does not persist |
| `POST` | `/radar/ai-suggest` | `{description, lang?}` | LLM-assisted rules drafting (uses host Brain; has deterministic fallback) |
| `GET` / `POST` / `DELETE` | `/radar/library[/{name}]` | — | CRUD for saved rule presets (capped at `MAX_PRESETS`) |
| `POST` | `/hot_radar/run` | `{rules_text, targets[], since_hours?, ...}` | Persists + dispatches |
| `POST` | `/dispatch/send` | `{channel, chat_id, content, ...}` | Thin wrapper over `api.send_message` |
| `GET` | `/scheduler/channels` | — | Proxies the host `GET /api/scheduler/channels` so the plugin IM dropdown matches `SchedulerView` (rich `{channel_id, chat_id, chat_name, ...}` entries). |
| `GET` | `/available-channels` | — | Fallback adapter probe used when `/scheduler/channels` returns nothing. |
| `GET` | `/schedules` | — | Returns tasks whose name starts with `fin-pulse ` (new canonical space-delimited form) or `fin-pulse:` (legacy — still accepted). |
| `POST` | `/schedules` | `{mode, cron, channel, chat_id, name?, enabled?, ...}` | `mode=daily_brief|hot_radar`; auto-names `fin-pulse {suffix}` unless `name` is provided (must itself start with `fin-pulse `). `enabled` defaults to `true`. |
| `POST` | `/schedules/{id}/toggle` | — | Flip enabled/disabled. Ownership-checked — refuses foreign tasks. |
| `POST` | `/schedules/{id}/trigger` | — | Fire an ad-hoc run via the host `trigger_task`. Non-blocking. |
| `DELETE` | `/schedules/{id}` | — | Refuses any task whose name isn't a fin-pulse-owned prefix. |

### Agent tools (same envelope as REST, dispatched via Brain)

- `fin_pulse_create` — create + run an ingest/digest/radar task.
- `fin_pulse_status` — inspect a task by id.
- `fin_pulse_list` — paginate recent tasks (`limit` clamped to 200).
- `fin_pulse_cancel` — flip status to `canceled`.
- `fin_pulse_settings_get` / `fin_pulse_settings_set` — config CRUD.
- `fin_pulse_search_news` — keyword + source + `days` + `min_score`
  search over the articles index.

All integer args flow through a strict `_clamp(v, lo, hi, default)`
so a misbehaving Brain cannot ask for `limit=99999` and hit the DB.

### Cron examples

```json
{"mode": "daily_brief", "cron": "0 8 * * *",  "session": "morning",
 "channel": "feishu", "chat_id": "oc_xxx"}
{"mode": "daily_brief", "cron": "0 12 * * *", "session": "noon",
 "channel": "feishu", "chat_id": "oc_xxx"}
{"mode": "hot_radar",   "cron": "*/15 * * * *",
 "rules_text": "+美联储\n+降息\n!传闻",
 "channel": "feishu", "chat_id": "oc_xxx"}
```

### Scheduler integration

`POST /schedules` creates a task directly on the host's
`TaskScheduler` (not a plugin-private job-runner). That means:

- Rows also show up in the main OpenAkita **Scheduler** panel, but you
  don't have to leave fin-pulse to manage them — the Settings tab now
  exposes the same verbs inline: **Run now** (`POST
  /schedules/{id}/trigger`), **Pause ↔ Resume** (`POST
  /schedules/{id}/toggle`) and **Delete** (`DELETE /schedules/{id}`),
  each gated by an ownership check so foreign tasks are untouchable.
- Task names use the canonical `fin-pulse <suffix>` form (space
  separator) so they stay legal in host UIs that reject `:`. The
  legacy `fin-pulse:<suffix>` form is still recognised by the
  `on_schedule` hook so existing installs keep working after an
  upgrade.
- `silent=True` is always set on the host task because fin-pulse sends
  its own IM notification from the `on_schedule` handler; the host's
  scheduler should not double-post.

---

## 7. Smoke test checklist

Follow this end-to-end to validate a fresh install:

1. Load plugin → `GET /health` returns `ok=true`.
2. Settings → Channels lists at least one adapter pill.
3. `POST /ingest` → Today tab shows a mixed 8-source feed within
   30s; the `oa-config-banner` disappears if channels are present.
4. NewsNow → select `public` → click **Probe** → green message with
   `items_count`.
5. `POST /digest/run` (morning) → Digests tab shows the new card;
   click **Preview** → iframe renders the HTML blob.
6. Settings → Schedules → click the **每日早报** template → tweak
   cron to `0 9 * * *`, pick a Feishu channel, toggle **启用** on →
   **新建任务**. 9:00 arrives → Feishu receives the brief (splitter
   handles long text automatically). Back on the list: **运行** fires
   an ad-hoc copy now, **暂停/恢复** flips enabled, **删除** removes
   the row — all without leaving the plugin.
7. Radar tab → type `+美联储\n!广告` → **Dry run** → hits list
   populates → **Save rules**.
8. In OpenAkita main chat, ask *"今天美股有什么大事"* → Brain
   invokes `fin_pulse_search_news` and returns structured results.
9. Press `d` anywhere in the plugin UI → `data-theme` toggles
   between light and dark.

---

## 8. Development

```bash
# unit tests (212+ cases)
cd D:/OpenAkita/plugins/fin-pulse
python -m pytest tests/ -q

# just the UI hard contracts
python -m pytest tests/test_smoke.py -v
```

Critical dirs:

- `finpulse_*.py` — business modules (see §2).
- `ui/dist/index.html` — single-file React 18 app with a vendored
  5-asset bundle under `_assets/`.
- `tests/test_smoke.py::test_ui_hard_contracts` — enforces the
  avatar-studio UI Kit contract (tokens that must appear, tokens
  that must not).

---

## 9. Credits

- **NewsNow community** — public hot-list aggregation endpoint.
- **Horizon** — AI scoring prompts, cross-source dedupe
  (simhash + title).
- **go-stock** — `canSendAlert` cooldown idea.
- **fed-statement-scraping / PbcCrawler** — central-bank calendar
  gating + PyExecJS fallback.
- **avatar-studio / footage-gate** — UI Kit + SQLite task manager
  contracts.

---

## 10. License

Same as OpenAkita — see `D:/OpenAkita/LICENSE`.
