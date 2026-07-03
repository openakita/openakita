"""Regression coverage for the shared tolerant JSONL reader (upstream #691).

The v1 ``orgs/event_store.py`` + ``orgs/blackboard.py`` file readers were
replaced by the orgs_v2 backends, which now route their append-only JSONL
reads through :mod:`openakita.orgs.jsonl_utils`. Upstream #691 hardened those
readers against torn / NUL-corrupted tail writes (a crash mid-append can leave
the last line partially written or NUL-filled). This test pins that behaviour
on the shared util that both orgs_v2 readers depend on, since the original
v1-targeted ``test_event_store.py`` / ``test_blackboard.py`` no longer apply.
"""

from __future__ import annotations

from pathlib import Path

from openakita.orgs.jsonl_utils import iter_jsonl_objects_reverse, read_jsonl_objects


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


class TestReadJsonlObjects:
    def test_reads_valid_records_and_skips_blank_lines(self, tmp_path: Path):
        p = _write(tmp_path / "a.jsonl", '{"id": 1}\n\n{"id": 2}\n')
        assert read_jsonl_objects(p) == [{"id": 1}, {"id": 2}]

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert read_jsonl_objects(tmp_path / "missing.jsonl") == []

    def test_recovers_earlier_records_when_tail_is_nul_only(self, tmp_path: Path):
        p = _write(tmp_path / "b.jsonl", '{"id": 1}\n{"id": 2}\n' + "\x00" * 16 + "\n")
        assert read_jsonl_objects(p) == [{"id": 1}, {"id": 2}]

    def test_recovers_json_line_with_appended_nul_tail(self, tmp_path: Path):
        p = _write(tmp_path / "c.jsonl", '{"id": 1}\n{"id": 2}\x00\x00\x00')
        assert read_jsonl_objects(p) == [{"id": 1}, {"id": 2}]

    def test_applies_decoder(self, tmp_path: Path):
        p = _write(tmp_path / "d.jsonl", '{"v": 1}\n{"v": 2}\n')
        assert read_jsonl_objects(p, decoder=lambda d: d["v"]) == [1, 2]


class TestIterJsonlObjectsReverse:
    def test_yields_dicts_newest_first(self, tmp_path: Path):
        p = _write(tmp_path / "e.jsonl", '{"id": 1}\n{"id": 2}\n{"id": 3}\n')
        assert list(iter_jsonl_objects_reverse(p)) == [{"id": 3}, {"id": 2}, {"id": 1}]

    def test_reverse_tolerates_nul_tail(self, tmp_path: Path):
        p = _write(tmp_path / "f.jsonl", '{"id": 1}\n{"id": 2}\n' + "\x00" * 8)
        assert list(iter_jsonl_objects_reverse(p)) == [{"id": 2}, {"id": 1}]
