"""L1 Unit Tests: UserProfileManager and profile state."""

import pytest
from pathlib import Path

from openakita.core.user_profile import (
    UserProfileManager,
    UserProfileItem,
    UserProfileState,
)


class TestUserProfileState:
    def test_default_state(self):
        state = UserProfileState()
        assert state.is_first_use is True
        assert state.onboarding_completed is False
        assert state.collected_items == {}

    def test_serialize_roundtrip(self):
        state = UserProfileState(is_first_use=False, onboarding_completed=True)
        d = state.to_dict()
        restored = UserProfileState.from_dict(d)
        assert restored.is_first_use is False
        assert restored.onboarding_completed is True


class TestUserProfileItem:
    def test_uncollected_item(self):
        item = UserProfileItem(
            key="name", name="姓名",
            description="用户姓名", question="你叫什么名字？",
        )
        assert item.is_collected is False

    def test_collected_item(self):
        item = UserProfileItem(
            key="name", name="姓名",
            description="用户姓名", question="你叫什么？",
            value="小明", collected_at="2026-01-01",
        )
        assert item.is_collected is True


class TestUserProfileManager:
    @pytest.fixture
    def pm(self, tmp_path):
        return UserProfileManager(
            data_dir=tmp_path / "profile",
            user_md_path=tmp_path / "USER.md",
        )

    def test_is_first_use(self, pm):
        assert isinstance(pm.is_first_use(), bool)

    def test_get_onboarding_prompt(self, pm):
        prompt = pm.get_onboarding_prompt()
        assert isinstance(prompt, str)

    def test_update_profile(self, pm):
        result = pm.update_profile("name", "TestUser")
        assert isinstance(result, bool)

    def test_skip_question(self, pm):
        pm.skip_question("age")

    def test_mark_onboarding_complete(self, pm):
        pm.mark_onboarding_complete()

    def test_get_profile_summary(self, pm):
        summary = pm.get_profile_summary()
        assert isinstance(summary, str)

    def test_get_available_keys(self, pm):
        keys = pm.get_available_keys()
        assert isinstance(keys, list)

