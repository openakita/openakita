# fin-pulse · 踩坑规避与手工校验清单

> Living doc. Each entry documents a known pitfall in the upstream
> reference repos (Horizon, go-stock, PbcCrawler,
> fed-statement-scraping, x-monitor) and the concrete defensive
> measure fin-pulse lands. Re-run the manual probes in §3 after any
> fetcher / dispatcher / scheduler change.

---

## 1. 8 典型坑 × 修法

| # | Pitfall | Source | Fin-pulse fix | Evidence |
|---|---------|--------|---------------|----------|
| 1 | NewsNow `api_url` 写死源码 | 上游示例实现 | `newsnow.api_url` 从 `config` 读；设置页三段向导可改，公共/自建/关闭三档 | `finpulse_fetchers/newsnow_fetcher.py` + `ui/dist/index.html` (Settings § NewsNow) |
| 2 | 文章标题当文件名，Windows 因非法字符崩 | PbcCrawler `crawl.py` | 统一用 `sha256(canonical_url)[:16]` 作 `article.id`；`url_hash` 唯一索引 | `finpulse_fetchers/base.py::NormalizedItem.url_hash` + `articles.url_hash UNIQUE` |
| 3 | `fcntl` Windows 不可用 | x-monitor `monitor.py` | 去掉 `fcntl`；aiosqlite 的 WAL 模式单连接即可串行写 | `finpulse_task_manager.py::init` (`PRAGMA journal_mode=WAL`) |
| 4 | `filter_words` 下游可变对象泄漏 | 关键词 DSL 常见陷阱 | `load_frequency_words` 返回时对 `filter_words` `copy.deepcopy`；同时对 `global_filters` 也做一次 | `finpulse_frequency.py::load_frequency_words` + `tests/test_frequency.py` |
| 5 | 兴趣文件改了分数不刷新 | AI 缓存常见陷阱 | `ai_interests_sha256` 比对；变化即 `reset_ai_scores()` 置空 `ai_score` 列 | `finpulse_ai/filter.py::score_batch` + `tests/test_ai_filter.py::test_interest_change_invalidates` |
| 6 | MCP `days` 无 clamp 直落 SQL | Agent 工具参数常见陷阱 | `_clamp(v, 1, 90, 1)` + `_clamp(limit, 1, 200, 50)` 所有 7 个工具入口统一套用 | `finpulse_services/query.py::_clamp` + `tests/test_services_query.py::TestClamp` |
| 7 | 长榜单推 IM 被截断、标题丢失 | IM 推送常见陷阱 | `split_by_lines(footer, max_bytes, base_header)` 按行切，保证每块 < 25 KB 并把首行头部复制给后续块 | `finpulse_notification/splitter.py` + `tests/test_splitter.py` |
| 8 | Ollama `127.0.0.1` 在 Docker 里失效 | 自托管 LLM 常见陷阱 | fin-pulse 不直接管理 LLM endpoint —— 调用透传到宿主 `LLMClient`；Settings 的「AI Brain」卡片明示「在 OpenAkita 主 Settings → Models 修改 provider / base_url」 | `finpulse_ai/filter.py` (全部 `await api.get_brain().chat(...)`) + README §4.4 |

> 修法 #1 ~ #3 在 Phase 1/2；#4 ~ #7 在 Phase 3/4；#8 是架构决策，写入文档 + UI 提示。

---

## 2. 其他潜在风险与当前缓解

- **NewsNow 公共服务间歇故障**：UI 在 public 模式下渲染黄色
  `oa-config-banner` 警告「公共服务无 SLA」；`source.newsnow.last_error`
  大于 N 次自动跳过（V1.1 会加 circuit breaker）。
- **SEC / 东财接口改版**：每源 `fetch()` 都 try / except，单源失败不影响
  其余源；失败原因写回 `config["source.{id}.last_error"]` 并在 Today Tab
  的 Source 胶囊变红。
- **宿主没装任何 IM 适配器**：`GET /available-channels` 返回空数组时，
  App 顶部 `oa-config-banner` 弹红条 + CTA 跳 Settings。
- **宿主 `TaskScheduler` 未启动**：`on_schedule` 永不触发；Settings →
  Schedules 在 `GET /schedules` 取到 `scheduler_ready=false` 时渲染
  一个禁用提示（目前 UI 仍可创建 schedule，只是不会触发——后续会加
  文字提醒）。
- **task.name 冲突**：`fin-pulse:{session}` / `fin-pulse:radar:{hash}`
  前缀 + `radar_key = sha256(rules_text)[:10]`；UI 新建 schedule 前
  会 `GET /schedules` 检查重名。
- **`channel.send` 权限未授予**：`api.send_message` 静默失败；
  `DispatchService.send` 将 `ok=False` + `error="send_message_unavailable"`
  反馈给调用方。
- **LLM 费用超支**：默认 `dedupe.use_llm=false`；`score_batch` 改兴趣
  才重算；`extract_tags` 只对新文章跑一次。

---

## 3. 手工 Probe 清单（单独可跑）

每条是 PowerShell 一行命令，便于测试人员快速回归。

```powershell
# 3.1 Plugin health
curl -s http://127.0.0.1:9090/api/plugins/fin-pulse/health | jq

# 3.2 Ingest 8 sources (skipping newsnow if off)
curl -s -X POST http://127.0.0.1:9090/api/plugins/fin-pulse/ingest `
  -H "Content-Type: application/json" -d '{"since_hours": 24}' | jq

# 3.3 Single-source probe (useful to isolate a regression)
curl -s -X POST http://127.0.0.1:9090/api/plugins/fin-pulse/ingest/source/wallstreetcn | jq

# 3.4 Generate a morning brief
curl -s -X POST http://127.0.0.1:9090/api/plugins/fin-pulse/digest/run `
  -H "Content-Type: application/json" -d '{"session": "morning"}' | jq

# 3.5 Dry-run a radar rule
curl -s -X POST http://127.0.0.1:9090/api/plugins/fin-pulse/radar/evaluate `
  -H "Content-Type: application/json" `
  -d '{"rules_text": "+美联储\n+降息\n!传闻", "since_hours": 24}' | jq

# 3.6 Tool redaction — via the REST passthrough
curl -s http://127.0.0.1:9090/api/plugins/fin-pulse/config | jq '.config'

# 3.7 Schedule CRUD (requires at least one adapter in gateway)
curl -s -X POST http://127.0.0.1:9090/api/plugins/fin-pulse/schedules `
  -H "Content-Type: application/json" `
  -d '{"mode":"daily_brief","cron":"0 8 * * *","session":"morning","channel":"feishu","chat_id":"oc_xxx"}' | jq

curl -s http://127.0.0.1:9090/api/plugins/fin-pulse/schedules | jq

# 3.8 Dispatch smoke (replace chat_id)
curl -s -X POST http://127.0.0.1:9090/api/plugins/fin-pulse/dispatch/send `
  -H "Content-Type: application/json" `
  -d '{"channel":"feishu","chat_id":"oc_xxx","content":"[fin-pulse] probe"}' | jq
```

---

## 4. 自动化回归

All pitfalls above have an associated test. Running the full suite
covers every entry in §1:

```powershell
cd D:/OpenAkita/plugins/fin-pulse
python -m pytest tests/ -q
```

Expected outcome (as of `1.0.0`):

```
213 passed, 4 skipped in ~3s
```

If any pitfall test starts failing after an upstream change, review
the corresponding row in §1 before relaxing the assertion.

---

## 5. Deferred to V1.1

- FastMCP stdio server (V1.0 uses `register_tools` single-track).
- NewsNow one-click installer (Docker compose helper).
- `signal_watch` mode (持续订阅单个实体 / 关键词并打点事件线).
- Automatic circuit breaker per source based on rolling failure
  rate (V1.0 relies on per-source `last_error` display only).
- Cross-plugin handoff via `assets_bus` (table is reserved but
  unused in V1.0; `tests/test_task_manager.py` asserts it stays
  empty).
