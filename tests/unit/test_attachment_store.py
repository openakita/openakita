from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from openakita.api.attachment_store import AttachmentStore


class TestAttachmentStore:
    def test_save_uploaded_file_returns_reference_attachment(self, tmp_path):
        store = AttachmentStore(root=tmp_path)

        record = store.save_uploaded_file(
            content=b"hello attachment",
            filename="note.txt",
            mime_type="text/plain",
            conversation_id="conv-a",
        )

        assert record["conversation_id"] == "conv-a"
        assert record["type"] == "document"
        assert record["content_url"].startswith("/api/attachments/")
        assert Path(record["storage_path"]).exists()
        assert "hello attachment" in record["text_preview"]

    def test_import_local_directory_as_listing_reference(self, tmp_path):
        store = AttachmentStore(root=tmp_path / "store")
        folder = tmp_path / "folder"
        folder.mkdir()
        (folder / "a.txt").write_text("a", encoding="utf-8")
        (folder / "b.txt").write_text("b", encoding="utf-8")

        record = store.import_local_path(path=str(folder), conversation_id="conv-dir")

        assert record["type"] == "directory"
        assert record["storage_path"] == ""
        assert sorted(record["entries"]) == ["a.txt", "b.txt"]

    def test_assign_to_conversation_moves_file(self, tmp_path):
        store = AttachmentStore(root=tmp_path)
        record = store.save_uploaded_file(
            content=b"abc",
            filename="image.png",
            mime_type="image/png",
            conversation_id=None,
        )

        old_path = Path(record["storage_path"])
        moved = store.assign_to_conversation(record["id"], "conv-final")

        assert moved is not None
        assert moved["conversation_id"] == "conv-final"
        assert Path(moved["storage_path"]).exists()
        assert not old_path.exists()

    def test_cleanup_stale_shared_removes_orphans(self, tmp_path):
        store = AttachmentStore(root=tmp_path)
        record = store.save_uploaded_file(
            content=b"old",
            filename="old.txt",
            mime_type="text/plain",
            conversation_id=None,
        )
        record_path = store._record_path(record["id"])
        data = json.loads(record_path.read_text(encoding="utf-8"))
        data["created_at"] = (datetime.now() - timedelta(days=2)).isoformat()
        record_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        deleted = store.cleanup_stale_shared(max_age_hours=24)

        assert deleted == 1
        assert store.get(record["id"]) is None

    def test_describe_attachment_supports_inline_snippet_and_reference(self, tmp_path):
        store = AttachmentStore(root=tmp_path)
        inline = store.save_uploaded_file(
            content="hello\nworld".encode("utf-8"),
            filename="short.txt",
            mime_type="text/plain",
            conversation_id="conv-a",
        )
        snippet = store.save_uploaded_file(
            content=("A" * 6000).encode("utf-8"),
            filename="mid.txt",
            mime_type="text/plain",
            conversation_id="conv-a",
        )
        reference = store.save_uploaded_file(
            content=("B\n" * 5000).encode("utf-8"),
            filename="long.txt",
            mime_type="text/plain",
            conversation_id="conv-a",
        )

        inline_summary = store.describe_attachment(inline["id"], inline_chars=100, snippet_chars=200)
        snippet_summary = store.describe_attachment(snippet["id"], inline_chars=100, snippet_chars=8000)
        reference_summary = store.describe_attachment(reference["id"], inline_chars=100, snippet_chars=400)

        assert inline_summary["mode"] == "inline"
        assert "hello" in inline_summary["inline_text"]
        assert snippet_summary["mode"] == "snippet"
        assert "中间省略" in snippet_summary["snippet_text"]
        assert reference_summary["mode"] == "reference"
        assert reference_summary["tool_hint"]

    def test_read_text_chunk_and_search_text(self, tmp_path):
        store = AttachmentStore(root=tmp_path)
        record = store.save_uploaded_file(
            content="\n".join(f"line {i}" for i in range(1, 21)).encode("utf-8"),
            filename="lines.txt",
            mime_type="text/plain",
            conversation_id="conv-a",
        )

        chunk = store.read_text_chunk(record["id"], offset=5, limit=4)
        search = store.search_text(record["id"], query="line 10", context_lines=1, max_results=5)

        assert chunk["start_line"] == 5
        assert chunk["end_line"] == 8
        assert "line 5" in chunk["content"]
        assert search["matches"]
        assert search["matches"][0]["line"] == 10
