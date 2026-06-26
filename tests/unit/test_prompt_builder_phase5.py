"""Phase 5 单测：prompt 性能优化的可观察行为。

覆盖三个点：

1. ``_is_short_chitchat`` 边界判断 —— 决定是否跳过 Layer 4 多路召回；
2. ``_get_core_memory`` mtime 失效缓存 —— 同一文件两次读，第二次不应碰盘；
   文件 mtime 变化时缓存自动失效，读到新内容；
3. ``_build_memory_section`` 行为：
   - 普通 ask 触发 Layer 4 active retrieval（命中 ``_retrieve_by_query``）；
   - 短 chitchat（"嗯"、"ok"）**不**触发 Layer 4，但 Layer 2 Core Memory
     依然注入，保证 identity slot fast-path 不受影响。
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openakita.prompt import builder as prompt_builder

# ---------------------------------------------------------------------------
# 1) _is_short_chitchat 行为判定
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        (None, True),
        ("", True),
        ("   ", True),
        ("ok", True),
        ("Ok", True),
        ("OKAY", True),
        ("好的", True),
        ("嗯嗯", True),
        ("继续", True),
        ("?", True),
        ("？", True),
        ("hi", True),
        ("hello", True),  # exactly 5 chars but in trigger set
        ("thanks!", True),  # trailing punct stripped
        ("能否解释一下 SQLite 的 WAL 是什么？", False),
        ("帮我看看 src/foo.py 的实现", False),
        ("write a python function to compute fibonacci", False),
        ("修改 utils 里那个 deprecate 的函数", False),
    ],
)
def test_is_short_chitchat_recognizes_low_signal_inputs(text, expected):
    assert prompt_builder._is_short_chitchat(text) is expected


# ---------------------------------------------------------------------------
# 2) MEMORY.md 进程级缓存
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_core_memory_cache():
    """每个 case 都从干净缓存开始。"""
    prompt_builder._CORE_MEMORY_CACHE.clear()
    yield
    prompt_builder._CORE_MEMORY_CACHE.clear()


def _make_manager(memory_md: Path) -> SimpleNamespace:
    return SimpleNamespace(memory_md_path=memory_md)


def test_get_core_memory_caches_by_mtime(tmp_path: Path, monkeypatch):
    md = tmp_path / "MEMORY.md"
    md.write_text("# user profile\n\n用户偏好简体中文回答\n", encoding="utf-8")
    manager = _make_manager(md)

    first = prompt_builder._get_core_memory(manager, max_chars=2000)
    assert "用户偏好简体中文回答" in first

    # 第二次读：mtime / max_chars 没变，必须走缓存。
    # 用 monkeypatch 让 read_text 抛错，缓存命中时根本不会调用到。
    real_read = Path.read_text

    def boom_read_text(self, *args, **kwargs):
        if self == md:
            raise AssertionError("read_text should not be called when cache hits")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom_read_text)
    second = prompt_builder._get_core_memory(manager, max_chars=2000)
    assert second == first


def test_get_core_memory_cache_invalidates_on_mtime_change(tmp_path: Path):
    md = tmp_path / "MEMORY.md"
    md.write_text("# v1 content\n", encoding="utf-8")
    manager = _make_manager(md)

    first = prompt_builder._get_core_memory(manager, max_chars=2000)
    assert "v1 content" in first

    # 显式抬高 mtime（避免 windows 上写得太快 mtime 没变）。
    new_mtime = time.time() + 5
    md.write_text("# v2 content\n", encoding="utf-8")
    import os as _os

    _os.utime(md, (new_mtime, new_mtime))

    second = prompt_builder._get_core_memory(manager, max_chars=2000)
    assert "v2 content" in second
    assert "v1 content" not in second


# ---------------------------------------------------------------------------
# 3) _build_memory_section 短消息跳过 Layer 4
# ---------------------------------------------------------------------------


class _FakeMemoryManager:
    """最小化的 memory_manager stub，只暴露 _build_memory_section 真正会问到的接口。

    回退到几个简单的 sentinel：
    - scratchpad 总是空
    - core_memory 通过 memory_md_path
    - retrieval_engine = None（走 builder 的 _retrieve_by_query 路径，由 monkeypatch 拦截）
    """

    def __init__(self, memory_md_path: Path):
        self.memory_md_path = memory_md_path
        self.store = None
        self.retrieval_engine = None

    # builder 会探 hasattr(memory_manager, "get_injection_context")
    # —— 我们用 monkeypatch 在外面替换 _retrieve_by_query，所以这里不实现。
    def _get_memory_mode(self):
        return "mode1"

    def _ensure_relational(self):
        return False


def test_short_chitchat_skips_active_retrieval(tmp_path: Path, monkeypatch):
    """短消息（"嗯"）不应触发 Layer 4 _retrieve_by_query。"""
    md = tmp_path / "MEMORY.md"
    md.write_text("# core\n\n用户偏好简体中文回答\n", encoding="utf-8")
    mm = _FakeMemoryManager(md)

    called: list[str] = []

    def fake_retrieve_by_query(memory_manager, query, max_tokens=500):
        called.append(query)
        return "FAKE-RETRIEVED"

    monkeypatch.setattr(prompt_builder, "_retrieve_by_query", fake_retrieve_by_query)
    # 关掉 pinned rules / experience / scratchpad / snapshot 干扰，专测 Layer 4 是否被跳过。
    monkeypatch.setattr(prompt_builder, "_build_pinned_rules_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_build_scratchpad_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_build_experience_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_retrieve_relational", lambda *a, **kw: "")

    section = prompt_builder._build_memory_section(
        memory_manager=mm,
        task_description="嗯",
        budget_tokens=1000,
        memory_keywords=None,
        use_compact_guide=True,
    )

    assert called == []
    # Core Memory（identity slot）仍然注入：
    assert "用户偏好简体中文回答" in section


def test_normal_query_still_triggers_active_retrieval(tmp_path: Path, monkeypatch):
    md = tmp_path / "MEMORY.md"
    md.write_text("# core\n\n用户偏好简体中文回答\n", encoding="utf-8")
    mm = _FakeMemoryManager(md)

    called: list[str] = []

    def fake_retrieve_by_query(memory_manager, query, max_tokens=500):
        called.append(query)
        return "FAKE-RETRIEVED-CONTENT"

    monkeypatch.setattr(prompt_builder, "_retrieve_by_query", fake_retrieve_by_query)
    monkeypatch.setattr(prompt_builder, "_build_pinned_rules_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_build_scratchpad_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_build_experience_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_retrieve_relational", lambda *a, **kw: "")

    section = prompt_builder._build_memory_section(
        memory_manager=mm,
        task_description="帮我看看 src/openakita/memory/manager.py 里 _load_memories 的实现",
        budget_tokens=1000,
        memory_keywords=None,
        use_compact_guide=True,
    )

    assert len(called) == 1
    assert "FAKE-RETRIEVED-CONTENT" in section


def test_explicit_memory_keywords_force_retrieval_even_for_short_input(tmp_path: Path, monkeypatch):
    """即便输入是 "ok"，只要 IntentAnalyzer 拿出了关键词，Layer 4 还是要跑。"""
    md = tmp_path / "MEMORY.md"
    md.write_text("# core\n", encoding="utf-8")
    mm = _FakeMemoryManager(md)

    called: list[str] = []

    def fake_retrieve_by_query(memory_manager, query, max_tokens=500):
        called.append(query)
        return "FAKE"

    monkeypatch.setattr(prompt_builder, "_retrieve_by_query", fake_retrieve_by_query)
    monkeypatch.setattr(prompt_builder, "_build_pinned_rules_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_build_scratchpad_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_build_experience_section", lambda *a, **kw: "")
    monkeypatch.setattr(prompt_builder, "_retrieve_relational", lambda *a, **kw: "")

    prompt_builder._build_memory_section(
        memory_manager=mm,
        task_description="ok",
        budget_tokens=1000,
        memory_keywords=["数据库", "SQLite"],
        use_compact_guide=True,
    )
    assert called and "数据库" in called[0]
