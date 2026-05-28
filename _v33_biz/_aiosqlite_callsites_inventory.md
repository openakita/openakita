# Sprint 16 P0 — aiosqlite call-site inventory (Phase A 取证)

引用上游 forensics：
- `_v32_biz_e2e/v32_regression_report.md` §8.2（真凶定位）
- `_v32_biz_e2e/_diagnostics_analysis.md`（14 × `_connection_worker_thread` 真稳每轮 stale）
- `_v32_biz_e2e/diagnostics_v32_PHASEA-1.log:5-18`（14 个 `Thread-NN (_connection_worker_thread)` 在 baseline 全部 alive）

## 1. `src/openakita` 内部 aiosqlite 直接调用点（rg 全量）

| # | 文件 | 行 | 创建时机 | 当前 close 时机 | 是否真凶 | 说明 |
|---|---|---|---|---|---|---|
| 1 | `plugins/asset_bus.py:115` | `self._db = await aiosqlite.connect(self._db_path)` | 首次 `publish()` / `get()` 等触发 `_ensure_init()` → `init()` | `PluginManager.shutdown()` → `_asset_bus.close()`（但 `PluginManager.shutdown` 当前**未在 lifespan teardown 中被调用**）| **是（潜在 1 个）** | host 单例；lifespan teardown 未挂；惰性 init 但生产环境通常已被某条 publish/get 路径 trigger → 已经开了 conn |
| 2 | `storage/database.py:36` | `self._connection = await aiosqlite.connect(self.db_path)` | `Database.connect()` 被显式调用 | `Database.close()`（已实现，但 lifespan 未挂） | **可疑（≤1 个）** | 当前唯一显式 `Database()` 持久构造点在 `api/routes/token_stats.py:53`，per-request 局部 `db = Database()`，但路由代码未在 finally 中 close → 每次调用泄一个 connection（生产中无固定数量） |
| 3 | `api/routes/agents.py:723` | `async with aiosqlite.connect(...) as db:` | per-request | `async with` 自动 close | 否 | request-scope，auto-close |
| 4 | `api/routes/feedback_store.py:36` | `conn = await aiosqlite.connect(db_path)` (多个调用) | per-call (`_get_conn`) | 每个调用方均 `try/finally: await conn.close()` | 否 | per-call open/close，无常驻 |

直接调用 `aiosqlite.connect` 的 **src/openakita** 内部 4 处全部识别。

## 2. plugin 树（`plugins/<name>/*_task_manager.py` 等）— 14 真凶之源

`PluginManager.load_all()` 启动期对每个 enabled plugin 触发 `on_load`，绝大多数 plugin 在 `on_load` 中：

```python
self._tm = TaskManager(data_dir / "<name>.db")
```

随后某条首调用路径（startup `on_load` 路径或第一个 HTTP 路由进来时）触发 `await self._tm.init()` 内的 `aiosqlite.connect(...)` —— 一个常驻 `_connection_worker_thread` 自此 alive 直到 `await self._tm.close()`。

落盘 plugin 列表（`data/plugins/*`）共 **18 个**：

```
avatar-studio        ✓ aiosqlite (avatar_task_manager.py)
clip-sense           ✓ aiosqlite (clip_task_manager.py)
ecommerce-image      ✓ aiosqlite (ecom_task_manager.py)
excel-maker          ✓ aiosqlite (excel_task_manager.py)
fin-pulse            ✓ aiosqlite (finpulse_task_manager.py + finpulse_models.py)
finance-auto         ✓ aiosqlite (finance_auto_backend/db/__init__.py + ai/...)
footage-gate         ✓ aiosqlite (footage_gate_task_manager.py)
happyhorse-video     ✓ aiosqlite (happyhorse_task_manager.py)
idea-research        ✓ aiosqlite (idea_task_manager.py)
manga-studio         ✓ aiosqlite (manga_task_manager.py)
media-post           ✓ aiosqlite (mediapost_task_manager.py)
media-strategy       ✓ aiosqlite (media_task_manager.py)
omni-post            ✓ aiosqlite (omni_post_task_manager.py)
ppt-maker            ✓ aiosqlite (ppt_task_manager.py)
seedance-video       ✓ aiosqlite (task_manager.py)
subtitle-craft       ✓ aiosqlite (subtitle_task_manager.py)
tongyi-image         ✓ aiosqlite (tongyi_task_manager.py)
word-maker           ✓ aiosqlite (word_task_manager.py + word_maker_inline/python_deps.py)
```

每个 plugin 在 `on_unload` 中已经写了 `await self._tm.close()`（已审核 seedance-video/plugin.py:415 等）。这条 close 路径完全可用 —— **问题在于宿主没人在 shutdown 时调用 `pm.unload_plugin(pid)`**。

### 2.1 真因（重申）

```
[serve mode shutdown flow]
shutdown_event.set()
  → api_task.cancel()
    → uvicorn lifespan shutdown handlers
      ├── _shutdown_org_runtime          ✓ stop reconcile / runtime
      ├── _shutdown_policy_hot_reloader  ✓
      ├── _shutdown_memory_storage       ✓ checkpoint memory
      ├── _shutdown_async_audit_writer   ✓
      ├── _stop_stream_cleanup           ✓
      └── _arm_shutdown_diagnostics      ✓ forensics
  → stop_im_channels                     ✓ gateway / pool / orchestrator / session_mgr
  ── (END) ──
agent.shutdown() 永远没被调用     ←──── BUG
  → pm.unload_plugin(pid) 永远没被调用    ←──── 14 aiosqlite 连接永远没 close
```

`agent.shutdown()` 在 CLI 模式经 `cli_chat` 的 `finally` 没经过；在 serve 模式更没经过。只有 sub-agent 经 `factory._on_done_callback` → `loop.create_task(agent.shutdown())` 会跑这条路径，但 sub-agent 不持有 `_owns_plugin_manager=True`，所以 `pm.unload_plugin` 也不会触发（见 `_agent_legacy.py:9267` `if pm is not None and owns_pm`）。

**结论**：进程退出时 14 ± 4 个 plugin TaskManager 的 aiosqlite 常驻连接全部 stale，对应 14 个 `_connection_worker_thread` non-daemon。

## 3. `sqlite3.connect`（同步 SQLite，**非 aiosqlite**）排除

| 文件 | 是否 aiosqlite worker 来源 |
|---|---|
| `orgs/sqlite_store.py` | 否（同步 sqlite3） |
| `orgs/project_store.py` | 否 |
| `orgs/blackboard.py` | 否 |
| `runtime/backends/sqlite.py` | 否（同步） |
| `runtime/event_store.py` | 否 |
| `memory/storage.py` | 否（同步） |
| `core/token_tracking.py` | 否（同步，独立 writer thread `token-usage-writer`） |
| `api/routes/memory_repair.py` | 否（同步） |

`sqlite3.connect` 不会创建 `_connection_worker_thread`，所以这一支彻底排除。

## 4. 真凶清单（最终）

按修法优先级排序：

| 级别 | callsite/component | 修法 | 治本占比（预估 14 stale 中） |
|---|---|---|---|
| **P0-c1** | plugin tree × N（每个 plugin 的 `on_unload` 已有 close，但没人调用 `pm.unload_plugin`）| 在 PluginManager 加 `unload_all_plugins()` + lifespan teardown 调用 | 14/14 |
| P0-c2 | `PluginManager._asset_bus` (1 个 max, lazy) | 同上 `unload_all` 之后调 `pm.shutdown()`（已存在，只是没人调）| 0–1 |
| P0-c3 | `storage.Database` 单例 leak 防御 | 在 lifespan teardown 收集所有 `Database` 实例并 close（也修复 `token_stats.py` 的 per-request leak）| 0–N（少量） |

c1 是治根的主刀；c2/c3 是 belt-and-suspenders。

## 5. 不动的代码（合规边界）

- ❌ **不改 aiosqlite 上游源码**（不在依赖管理范围；只在调用端显式 close）
- ❌ **不给 aiosqlite worker thread 强 `daemon=True`**（同上，monkey-patch 第三方风险大）
- ❌ **不改任何 plugin（`plugins/*`）的 on_unload**（每个 plugin 都已经正确写了 `await self._tm.close()`；问题在宿主调用链）
- ❌ **不动 `_v*_biz_e2e/` / `_v*_biz/`**（forensics 静止物）

## 6. 已 close 与未 close 的对照矩阵

| 组件 | close API | startup wire-up | shutdown wire-up | 现状 |
|---|---|---|---|---|
| `AssetBus` | `await close()` | lazy | `PluginManager.shutdown()` calls it; PM.shutdown 未挂 lifespan | leak (lazy 触发后) |
| `Database` (`storage/database.py`) | `await close()` | `Database.connect()` | 路由代码没 close | leak per-request |
| `feedback_store` per-call conn | per-call `await close()` finally | per-call | per-call | 已正常 close ✓ |
| `agents.py` profile memory stats | `async with` | per-call | per-call | 已正常 close ✓ |
| **Plugin TaskManager (×N)** | **每 plugin `on_unload` 已有 close** | `pm.load_all` | **❌ `pm.unload_all` 不存在；没人在 lifespan 调它** | **真凶 leak (14 个)** |

修法见 `_p0_aiosqlite_close_hooks.md`。
