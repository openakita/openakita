# Sprint 16 P0 — aiosqlite close hooks 修法详表

参考：
- 取证：`_v33_biz/_aiosqlite_callsites_inventory.md` 第 2/4 节
- 上游 forensics：`_v32_biz_e2e/v32_regression_report.md` §10 P0
- 上游真凶：`_v32_biz_e2e/_diagnostics_analysis.md`（14 × `_connection_worker_thread` 真稳 stale）

## 1. 修法 c1：`PluginManager.unload_all_plugins`（新增）+ `shutdown()` 复用

### 1.1 改动文件
`src/openakita/plugins/manager.py`

### 1.2 改动语义
- 新增 `async def unload_all_plugins(*, per_plugin_timeout_s: float = 3.0) -> int`
  - LIFO 顺序遍历 `self._loaded.keys()`
  - 每个 plugin 用 `asyncio.wait_for(self.unload_plugin(pid), timeout=per_plugin_timeout_s)` 包，互不影响
  - 返回真正 unload 成功的 plugin 数（其他被 timeout / raise 的不计但也不阻塞）
- 修改 `async def shutdown(self, *, unload_plugins: bool = True) -> None`
  - 默认 `unload_plugins=True`：先调 `unload_all_plugins()` → 再 close AssetBus
  - 当 `agent.shutdown` 自己也走 plugin unload 时可传 `unload_plugins=False` 防双 unload

### 1.3 为什么治根
- plugin 端 `on_unload` 早已正确写了 `await self._tm.close()`（每个 TaskManager 都覆盖；rg 已确认覆盖率）
- 缺失的就是宿主端的"调用入口"——v32 之前 serve 模式 shutdown 从不进 `pm.unload_plugin(pid)`
- 现在一旦 `pm.shutdown()` 被 lifespan 调用，14 个 `_connection_worker_thread` 全部被 cooperative join 掉

### 1.4 fail-safe
- LIFO 顺序：最后加载的 plugin 最先 unload，避开"被依赖的 plugin 先死、依赖方再死"的方向
- per-plugin timeout 3.0s：上游 lifespan stage timeout 也是 8s，单 plugin 卡死最多吞 3s，留 5s 给其他 plugin
- try/except 包住每个调用：单 plugin 报错不影响其他

## 2. 修法 c2：lifespan teardown 新增 `_shutdown_plugin_aiosqlite_workers`

### 2.1 改动文件
`src/openakita/api/server.py`

### 2.2 改动语义
在 `_arm_shutdown_diagnostics` **之前**新增一个 `@app.on_event("shutdown")` 处理器：

```python
@app.on_event("shutdown")
async def _shutdown_plugin_aiosqlite_workers() -> None:
    stage_timeout = float(settings.lifespan_stage_timeout_s or 8)
    agent_ref = getattr(app.state, "agent", None)
    pm = getattr(agent_ref, "_plugin_manager", None) if agent_ref else None
    if pm is None:
        return
    await asyncio.wait_for(pm.shutdown(), timeout=stage_timeout)
    # 同时关掉 token_stats.Database 单例 + app.state.storage_database
```

注册位置：
- AFTER 已有 5 个 shutdown handler（org_runtime / hot_reloader / memory_storage / async_audit_writer / stream_cleanup）—— plugin on_unload 可能还在调用它们
- BEFORE `_arm_shutdown_diagnostics`（diagnostics 必须 LAST，才能 dump 到一个干净的 thread set）

### 2.3 为什么这是治根
- 这一刀让 lifespan teardown 调用链第一次走通 `pm.shutdown() → unload_all_plugins() → plugin.on_unload() → tm.close() → aiosqlite worker join`
- 14 个 stale aiosqlite worker 在 lifespan 周期内全部被关掉，shutdown_diagnostics 的 baseline 期 non_daemon_alive 应该回到 0~2（只剩 `MainThread` + `asyncio_0`，14 个 aiosqlite worker 都不在了）

### 2.4 fail-safe
- `await asyncio.wait_for(pm.shutdown(), timeout=stage_timeout)`：8s 上限挂在外层，遵照已有 lifespan stage 节奏
- try/except 全部包住：handler 自身不能 raise，否则后续 diagnostics handler 跑不了
- 失败时仍走 force-exit watchdog 兜底（threading.Timer，已存在）

## 3. 修法 c3：token_stats.Database 单例 + app.state.storage_database 防御

### 3.1 改动文件
`src/openakita/api/server.py`（在 c2 handler 中追加）

### 3.2 改动语义
在 `_shutdown_plugin_aiosqlite_workers` 末尾追加：

```python
# 关 token_stats 单例（少数路由用 lazy-init Database）
from openakita.api.routes import token_stats as _token_stats
if getattr(_token_stats, "_db_instance", None) is not None:
    await asyncio.wait_for(_token_stats._reset_db(), timeout=3.0)

# 关 app.state.storage_database / _storage_database（前瞻：未来路由可能挂这里）
for attr in ("storage_database", "_storage_database"):
    db = getattr(app.state, attr, None)
    if db and hasattr(db, "close"):
        await asyncio.wait_for(db.close(), timeout=3.0)
```

### 3.3 为什么这是 belt-and-suspenders
- `token_stats.py` 单例（lazy）—— 用户访问过 `/api/stats/tokens/*` 后会留 1 个 aiosqlite worker
- 现有 `_reset_db()` 函数已经 wire 好但从未被生产代码调用 → 这次接进来
- `app.state.storage_database` 钩子是为未来路由准备的；当前 codebase 还没人挂 attr，所以 no-op

### 3.4 fail-safe
- 每个 close 独立 timeout（3.0s）+ try/except
- 失败时只 debug log，不阻塞 plugin unload 这条主线

## 4. 测试覆盖

### 4.1 单测：`tests/unit/test_plugins/test_unload_all_aiosqlite.py`（新文件，4 个测试）

| 测试 | 校验 |
|---|---|
| `test_unload_all_closes_aiosqlite_worker_threads` | 在 fixture 里建 3 个 fake plugin（每个 on_load 真的开 aiosqlite），`unload_all_plugins` 返回 3 且 `threading.enumerate()` 中 `_connection_worker_thread` 个数回到 baseline |
| `test_unload_all_tolerates_individual_plugin_failure` | 1 个 plugin 的 on_unload 主动 raise；`unload_all_plugins` 仍能完成其他 plugin 的 unload |
| `test_shutdown_unloads_plugins_and_closes_asset_bus` | `pm.shutdown()`（默认 unload_plugins=True）同时 unload plugin + close AssetBus |
| `test_shutdown_with_unload_plugins_false_only_closes_asset_bus` | `pm.shutdown(unload_plugins=False)` 只关 AssetBus，plugin 保留 |

### 4.2 集成测：`tests/api/test_lifespan_closes_aiosqlite.py`（新文件，2 个测试）

| 测试 | 校验 |
|---|---|
| `test_lifespan_teardown_releases_aiosqlite_workers` | 用 TestClient 起 lifespan，startup 期 load 3 个 aiosqlite plugin → `threading.enumerate` 见 +3 worker；TestClient 退出后 lifespan shutdown 跑完 → worker 数回到 baseline（即 leaked = 0） |
| `test_lifespan_teardown_safe_when_no_plugin_manager` | `create_app(agent=None)` 时新 handler 不能 raise（headless / CLI 场景兼容） |

### 4.3 测试结果（焦点合集）

```
tests/api/test_shutdown_diagnostics.py        7 passed
tests/api/test_shutdown_endpoint_bounded.py   3 passed
tests/api/test_force_exit_watchdog_threading.py 8 passed
tests/api/test_lifespan_closes_aiosqlite.py   2 passed   ← NEW
tests/unit/test_plugins/test_unload_all_aiosqlite.py 4 passed  ← NEW

Total: 24/24 passed.
```

### 4.4 ruff/mypy

```
ruff check src/openakita/plugins/manager.py src/openakita/api/server.py
  → All checks passed!

mypy src/openakita/plugins/manager.py src/openakita/api/server.py
  → Success: no issues found in 2 source files
```

### 4.5 smoke import

```
python -c "from openakita.api.server import create_app; create_app(agent=None); print('OK')"
  → OK
```

## 5. 不动的代码（合规边界）

- `aiosqlite` 源（`.venv/Lib/site-packages/aiosqlite/core.py:90`）— 不动
- 任何 `plugins/*/plugin.py` 的 on_load / on_unload — 不动（已经写对）
- `_v*_biz_e2e/` / `_v*_biz/` 产物 — 不动
- `apps/setup-center/` 前端 — 不动
- `agent.shutdown()`（在 `_agent_legacy.py:9248`）— 不动；本次只是补 lifespan 这条入口
