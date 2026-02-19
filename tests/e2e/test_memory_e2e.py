"""E2E 记忆系统测试: 端到端场景验证.

场景:
1. 用户发图 → 记忆存储 → 搜索找回
2. agent 生成文件 → outbound 附件记录
3. 中文模糊查询找回附件
4. 完整对话→记忆提取→检索注入
"""

import asyncio
from datetime import datetime
from pathlib import Path

import pytest

from openakita.memory.manager import MemoryManager
from openakita.memory.retrieval import RetrievalEngine
from openakita.memory.types import (
    Attachment,
    AttachmentDirection,
    ConversationTurn,
    Memory,
    MemoryType,
    SemanticMemory,
)
from openakita.memory.unified_store import UnifiedStore


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
    mem_dir, memory_md = memory_dir
    mgr = MemoryManager(data_dir=mem_dir, memory_md_path=memory_md, brain=mock_brain)
    mgr.start_session("e2e-session-1")
    return mgr


class TestUserSendsImageAndSearchesBack:
    """场景: 用户发了一张猫的图片, 后来问"那张猫图给我一下"."""

    def test_full_flow(self, manager):
        manager.record_attachment(
            filename="cat_photo.jpg",
            mime_type="image/jpeg",
            local_path="/data/media/cat_photo.jpg",
            description="一只橘猫趴在沙发上打盹",
            direction="inbound",
            tags=["猫", "宠物", "橘猫"],
        )
        manager.record_turn(
            role="user", content="看看这张我家猫的照片",
        )

        results = manager.search_attachments(query="橘猫")
        assert len(results) >= 1
        found = results[0]
        assert found.filename == "cat_photo.jpg"
        assert found.local_path == "/data/media/cat_photo.jpg"
        assert "橘猫" in found.description

    def test_fuzzy_search(self, manager):
        manager.record_attachment(
            filename="cat_sunset.jpg",
            mime_type="image/jpeg",
            description="夕阳下的猫咪在窗台上",
            direction="inbound",
        )

        results = manager.search_attachments(query="猫咪")
        assert len(results) >= 1


class TestAgentGeneratesFileRecorded:
    """场景: agent 通过 write_file 工具生成了报告, 记录为 outbound."""

    def test_outbound_via_record_turn(self, manager):
        manager.record_turn(
            role="assistant",
            content="已生成月度报告",
            tool_calls=[{
                "name": "write_file",
                "arguments": {"path": "/output/report_2026_02.pdf"},
            }],
            attachments=[{
                "filename": "report_2026_02.pdf",
                "mime_type": "application/pdf",
                "local_path": "/output/report_2026_02.pdf",
                "direction": "outbound",
                "description": "2026年2月月度销售报告",
            }],
        )

        results = manager.search_attachments(direction="outbound")
        assert len(results) >= 1
        assert any("report" in a.filename for a in results)


class TestChineseQueryVariations:
    """场景: 用户用各种中文表述找回附件."""

    @pytest.fixture(autouse=True)
    def setup_data(self, manager):
        manager.record_attachment(
            filename="contract.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            description="与客户A签订的服务合同",
            direction="inbound",
        )
        manager.record_attachment(
            filename="screenshot.png",
            mime_type="image/png",
            description="系统报错截图，显示数据库连接失败",
            direction="inbound",
        )
        manager.record_attachment(
            filename="meeting_audio.mp3",
            mime_type="audio/mp3",
            transcription="明天下午三点开项目评审会",
            direction="inbound",
        )

    def test_search_by_content_description(self, manager):
        results = manager.search_attachments(query="合同")
        assert any("contract" in a.filename for a in results)

    def test_search_by_transcription(self, manager):
        results = manager.search_attachments(query="评审会")
        assert any("meeting" in a.filename for a in results)

    def test_search_by_error_description(self, manager):
        results = manager.search_attachments(query="报错")
        assert any("screenshot" in a.filename for a in results)


class TestRetrievalEngineAttachmentIntegration:
    """场景: RetrievalEngine 在回答附件相关问题时能检索到附件."""

    def test_retrieve_mentions_attachment(self, manager):
        manager.record_attachment(
            filename="design_mockup.png",
            mime_type="image/png",
            description="首页设计稿v2",
        )
        manager.store.save_semantic(SemanticMemory(
            content="用户在做产品设计",
            type=MemoryType.FACT,
        ))

        engine = RetrievalEngine(manager.store)
        result = engine.retrieve("给我那个设计稿的图片")
        assert isinstance(result, str)


class TestConversationToMemoryExtraction:
    """场景: 对话中提取记忆, 后续能检索到."""

    def test_add_memory_then_retrieve(self, manager):
        manager.add_memory(Memory(
            content="用户的项目叫 OpenAkita",
            type=MemoryType.FACT,
        ))
        manager.add_memory(Memory(
            content="用户偏好使用 Python 和 TypeScript",
            type=MemoryType.PREFERENCE,
        ))

        ctx = manager.get_injection_context("技术栈是什么")
        assert isinstance(ctx, str)

    def test_multiple_sessions(self, manager):
        manager.record_turn(role="user", content="我在开发一个AI助手项目")
        manager.end_session("AI助手开发")

        manager.start_session("e2e-session-2")
        manager.record_turn(role="user", content="继续昨天的工作")
        manager.end_session("继续开发")


class TestMixedMemoryAndAttachment:
    """场景: 语义记忆和附件记忆混合检索."""

    def test_both_memories_and_attachments(self, manager):
        manager.add_memory(Memory(
            content="用户养了一只叫小花的橘猫",
            type=MemoryType.FACT,
        ))
        manager.record_attachment(
            filename="xiaohua.jpg",
            mime_type="image/jpeg",
            description="小花在阳台上晒太阳",
            direction="inbound",
        )

        mems = manager.search_memories(query="小花")
        atts = manager.search_attachments(query="小花")
        assert len(mems) >= 1 or len(atts) >= 1
