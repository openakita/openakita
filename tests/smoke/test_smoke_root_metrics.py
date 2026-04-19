"""L0 Smoke: 6 个根因修复硬指标回归挡板。

每条 case 直接对应 _exploratory_fix_plan_20260417 中的一个 F* 修复，
回归即 CI 失败。所有测试都纯离线、无网络、无 LLM 依赖，
单条 < 1s。

覆盖：
  M1  cache_control 注入（F1a）
  M2  cached_tokens 解析（F1b）
  M3  qwen3.5-plus supports_cache=True（F1c）
  M4  新写入记忆即时召回（F2a/F2b）
  M5  子 Agent 数值任务零代码守卫（F3）
  M6  ProfileHandler 未知 key 回退记忆（F4a）
"""

from __future__ import annotations

import pytest

# ─────────────────── M1: cache_control 注入 ───────────────────


def test_cache_control_injected_for_dashscope_qwen35():
    """DashScope + qwen3.5-plus + 含 DYNAMIC_BOUNDARY 的 system → 必须注入 cache_control。

    回归场景：F1a 修复前 OpenAI 兼容 provider 不会拆分 system，
    导致 prompt cache 永远未命中、cost 翻 5x。
    """
    from openakita.llm.cache import SYSTEM_PROMPT_DYNAMIC_BOUNDARY
    from openakita.llm.providers.openai import OpenAIProvider
    from openakita.llm.types import EndpointConfig, LLMRequest, Message

    cfg = EndpointConfig(
        name="smoke-dashscope",
        provider="dashscope",
        api_type="openai",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="sk-dummy",
        model="qwen3.5-plus",
    )
    provider = OpenAIProvider(cfg)

    static = "你是 OpenAkita 助手。规则：永不编造。"
    dyn = "当前时间: 2026-04-18 10:00"
    sys_prompt = f"{static}\n{SYSTEM_PROMPT_DYNAMIC_BOUNDARY}\n{dyn}"

    req = LLMRequest(
        messages=[Message(role="user", content="你好")],
        system=sys_prompt,
        max_tokens=128,
    )

    body = provider._build_request_body(req)
    sys_msg = body["messages"][0]
    assert sys_msg["role"] == "system"
    content = sys_msg["content"]
    assert isinstance(content, list), "system content 必须被改写为 list[block]"
    cached_blocks = [b for b in content if b.get("cache_control")]
    assert cached_blocks, "至少有一个 block 必须带 cache_control"
    assert cached_blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert static in cached_blocks[0]["text"]
    dyn_blocks = [b for b in content if not b.get("cache_control")]
    assert dyn_blocks, "动态部分必须保留为不带 cache_control 的独立 block"
    assert dyn in dyn_blocks[0]["text"]


def test_cache_control_skipped_for_non_cache_model():
    """非 supports_cache 的 provider/model 不应做任何注入，避免误伤其他厂商。"""
    from openakita.llm.providers.openai import OpenAIProvider
    from openakita.llm.types import EndpointConfig, LLMRequest, Message

    cfg = EndpointConfig(
        name="smoke-openrouter",
        provider="openrouter",
        api_type="openai",
        base_url="https://openrouter.ai/api/v1",
        api_key="sk-dummy",
        model="some-non-cache-model",
    )
    provider = OpenAIProvider(cfg)
    req = LLMRequest(
        messages=[Message(role="user", content="hi")],
        system="hello",
        max_tokens=64,
    )
    body = provider._build_request_body(req)
    sys_msg = body["messages"][0]
    assert isinstance(sys_msg["content"], str), "非 dashscope 不应改写 system 为 list"


# ─────────────────── M2: cached_tokens 解析 ───────────────────


def test_parse_response_extracts_cached_tokens():
    """LLM usage.prompt_tokens_details.cached_tokens 必须落到 Usage.cache_read_input_tokens。

    回归场景：F1b 修复前 cached_tokens 被丢弃，token_tracking 永远显示 0%。
    """
    from openakita.llm.providers.openai import OpenAIProvider
    from openakita.llm.types import EndpointConfig

    cfg = EndpointConfig(
        name="smoke",
        provider="dashscope",
        api_type="openai",
        base_url="https://x",
        api_key="k",
        model="qwen3.5-plus",
    )
    provider = OpenAIProvider(cfg)

    fake_resp = {
        "id": "resp-1",
        "model": "qwen3.5-plus",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "prompt_tokens_details": {
                "cached_tokens": 800,
                "cache_creation_input_tokens": 0,
            },
        },
    }
    parsed = provider._parse_response(fake_resp)
    assert parsed.usage.input_tokens == 1000
    assert parsed.usage.output_tokens == 50
    assert parsed.usage.cache_read_input_tokens == 800, (
        "cached_tokens 必须被解析到 cache_read_input_tokens"
    )


def test_parse_response_falls_back_to_top_level_cached_tokens():
    """部分网关把 cached_tokens 平铺到 usage 顶层，需要兼容。"""
    from openakita.llm.providers.openai import OpenAIProvider
    from openakita.llm.types import EndpointConfig

    cfg = EndpointConfig(
        name="smoke",
        provider="dashscope",
        api_type="openai",
        base_url="https://x",
        api_key="k",
        model="qwen3.5-plus",
    )
    provider = OpenAIProvider(cfg)
    fake = {
        "id": "r",
        "model": "qwen3.5-plus",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "x"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "cached_tokens": 70},
    }
    parsed = provider._parse_response(fake)
    assert parsed.usage.cache_read_input_tokens == 70


# ─────────────────── M3: model_registry 标记 ───────────────────


def test_qwen35_plus_supports_cache():
    """qwen3.5-plus 必须保持 supports_cache=True，否则 cache_control 注入路径会被绕过。"""
    from openakita.llm.model_registry import get_model_capabilities

    caps = get_model_capabilities("qwen3.5-plus")
    assert caps.supports_cache is True


# ─────────────────── M4: 新写入记忆即时召回 ───────────────────


def test_new_memory_immediately_recallable(tmp_path):
    """save_semantic 后立即 search_semantic_scored 必须能拿到（recency 豁免）。

    回归场景：F2a/F2b 修复前 ChromaDB 异步未刷新 + MIN_RERANK_SCORE 过滤，
    新事实首轮召回率为 0。
    """
    from openakita.memory.types import MemoryType, SemanticMemory
    from openakita.memory.unified_store import UnifiedStore

    store = UnifiedStore(tmp_path / "smoke.db")
    mem = SemanticMemory(
        content="用户的猫叫小白",
        type=MemoryType.FACT,
        subject="用户",
        predicate="宠物",
        importance_score=0.8,
    )
    store.save_semantic(mem)

    results = store.search_semantic_scored("用户的猫", limit=5)
    ids = [m.id for m, _s in results]
    assert mem.id in ids, "FTS5 union 后新写入记忆应可被召回"


# ─────────────────── M5: 子 Agent 零代码数值守卫 ───────────────────


@pytest.mark.parametrize(
    "task,output,tools,expected_trigger",
    [
        # 蒙特卡洛 + 数值结论 + 无代码 → 触发
        (
            "用蒙特卡洛模拟 1000 次抛硬币，计算正面概率",
            "正面概率约 50.2%",
            ["delegate_to_agent"],
            True,
        ),
        # 跑了 run_shell → 不触发
        (
            "蒙特卡洛模拟 1000 次抛硬币",
            "正面概率约 49.8%",
            ["run_shell"],
            False,
        ),
        # 任务非数值 → 不触发
        (
            "请帮我总结这篇文章",
            "文章主旨是 ...",
            ["web_search"],
            False,
        ),
        # 输出无具体数字 → 不触发
        (
            "统计本周日历",
            "本周共有若干场会议",
            [],
            False,
        ),
    ],
)
def test_agent_output_guard(task, output, tools, expected_trigger):
    from openakita.core.agent_output_guard import (
        DISCLAIMER_TEXT,
        validate_no_fabricated_numbers,
    )

    triggered, augmented = validate_no_fabricated_numbers(task, output, tools)
    assert triggered is expected_trigger
    if expected_trigger:
        assert augmented.endswith(DISCLAIMER_TEXT)
    else:
        assert augmented == output


# ─────────────────── M6: ProfileHandler 未知 key 回退 ───────────────────


def test_profile_unknown_key_falls_back_to_memory():
    """update_user_profile 收到非白名单 key 必须自动写入 add_memory，而非报错。

    回归场景：F4a 修复前直接返回 `❌ 未知的档案项`，
    小白用户提供的事实信息全部丢失。
    """
    from openakita.tools.handlers.profile import ProfileHandler

    captured: dict = {}

    class _FakeProfileMgr:
        def get_available_keys(self):
            return ["name", "industry"]

        def update_profile(self, key, value):  # pragma: no cover - 不该被走到
            captured["profile_set"] = (key, value)

    class _FakeMemMgr:
        def add_memory(self, mem):
            captured["memory"] = mem

    class _FakeAgent:
        profile_manager = _FakeProfileMgr()
        memory_manager = _FakeMemMgr()

    handler = ProfileHandler(_FakeAgent())
    msg = handler._update_profile({"key": "favorite_food", "value": "拉面"})
    assert "拉面" in msg
    assert "memory" in captured, "未知 key 必须落入 add_memory"
    assert "profile_set" not in captured
    assert captured["memory"].content.endswith("favorite_food = 拉面")
