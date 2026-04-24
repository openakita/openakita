"""Unit tests for the host-level Asset Bus (commit D).

Covers:
- basic publish/get round-trip
- ACL matrix: owner / explicit shared_with / wildcard "*" / forbidden
- non-existent asset returns None (no existence leak)
- list_owned + delete_owned (owner vs non-owner)
- TTL expiry and sweep_expired
- sweep_owner removes only the target owner's rows
- concurrent publish keeps asset_id unique
- lazy init (no explicit init() call)
- PluginAPI gates: missing assets.publish/consume yields None / [] / False
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from openakita.plugins.asset_bus import AssetBus
from openakita.plugins.manifest import PluginManifest


# ---------- fixtures ----------


@pytest.fixture
async def bus(tmp_path: Path):
    db_path = tmp_path / "asset_bus.db"
    b = AssetBus(db_path)
    await b.init()
    try:
        yield b
    finally:
        await b.close()


def _manifest(plugin_id: str, perms: list[str]) -> PluginManifest:
    return PluginManifest.model_validate(
        {
            "id": plugin_id,
            "name": plugin_id,
            "version": "1.0.0",
            "type": "python",
            "permissions": perms,
        }
    )


def _api(tmp_path: Path, plugin_id: str, perms: list[str], asset_bus: AssetBus | None):
    """Build a PluginAPI with only the host_refs we need."""
    from openakita.plugins.api import PluginAPI

    data_dir = tmp_path / plugin_id
    data_dir.mkdir(parents=True, exist_ok=True)
    return PluginAPI(
        plugin_id=plugin_id,
        manifest=_manifest(plugin_id, perms),
        granted_permissions=perms,
        data_dir=data_dir,
        host_refs={"asset_bus": asset_bus} if asset_bus else {},
    )


# ---------- basic round-trip ----------


@pytest.mark.asyncio
async def test_publish_then_get_owner(bus):
    aid = await bus.publish(
        plugin_id="p1",
        asset_kind="video",
        source_path="/tmp/x.mp4",
        preview_url="http://x/preview",
        duration_sec=12.5,
        metadata={"codec": "h264"},
    )
    assert isinstance(aid, str) and len(aid) >= 16

    row = await bus.get(aid, requester_plugin_id="p1")
    assert row is not None
    assert row["asset_id"] == aid
    assert row["asset_kind"] == "video"
    assert row["source_path"] == "/tmp/x.mp4"
    assert row["preview_url"] == "http://x/preview"
    assert row["duration_sec"] == 12.5
    assert row["metadata"] == {"codec": "h264"}
    assert row["created_by_plugin"] == "p1"
    assert row["shared_with"] == []
    assert row["expires_at"] is None
    assert row["created_at"] is not None


# ---------- ACL matrix ----------


@pytest.mark.asyncio
async def test_acl_default_private(bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="video")
    assert await bus.get(aid, requester_plugin_id="p2") is None
    assert await bus.get(aid, requester_plugin_id="p1") is not None


@pytest.mark.asyncio
async def test_acl_explicit_shared_with(bus):
    aid = await bus.publish(
        plugin_id="p1", asset_kind="video", shared_with=["p2"]
    )
    assert await bus.get(aid, requester_plugin_id="p2") is not None
    assert await bus.get(aid, requester_plugin_id="p3") is None


@pytest.mark.asyncio
async def test_acl_wildcard_public(bus):
    aid = await bus.publish(
        plugin_id="p1", asset_kind="video", shared_with=["*"]
    )
    assert await bus.get(aid, requester_plugin_id="p2") is not None
    assert await bus.get(aid, requester_plugin_id="anyone") is not None


@pytest.mark.asyncio
async def test_get_missing_asset_returns_none(bus):
    assert await bus.get("does-not-exist", requester_plugin_id="p1") is None


@pytest.mark.asyncio
async def test_get_with_empty_requester_returns_none(bus):
    aid = await bus.publish(
        plugin_id="p1", asset_kind="video", shared_with=["*"]
    )
    assert await bus.get(aid, requester_plugin_id="") is None


# ---------- list / delete ----------


@pytest.mark.asyncio
async def test_list_owned_filters_to_owner(bus):
    a1 = await bus.publish(plugin_id="p1", asset_kind="video")
    a2 = await bus.publish(plugin_id="p1", asset_kind="audio")
    a3 = await bus.publish(plugin_id="p2", asset_kind="video")

    p1_assets = await bus.list_owned("p1")
    p2_assets = await bus.list_owned("p2")

    assert {a["asset_id"] for a in p1_assets} == {a1, a2}
    assert {a["asset_id"] for a in p2_assets} == {a3}


@pytest.mark.asyncio
async def test_delete_owned_owner_succeeds(bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="video")
    assert await bus.delete_owned(aid, "p1") is True
    assert await bus.get(aid, requester_plugin_id="p1") is None


@pytest.mark.asyncio
async def test_delete_owned_non_owner_fails(bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="video")
    assert await bus.delete_owned(aid, "p2") is False
    # row still readable by owner
    assert await bus.get(aid, requester_plugin_id="p1") is not None


# ---------- TTL / sweep ----------


@pytest.mark.asyncio
async def test_ttl_zero_means_no_expiry(bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="x", ttl_seconds=0)
    row = await bus.get(aid, requester_plugin_id="p1")
    assert row is not None
    assert row["expires_at"] is None


@pytest.mark.asyncio
async def test_sweep_expired_removes_only_expired(bus):
    long = await bus.publish(plugin_id="p1", asset_kind="x", ttl_seconds=3600)
    short = await bus.publish(plugin_id="p1", asset_kind="x", ttl_seconds=1)
    fresh = await bus.publish(plugin_id="p1", asset_kind="x")  # no TTL

    # Sweep with explicit "now" 5 seconds in the future to avoid sleeping.
    removed = await bus.sweep_expired(now=time.time() + 5)
    assert removed == 1

    assert await bus.get(long, requester_plugin_id="p1") is not None
    assert await bus.get(short, requester_plugin_id="p1") is None
    assert await bus.get(fresh, requester_plugin_id="p1") is not None


@pytest.mark.asyncio
async def test_sweep_expired_idempotent(bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="x", ttl_seconds=1)
    n1 = await bus.sweep_expired(now=time.time() + 5)
    n2 = await bus.sweep_expired(now=time.time() + 5)
    assert n1 == 1
    assert n2 == 0
    assert await bus.get(aid, requester_plugin_id="p1") is None


# ---------- sweep_owner ----------


@pytest.mark.asyncio
async def test_sweep_owner_removes_only_target_owner(bus):
    await bus.publish(plugin_id="p1", asset_kind="video")
    await bus.publish(plugin_id="p1", asset_kind="audio")
    a3 = await bus.publish(plugin_id="p2", asset_kind="video")

    n = await bus.sweep_owner("p1")
    assert n == 2
    assert await bus.list_owned("p1") == []
    p2_rows = await bus.list_owned("p2")
    assert {r["asset_id"] for r in p2_rows} == {a3}


@pytest.mark.asyncio
async def test_sweep_owner_no_rows_returns_zero(bus):
    assert await bus.sweep_owner("never-published") == 0


@pytest.mark.asyncio
async def test_sweep_owner_empty_id_returns_zero(bus):
    await bus.publish(plugin_id="p1", asset_kind="video")
    assert await bus.sweep_owner("") == 0
    # the real owner's row is untouched
    assert len(await bus.list_owned("p1")) == 1


# ---------- concurrency / lazy init ----------


@pytest.mark.asyncio
async def test_concurrent_publish_unique_ids(bus):
    aids = await asyncio.gather(
        *[bus.publish(plugin_id="p1", asset_kind="video") for _ in range(10)]
    )
    assert len(set(aids)) == 10
    assert await bus.count_all() == 10


@pytest.mark.asyncio
async def test_lazy_init_without_explicit_init(tmp_path):
    db_path = tmp_path / "lazy.db"
    b = AssetBus(db_path)
    try:
        aid = await b.publish(plugin_id="p1", asset_kind="video")
        row = await b.get(aid, requester_plugin_id="p1")
        assert row is not None
        assert db_path.exists()
    finally:
        await b.close()


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_path):
    db_path = tmp_path / "idempotent.db"
    b = AssetBus(db_path)
    try:
        await b.init()
        await b.init()  # second call must not raise
        aid = await b.publish(plugin_id="p1", asset_kind="x")
        assert aid
    finally:
        await b.close()


# ---------- argument validation ----------


@pytest.mark.asyncio
async def test_publish_requires_plugin_id(bus):
    with pytest.raises(ValueError):
        await bus.publish(plugin_id="", asset_kind="video")


@pytest.mark.asyncio
async def test_publish_requires_asset_kind(bus):
    with pytest.raises(ValueError):
        await bus.publish(plugin_id="p1", asset_kind="")


# ---------- PluginAPI permission gates ----------


@pytest.mark.asyncio
async def test_publish_asset_blocked_without_permission(tmp_path, bus):
    api = _api(tmp_path, "p1", perms=[], asset_bus=bus)
    aid = await api.publish_asset(asset_kind="video", source_path="/x.mp4")
    assert aid is None
    assert await bus.count_all() == 0


@pytest.mark.asyncio
async def test_publish_asset_with_permission_works(tmp_path, bus):
    api = _api(tmp_path, "p1", perms=["assets.publish"], asset_bus=bus)
    aid = await api.publish_asset(
        asset_kind="video", source_path="/x.mp4"
    )
    assert aid is not None
    assert await bus.count_all() == 1


@pytest.mark.asyncio
async def test_consume_asset_blocked_without_permission(tmp_path, bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="video", shared_with=["*"])

    api = _api(tmp_path, "p2", perms=[], asset_bus=bus)
    assert await api.consume_asset(aid) is None


@pytest.mark.asyncio
async def test_consume_asset_with_permission_and_acl(tmp_path, bus):
    aid_priv = await bus.publish(plugin_id="p1", asset_kind="video")
    aid_pub = await bus.publish(plugin_id="p1", asset_kind="video", shared_with=["*"])

    api = _api(tmp_path, "p2", perms=["assets.consume"], asset_bus=bus)
    assert await api.consume_asset(aid_priv) is None
    row = await api.consume_asset(aid_pub)
    assert row is not None
    assert row["asset_id"] == aid_pub


@pytest.mark.asyncio
async def test_list_my_assets_requires_publish_permission(tmp_path, bus):
    await bus.publish(plugin_id="p1", asset_kind="x")
    api_no = _api(tmp_path, "p1", perms=[], asset_bus=bus)
    assert await api_no.list_my_assets() == []
    api_yes = _api(tmp_path, "p1", perms=["assets.publish"], asset_bus=bus)
    rows = await api_yes.list_my_assets()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_delete_my_asset_owner_only(tmp_path, bus):
    aid = await bus.publish(plugin_id="p1", asset_kind="x")
    api_intruder = _api(tmp_path, "p2", perms=["assets.publish"], asset_bus=bus)
    assert await api_intruder.delete_my_asset(aid) is False
    api_owner = _api(tmp_path, "p1", perms=["assets.publish"], asset_bus=bus)
    assert await api_owner.delete_my_asset(aid) is True
    assert await bus.count_all() == 0


@pytest.mark.asyncio
async def test_methods_safe_when_bus_missing(tmp_path):
    api = _api(tmp_path, "p1", perms=["assets.publish", "assets.consume"], asset_bus=None)
    assert await api.publish_asset(asset_kind="x") is None
    assert await api.consume_asset("anything") is None
    assert await api.list_my_assets() == []
    assert await api.delete_my_asset("anything") is False


# ---------- manifest ----------


def test_assets_perms_in_advanced_set():
    from openakita.plugins.manifest import ADVANCED_PERMISSIONS

    assert "assets.publish" in ADVANCED_PERMISSIONS
    assert "assets.consume" in ADVANCED_PERMISSIONS
