"""L2 Component Tests: Database CRUD operations."""

import pytest
from datetime import datetime, timedelta

from openakita.storage.database import Database


@pytest.fixture
async def db(tmp_path):
    database = Database(db_path=tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


class TestConversationCRUD:
    @pytest.mark.asyncio
    async def test_create_conversation(self, db):
        conv_id = await db.create_conversation(title="Test Chat")
        assert isinstance(conv_id, int)

    @pytest.mark.asyncio
    async def test_get_conversation(self, db):
        conv_id = await db.create_conversation(title="Retrieval Test")
        conv = await db.get_conversation(conv_id)
        assert conv is not None
        assert conv.title == "Retrieval Test"


class TestMessageCRUD:
    @pytest.mark.asyncio
    async def test_add_and_get_messages(self, db):
        conv_id = await db.create_conversation(title="Msg Test")
        await db.add_message(conv_id, "user", "你好")
        await db.add_message(conv_id, "assistant", "你好！有什么可以帮你？")
        messages = await db.get_messages(conv_id)
        assert len(messages) == 2
        assert messages[0].role == "user"


class TestMemoryCRUD:
    @pytest.mark.asyncio
    async def test_add_memory(self, db):
        mem_id = await db.add_memory("preference", "用户喜欢Python", importance=3)
        assert isinstance(mem_id, int)

    @pytest.mark.asyncio
    async def test_get_memories(self, db):
        await db.add_memory("skill", "用户会用React", importance=2)
        memories = await db.get_memories(category="skill")
        assert len(memories) >= 1

    @pytest.mark.asyncio
    async def test_search_memories(self, db):
        await db.add_memory("fact", "用户的狗叫旺财")
        results = await db.search_memories("旺财")
        assert isinstance(results, list)


class TestPreferenceCRUD:
    @pytest.mark.asyncio
    async def test_set_and_get_preference(self, db):
        await db.set_preference("theme", "dark")
        value = await db.get_preference("theme")
        assert value == "dark"

    @pytest.mark.asyncio
    async def test_get_default_preference(self, db):
        value = await db.get_preference("nonexistent", default="fallback")
        assert value == "fallback"

    @pytest.mark.asyncio
    async def test_get_all_preferences(self, db):
        await db.set_preference("lang", "zh")
        prefs = await db.get_all_preferences()
        assert isinstance(prefs, dict)


class TestSkillCRUD:
    @pytest.mark.asyncio
    async def test_record_skill(self, db):
        skill_id = await db.record_skill("web-search", "1.0", "builtin")
        assert isinstance(skill_id, int)

    @pytest.mark.asyncio
    async def test_get_skill(self, db):
        await db.record_skill("file-ops", "2.0", "github")
        skill = await db.get_skill("file-ops")
        assert skill is not None
        assert skill.version == "2.0"

    @pytest.mark.asyncio
    async def test_list_skills(self, db):
        await db.record_skill("test-skill", "1.0", "local")
        skills = await db.list_skills()
        assert len(skills) >= 1


class TestTaskCRUD:
    @pytest.mark.asyncio
    async def test_record_task(self, db):
        task_id_db = await db.record_task("t1", "Process file")
        assert isinstance(task_id_db, int)

    @pytest.mark.asyncio
    async def test_update_task(self, db):
        await db.record_task("t2", "Upload data")
        await db.update_task("t2", status="completed", result="Success")


class TestTokenUsage:
    @pytest.mark.asyncio
    async def test_get_total(self, db):
        now = datetime.now()
        result = await db.get_token_usage_total(
            start_time=now - timedelta(hours=1),
            end_time=now,
        )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_get_summary(self, db):
        now = datetime.now()
        result = await db.get_token_usage_summary(
            start_time=now - timedelta(hours=1),
            end_time=now,
        )
        assert isinstance(result, list)
