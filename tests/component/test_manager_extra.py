"""补充 manager 测试: record_turn+attachments, record_attachment, on_context_compressing."""

import asyncio
from pathlib import Path

import pytest

from openakita.memory.types import (
    Attachment,
    AttachmentDirection,
    Memory,
    MemoryPriority,
    MemoryType,
)


@pytest.fixture
def memory_dir(tmp_workspace):
    mem_dir = tmp_workspace / "data" / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    memory_md = tmp_workspace / "identity" / "MEMORY.md"
    memory_md.parent.mkdir(parents=True, exist_ok=True)
    memory_md.write_text("# Memory\n", encoding="utf-8")
    return mem_dir, memory_md


@pytest.fixture
def manager(memory_dir, mock_brain):
    from openakita.memory.manager import MemoryManager
    mem_dir, memory_md = memory_dir
    mgr = MemoryManager(data_dir=mem_dir, memory_md_path=memory_md, brain=mock_brain)
    mgr.start_session("test-session-1")
    return mgr


class TestRecordAttachment:
    def test_record_inbound_image(self, manager):
        aid = manager.record_attachment(
            filename="cat.jpg",
            mime_type="image/jpeg",
            local_path="/tmp/cat.jpg",
            description="一只橘猫趴在沙发上",
            direction="inbound",
            tags=["猫", "宠物"],
        )
        assert isinstance(aid, str)
        assert len(aid) > 0

    def test_record_outbound_file(self, manager):
        aid = manager.record_attachment(
            filename="report.pdf",
            mime_type="application/pdf",
            local_path="/tmp/report.pdf",
            direction="outbound",
        )
        assert isinstance(aid, str)

    def test_search_attachments_by_query(self, manager):
        manager.record_attachment(
            filename="dog.jpg",
            mime_type="image/jpeg",
            description="一只金毛犬在草地上",
        )
        results = manager.search_attachments(query="金毛犬")
        assert len(results) >= 1
        assert any(a.description == "一只金毛犬在草地上" for a in results)

    def test_search_attachments_by_mime(self, manager):
        manager.record_attachment(filename="a.jpg", mime_type="image/jpeg")
        manager.record_attachment(filename="b.pdf", mime_type="application/pdf")
        results = manager.search_attachments(mime_type="image/")
        assert all(a.mime_type.startswith("image/") for a in results)

    def test_search_attachments_by_direction(self, manager):
        manager.record_attachment(filename="in.jpg", direction="inbound")
        manager.record_attachment(filename="out.pdf", direction="outbound")
        results = manager.search_attachments(direction="outbound")
        assert all(a.direction == AttachmentDirection.OUTBOUND for a in results)

    def test_search_attachments_by_session(self, manager):
        manager.record_attachment(filename="s1.jpg")
        results = manager.search_attachments(session_id="test-session-1")
        assert len(results) >= 1

    def test_get_attachment(self, manager):
        aid = manager.record_attachment(filename="test.txt", description="测试文件")
        att = manager.get_attachment(aid)
        assert att is not None
        assert att.filename == "test.txt"
        assert att.description == "测试文件"


class TestRecordTurnWithAttachments:
    def test_attachments_parameter(self, manager):
        manager.record_turn(
            role="user",
            content="看看这张照片",
            attachments=[{
                "filename": "cat.jpg",
                "mime_type": "image/jpeg",
                "local_path": "/tmp/cat.jpg",
                "description": "一只猫",
            }],
        )
        results = manager.search_attachments(query="猫")
        assert len(results) >= 1

    def test_assistant_turn_outbound(self, manager):
        manager.record_turn(
            role="assistant",
            content="已生成报告",
            attachments=[{
                "filename": "report.pdf",
                "mime_type": "application/pdf",
                "direction": "outbound",
            }],
        )
        results = manager.search_attachments(direction="outbound")
        assert len(results) >= 1

    def test_no_attachments_backward_compat(self, manager):
        manager.record_turn(role="user", content="hello world, how are you today?")

    def test_empty_attachments(self, manager):
        manager.record_turn(role="user", content="no files here", attachments=[])


class TestOnContextCompressing:
    def test_extracts_quick_facts(self, manager):
        messages = [
            {"role": "user", "content": "我喜欢使用 Python 开发"},
            {"role": "assistant", "content": "好的，我记住了"},
        ]
        asyncio.get_event_loop().run_until_complete(
            manager.on_context_compressing(messages)
        )

    def test_handles_empty_messages(self, manager):
        asyncio.get_event_loop().run_until_complete(
            manager.on_context_compressing([])
        )

    def test_handles_rules(self, manager):
        messages = [
            {"role": "user", "content": "不要使用 var，必须用 const 或 let"},
        ]
        asyncio.get_event_loop().run_until_complete(
            manager.on_context_compressing(messages)
        )


class TestEndSession:
    def test_end_session_basic(self, manager):
        manager.record_turn(role="user", content="帮我写一个 Python 脚本来处理数据")
        manager.record_turn(role="assistant", content="好的，我来帮你写")
        manager.end_session("写了一个 Python 脚本", success=True)

    def test_end_session_with_errors(self, manager):
        manager.record_turn(role="user", content="这段代码有问题吧")
        manager.end_session("代码调试", success=False, errors=["TypeError"])

    def test_end_session_empty(self, manager):
        manager.end_session("empty session")
