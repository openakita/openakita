"""
Attachment (文件/媒体记忆) 单元测试

覆盖:
- Attachment 类型创建、序列化、反序列化
- storage.py: CRUD (save/get/search/delete/session)
- unified_store.py: 附件 CRUD
- manager.py: record_attachment / search_attachments
"""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from openakita.memory.storage import MemoryStorage
from openakita.memory.types import Attachment, AttachmentDirection
from openakita.memory.unified_store import UnifiedStore


# =========================================================================
# Attachment dataclass tests
# =========================================================================


class TestAttachmentType:

    def test_create_default(self):
        att = Attachment()
        assert att.id
        assert att.direction == AttachmentDirection.INBOUND
        assert att.filename == ""
        assert att.tags == []

    def test_create_with_values(self):
        att = Attachment(
            filename="cat.jpg",
            mime_type="image/jpeg",
            local_path="/tmp/cat.jpg",
            description="一只橘猫趴在沙发上",
            direction=AttachmentDirection.INBOUND,
            tags=["猫", "宠物"],
        )
        assert att.is_image
        assert not att.is_video
        assert not att.is_document
        assert "橘猫" in att.searchable_text
        assert "cat.jpg" in att.searchable_text

    def test_to_dict_and_from_dict(self):
        att = Attachment(
            filename="report.pdf",
            mime_type="application/pdf",
            description="月度报告",
            direction=AttachmentDirection.OUTBOUND,
            tags=["报告"],
            file_size=1024,
        )
        d = att.to_dict()
        assert d["direction"] == "outbound"
        assert d["mime_type"] == "application/pdf"

        restored = Attachment.from_dict(d)
        assert restored.filename == "report.pdf"
        assert restored.direction == AttachmentDirection.OUTBOUND
        assert restored.tags == ["报告"]
        assert restored.file_size == 1024

    def test_is_document(self):
        for mime in ("application/pdf", "text/plain", "text/markdown"):
            att = Attachment(mime_type=mime)
            assert att.is_document, f"{mime} should be document"

    def test_is_audio(self):
        att = Attachment(mime_type="audio/wav")
        assert att.is_audio
        assert not att.is_image

    def test_is_video(self):
        att = Attachment(mime_type="video/mp4")
        assert att.is_video

    def test_searchable_text_empty_fields(self):
        att = Attachment()
        assert att.searchable_text == ""

    def test_from_dict_invalid_direction(self):
        d = {"direction": "invalid_direction", "filename": "test.txt"}
        att = Attachment.from_dict(d)
        assert att.direction == AttachmentDirection.INBOUND


# =========================================================================
# Storage layer tests
# =========================================================================


class TestAttachmentStorage:

    @pytest.fixture
    def storage(self, tmp_path):
        db = MemoryStorage(tmp_path / "test.db")
        yield db
        db.close()

    def test_save_and_get(self, storage):
        att = Attachment(
            id="att-001",
            session_id="sess-1",
            filename="cat.jpg",
            mime_type="image/jpeg",
            description="一只可爱的橘猫",
            local_path="/data/media/cat.jpg",
            tags=["猫", "宠物"],
        )
        storage.save_attachment(att.to_dict())

        result = storage.get_attachment("att-001")
        assert result is not None
        assert result["filename"] == "cat.jpg"
        assert result["description"] == "一只可爱的橘猫"
        assert isinstance(result["tags"], list)
        assert "猫" in result["tags"]

    def test_get_nonexistent(self, storage):
        assert storage.get_attachment("nonexistent") is None

    def test_search_no_query(self, storage):
        for i in range(3):
            att = Attachment(
                id=f"att-{i}",
                filename=f"file_{i}.txt",
                created_at=datetime.now(),
            )
            storage.save_attachment(att.to_dict())

        results = storage.search_attachments(limit=10)
        assert len(results) == 3

    def test_search_with_query(self, storage):
        storage.save_attachment(Attachment(
            id="att-cat", filename="cat.jpg",
            description="一只橘猫趴在沙发上", mime_type="image/jpeg",
        ).to_dict())
        storage.save_attachment(Attachment(
            id="att-dog", filename="dog.jpg",
            description="一只金毛犬在草地上玩耍", mime_type="image/jpeg",
        ).to_dict())

        results = storage.search_attachments(query="橘猫")
        found_ids = [r["id"] for r in results]
        assert "att-cat" in found_ids

    def test_search_filter_mime(self, storage):
        storage.save_attachment(Attachment(
            id="att-img", filename="photo.jpg", mime_type="image/jpeg",
        ).to_dict())
        storage.save_attachment(Attachment(
            id="att-pdf", filename="doc.pdf", mime_type="application/pdf",
        ).to_dict())

        results = storage.search_attachments(mime_type="image/")
        assert len(results) == 1
        assert results[0]["id"] == "att-img"

    def test_search_filter_direction(self, storage):
        storage.save_attachment(Attachment(
            id="att-in", filename="in.jpg", direction=AttachmentDirection.INBOUND,
        ).to_dict())
        storage.save_attachment(Attachment(
            id="att-out", filename="out.pdf", direction=AttachmentDirection.OUTBOUND,
        ).to_dict())

        results = storage.search_attachments(direction="outbound")
        assert len(results) == 1
        assert results[0]["id"] == "att-out"

    def test_delete(self, storage):
        storage.save_attachment(Attachment(id="att-del", filename="del.txt").to_dict())
        assert storage.get_attachment("att-del") is not None

        assert storage.delete_attachment("att-del") is True
        assert storage.get_attachment("att-del") is None

    def test_get_session_attachments(self, storage):
        storage.save_attachment(Attachment(
            id="att-s1-1", session_id="sess-A", filename="a1.txt",
        ).to_dict())
        storage.save_attachment(Attachment(
            id="att-s1-2", session_id="sess-A", filename="a2.txt",
        ).to_dict())
        storage.save_attachment(Attachment(
            id="att-s2-1", session_id="sess-B", filename="b1.txt",
        ).to_dict())

        results = storage.get_session_attachments("sess-A")
        assert len(results) == 2
        assert all(r["session_id"] == "sess-A" for r in results)

    def test_upsert_same_id(self, storage):
        storage.save_attachment(Attachment(
            id="att-up", filename="v1.txt", description="version 1",
        ).to_dict())
        storage.save_attachment(Attachment(
            id="att-up", filename="v2.txt", description="version 2",
        ).to_dict())

        result = storage.get_attachment("att-up")
        assert result["filename"] == "v2.txt"
        assert result["description"] == "version 2"


# =========================================================================
# UnifiedStore attachment tests
# =========================================================================


class TestUnifiedStoreAttachments:

    @pytest.fixture
    def store(self, tmp_path):
        s = UnifiedStore(tmp_path / "unified.db")
        yield s
        s.close()

    def test_save_and_get(self, store):
        att = Attachment(
            filename="cat.jpg", mime_type="image/jpeg",
            description="橘猫照片", direction=AttachmentDirection.INBOUND,
        )
        aid = store.save_attachment(att)
        assert aid == att.id

        loaded = store.get_attachment(att.id)
        assert loaded is not None
        assert loaded.filename == "cat.jpg"
        assert loaded.description == "橘猫照片"

    def test_search(self, store):
        store.save_attachment(Attachment(
            filename="cat.jpg", description="一只橘猫", mime_type="image/jpeg",
        ))
        store.save_attachment(Attachment(
            filename="dog.jpg", description="一只金毛", mime_type="image/jpeg",
        ))

        results = store.search_attachments(query="橘猫")
        assert any(a.description == "一只橘猫" for a in results)

    def test_delete(self, store):
        att = Attachment(filename="tmp.txt")
        store.save_attachment(att)
        assert store.get_attachment(att.id) is not None

        store.delete_attachment(att.id)
        assert store.get_attachment(att.id) is None

    def test_session_attachments(self, store):
        store.save_attachment(Attachment(
            session_id="s1", filename="a.txt",
        ))
        store.save_attachment(Attachment(
            session_id="s2", filename="b.txt",
        ))

        results = store.get_session_attachments("s1")
        assert len(results) == 1
        assert results[0].filename == "a.txt"
