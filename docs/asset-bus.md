# Asset Bus — 跨插件资产总线

> Host-level cross-plugin asset registry. v1.0
>
> 入口实现: `src/openakita/plugins/asset_bus.py`
> 测试覆盖: `tests/unit/test_asset_bus.py` (28 cases)

---

## 1. 解决什么问题

OpenAkita 的"流水线"型场景里，常常需要 A 插件产出一个中间产物（视频、音频、字幕、缩略图、转录文本等），交给 B 插件继续处理。在没有 Asset Bus 之前，唯一可行的做法是：

* B 插件直接读取 A 插件的私有 SQLite / 私有目录（耦合到 A 的内部 schema）；
* 或者通过文件系统约定 + 自定义 JSON 元数据（每对插件都要重写一遍）；
* 或者塞进 Hook 的 `kwargs`（但 Hook 是事件，不适合传递可寻址的产物）。

Asset Bus 提供一个 **由宿主拥有、跨插件共享、带 ACL** 的轻量资产登记表，让 A 只负责 `publish_asset(...)`，B 只负责 `consume_asset(asset_id)`，双方互不感知对方的存储细节。

---

## 2. 与 `subtitle-craft` 的"私表"是什么关系？

`plugins/subtitle-craft/subtitle_task_manager.py` 中已经预留了一个 `assets_bus` 表，用于该插件**自己内部**的任务记账（v1.0 阶段）。这是**插件私有表**，与本文档描述的"宿主 Asset Bus"在物理与语义上都**不同**：

| | subtitle-craft 私表 | 宿主 Asset Bus（本文档）|
|---|---|---|
| 文件位置 | `plugin_data/subtitle-craft/*.db` | `data/asset_bus.db` |
| 拥有者 | subtitle-craft 自己 | `PluginManager` |
| 跨插件可读 | 否 | 是（受 ACL 控制） |
| 生命周期 | 插件卸载即随之删除 | 卸载触发 `sweep_owner` 清理本插件行 |
| 调用方式 | 直接 SQL | 走 `PluginAPI.publish_asset / consume_asset / list_my_assets / delete_my_asset` |

**v2.0 路线**：subtitle-craft 在做完字幕后，调用宿主的 `publish_asset(asset_kind="subtitle_pack", source_path=..., shared_with=["video-editor"])`，video-editor 通过 `consume_asset(asset_id)` 拿到字幕包路径，完成"字幕 → 成片"的 Handoff。私表则继续保留，仅用作 subtitle-craft 内部的任务记账，不对外。

---

## 3. Schema

```sql
CREATE TABLE IF NOT EXISTS assets_bus (
    asset_id           TEXT PRIMARY KEY,    -- uuid4 hex
    asset_kind         TEXT NOT NULL,       -- 业务类型，如 "video" / "subtitle_pack" / "tts_audio"
    source_path        TEXT,                -- 文件系统路径（见安全约定）
    preview_url        TEXT,                -- 预览 URL，可选
    duration_sec       REAL,                -- 时长，可选
    metadata_json      TEXT NOT NULL DEFAULT '{}',
    created_by_plugin  TEXT NOT NULL,       -- 拥有者 plugin_id
    shared_with_json   TEXT NOT NULL DEFAULT '[]',  -- ["pluginA", "*", ...]
    created_at         TEXT NOT NULL,       -- ISO8601 UTC
    expires_at         TEXT                 -- ISO8601 UTC，NULL 表示永不过期
);
CREATE INDEX idx_asset_bus_owner   ON assets_bus(created_by_plugin);
CREATE INDEX idx_asset_bus_kind    ON assets_bus(asset_kind);
CREATE INDEX idx_asset_bus_expires ON assets_bus(expires_at);
```

* 使用 WAL 模式开启并发读 + 单写。
* `aiosqlite` 已是 OpenAkita 的核心依赖，不引入新依赖。
* 单进程单文件，**v1 不做跨进程或集群一致性**。

---

## 4. ACL 矩阵

`get(asset_id, requester_plugin_id)` 的访问决策：

| 场景 | requester == owner | requester ∈ shared_with | "*" ∈ shared_with | 其它 |
|---|---|---|---|---|
| 返回结果 | 完整 row | 完整 row | 完整 row | `None` |

设计要点：

* **无差别返回 None**：当资产不存在 *或* 调用方无权读取时，统一返回 `None`，不区分两种语义。这样持有 `assets.consume` 权限的恶意插件无法通过遍历 `asset_id` 来枚举别人的资产。
* **owner 恒可读**：拥有者无需在 `shared_with` 中再加自己。
* **`*` 表示公开**：所有持有 `assets.consume` 权限的插件都能读取。需要慎用。

---

## 5. 权限闸门

新增两个 Advanced 权限（需用户在插件设置里手动授权）：

| 权限 | 含义 | 涉及方法 |
|---|---|---|
| `assets.publish` | 发布、列出、删除自己拥有的资产 | `publish_asset`、`list_my_assets`、`delete_my_asset` |
| `assets.consume` | 消费别的插件的资产（受 ACL） | `consume_asset` |

* 同一个插件常常同时持有两种权限（典型流水线既消费上游也产出下游）。
* `data.own` 等其它权限与本权限**正交**：本权限只控制 Asset Bus 表，与插件自己的 `data/` 目录互不影响。

---

## 6. PluginAPI

```python
api.publish_asset(
    *,
    asset_kind: str,
    source_path: str | None = None,
    preview_url: str | None = None,
    duration_sec: float | None = None,
    metadata: dict | None = None,
    shared_with: list[str] | None = None,   # ["plugin_b", "plugin_c"] or ["*"]
    ttl_seconds: int | None = None,         # None / 0 表示永不过期
) -> str | None                              # 返回 asset_id

api.consume_asset(asset_id: str) -> dict | None    # 受 ACL，None 同时表示"不存在"和"无权"
api.list_my_assets() -> list[dict]                  # 仅返回 owner == self 的行
api.delete_my_asset(asset_id: str) -> bool          # 仅 owner 能删
```

权限不足、`asset_bus` 未注入、底层抛异常时，所有方法都**返回安全默认值**（`None` / `[]` / `False`），并在插件日志里打 warning，不会向上抛。

---

## 7. `source_path` 的安全约定 ⚠

**Asset Bus 不会校验 `source_path`。** 这是有意为之：

* 校验"文件存在 + 可读"会让 publisher 在跨进程文件转移过程中受限；
* 校验"路径在某个白名单目录下"会限制合法的多盘符场景。

**消费方 (consumer) 必须**：

1. 自己解析路径 (`pathlib.Path(...).resolve()`)；
2. 自己确认它在自己**预期的根目录之下**（`relative_to` 检查避免 `..` 路径穿越）；
3. 自己处理"文件已被 owner 删除"的情况（`FileNotFoundError`）。

否则恶意 publisher 可以塞 `source_path="C:\\Windows\\System32\\config\\SAM"` 之类的字符串，让 consumer 替它读敏感文件。

---

## 8. 生命周期

* **创建**：`PluginManager.__init__` 注入 `AssetBus(settings.data_dir / "asset_bus.db")` 到 `host_refs["asset_bus"]`，但 **不立即** 打开 SQLite 连接；
* **首次使用懒加载**：第一次 `publish_asset` / `consume_asset` 触发 `init()`，创建 schema，打开 WAL；
* **插件卸载**：HTTP 路由 `DELETE /api/plugins/{id}` 在 `removed=True` 后调用 `pm.purge_plugin_assets(id)`，等价于 `asset_bus.sweep_owner(id)`，清掉所有该插件拥有的行，避免出现"幽灵资产"（owner 早已不在）；
* **TTL 过期**：`sweep_expired()` 删除所有 `expires_at <= now()` 的行；v1 **不带后台扫描任务**，由测试或将来的运维端点显式调用；
* **宿主关闭**：`PluginManager.shutdown()` 关闭 SQLite 连接。

---

## 9. v2.0 用法示例：subtitle-craft → video-editor Handoff

```python
async def on_finished(self, *, task_id: str, srt_path: str, duration: float):
    aid = await self.api.publish_asset(
        asset_kind="subtitle_pack",
        source_path=srt_path,
        duration_sec=duration,
        metadata={
            "language": "zh-CN",
            "format": "srt",
            "speaker_diarized": True,
        },
        shared_with=["video-editor"],   # 仅指定下游可读
        ttl_seconds=24 * 3600,          # 24 小时后清理
    )
    await self.api.gateway.send(
        channel="wecom",
        to=user_id,
        text=f"字幕已生成，video-editor 可通过 asset_id={aid} 直接接续。",
    )


async def on_message_received(self, *, text: str, **_):
    m = re.match(r"^做成片 (\w+)$", text)
    if not m:
        return
    asset = await self.api.consume_asset(m.group(1))
    if asset is None:
        return "字幕不存在，或当前插件无权访问。"

    srt_path = Path(asset["source_path"]).resolve()
    if not srt_path.is_relative_to(self.expected_workspace.resolve()):
        return "字幕路径越权，已拒绝。"     # 见 §7 安全约定

    await self.start_render(srt_path=srt_path, **asset["metadata"])
```

---

## 10. 后续路线（不在本 commit 范围）

| 项 | 状态 |
|---|---|
| 前端管理 UI（查看、清理资产） | TODO（`apps/setup-center` 未来加 tab） |
| 后台 TTL 扫描任务 | TODO（按需，目前调用方主动 sweep 即可） |
| 跨进程一致性 | 不做（v1 单进程足够） |
| 引用计数 / 软删除 | 不做（owner 删除即真删，下游自行容错） |
| 大对象转储到对象存储 | 不做（沿用文件系统路径，复杂度让位简单度） |
