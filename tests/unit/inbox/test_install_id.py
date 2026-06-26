from __future__ import annotations

from openakita.inbox.install_id import get_or_create_install_id_hash, hash_install_id


async def test_install_id_hash_is_stable(tmp_path) -> None:
    first = await get_or_create_install_id_hash(tmp_path)
    second = await get_or_create_install_id_hash(tmp_path)

    assert first == second
    assert len(first) == 64
    assert (tmp_path / "install_id").read_text("utf-8").strip()


def test_hash_install_id_is_sha256_hex() -> None:
    assert (
        hash_install_id("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
