"""L1 Unit Tests: ResponseHandler static/utility methods."""

import pytest

from openakita.core.response_handler import (
    strip_thinking_tags,
    strip_tool_simulation_text,
    clean_llm_response,
    ResponseHandler,
    request_expects_artifact,
)


class TestStripThinkingTags:
    def test_strip_basic_thinking(self):
        text = "<thinking>I need to analyze this</thinking>Here is my answer."
        result = strip_thinking_tags(text)
        assert "<thinking>" not in result
        assert "Here is my answer" in result

    def test_no_thinking_tags(self):
        text = "Just a normal response."
        result = strip_thinking_tags(text)
        assert result == text

    def test_empty_input(self):
        assert strip_thinking_tags("") == ""


class TestStripToolSimulation:
    def test_strip_tool_sim(self):
        text = "Let me check that for you."
        result = strip_tool_simulation_text(text)
        assert isinstance(result, str)


class TestCleanLLMResponse:
    def test_clean_basic(self):
        result = clean_llm_response("  Hello, how can I help?  ")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_clean_with_thinking(self):
        text = "<thinking>plan</thinking>Here is the answer."
        result = clean_llm_response(text)
        assert "Here is the answer" in result


class TestResponseHandlerStaticMethods:
    def test_should_compile_prompt_simple(self):
        result = ResponseHandler.should_compile_prompt("你好")
        assert isinstance(result, bool)

    def test_should_compile_prompt_complex(self):
        result = ResponseHandler.should_compile_prompt(
            "帮我分析这个项目的架构，然后重构数据库层，最后写测试"
        )
        assert isinstance(result, bool)

    def test_get_last_user_request(self):
        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
            {"role": "user", "content": "帮我写代码"},
        ]
        last = ResponseHandler.get_last_user_request(messages)
        assert "写代码" in last

    def test_get_last_user_request_empty(self):
        result = ResponseHandler.get_last_user_request([])
        assert isinstance(result, str)


class TestRequestExpectsArtifactPrefixGuard:
    """request_expects_artifact 必须对系统/组织合成「被动通知」前缀返回 False，
    避免汇总轮、root 节点收下属交付时命中正文中的『文件/附件/写一份/openakita-promotion-plan.md』
    等关键词被误判为需要附件交付，进而触发 verify_incomplete + emit task_failed。"""

    def test_summary_round_does_not_expect_artifact(self):
        msg = (
            "[用户指令最终汇总] 你最初接到的用户指令所触发的所有委派任务均已关闭。"
            "请基于下级各自交付的成果，向用户输出一份完整的最终汇总。"
        )
        assert request_expects_artifact(msg) is False

    def test_system_prefix_does_not_expect_artifact(self):
        assert request_expects_artifact("[系统] 请立即调用 write_file 写一份文件") is False

    def test_real_user_artifact_request_still_detected(self):
        assert request_expects_artifact("帮我写一份openakita的宣传计划") is True

    def test_root_receives_task_delivered_does_not_expect_artifact(self):
        """回归 2026-04-28 13:42:53 _134209 失败链：
        editor-in-chief 收到 seo-opt 的 [收到任务交付]，正文里包含
        『文件名 openakita-promotion-plan.md』『写一份』等关键字，
        旧逻辑会强行要求附件交付 → INCOMPLETE → root emit task_failed
        → 用户看到「主编 未完成 任务验证未通过」噪音卡片。"""
        msg = (
            "[收到任务交付] 来自 seo-opt [任务链: 2026-04-28T0]:\n"
            "任务交付: # OpenAkita SEO 优化建议交付物\n"
            "...产出文件：openakita-promotion-plan.md，请帮我写一份汇总..."
        )
        assert request_expects_artifact(msg) is False

    def test_task_rejected_notification_does_not_expect_artifact(self):
        """[任务被打回] 也应豁免——它是被动收到的通知，
        让被打回方去补做交付，自身回复无需附件。"""
        msg = "[任务被打回] 来自 editor-in-chief：你的交付不合规，请重写文件"
        assert request_expects_artifact(msg) is False

    def test_handshake_notification_does_not_expect_artifact(self):
        msg = "[收到握手请求] 来自 planner：可以协作上传文件吗？"
        assert request_expects_artifact(msg) is False

    def test_active_task_assignment_still_expects_artifact(self):
        """[收到任务] 是子节点真正接到的工作派单，必须保留 verify。
        正文里写了『写一份』就该按 expects_artifact=True 处理。"""
        msg = (
            "[收到任务] 来自 editor-in-chief [任务链: 2026-04-28T0]:\n"
            "请帮我写一份openakita的SEO优化文档"
        )
        assert request_expects_artifact(msg) is True


class TestVerifyTaskCompletionPrefixBypass:
    """verify_task_completion 在 bypass 检查后增加的系统前缀兜底，
    保证即使上游 is_summary_round 计算失误，汇总轮也不会被误判 INCOMPLETE。"""

    @pytest.mark.asyncio
    async def test_summary_round_user_request_bypasses_verify(self):
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request=(
                "[用户指令最终汇总] 你最初接到的用户指令所触发的所有委派任务均已关闭。"
            ),
            assistant_response="（任意纯文本汇总，无附件）",
            executed_tools=["read_file"],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=False,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_system_prefix_user_request_bypasses_verify(self):
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request="[系统] 请立即继续推进 plan",
            assistant_response="OK，已继续",
            executed_tools=[],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=False,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_supervisor_bypass_path_still_works(self):
        """老的 supervisor bypass 路径不能被新增的兜底破坏。"""
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request="帮我写一份文件",
            assistant_response="...",
            executed_tools=[],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=True,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_root_receiving_task_delivered_bypasses_verify(self):
        """回归 2026-04-28 13:42:53 _134209 失败链：root 节点收到下属的
        [收到任务交付] 时，verify 必须直接 bypass return True，否则会被判
        INCOMPLETE → emit task_failed → 用户看到「任务验证未通过」噪音卡片。"""
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request=(
                "[收到任务交付] 来自 seo-opt [任务链: 2026-04-28T0]:\n"
                "任务交付: # OpenAkita SEO 优化建议交付物\n"
                "...产出文件：openakita-promotion-plan.md..."
            ),
            assistant_response="## OpenAkita 宣传计划汇总\n下属交付已收到，已综合输出汇总文档。",
            executed_tools=["org_accept_deliverable"],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=False,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_task_rejected_notification_bypasses_verify(self):
        """[任务被打回] 等被动通知前缀也应豁免 verify。"""
        handler = ResponseHandler(brain=None, memory_manager=None)

        is_completed = await handler.verify_task_completion(
            user_request="[任务被打回] 来自 editor-in-chief：附件格式不合规",
            assistant_response="收到，下次会注意",
            executed_tools=[],
            delivery_receipts=[],
            tool_results=[],
            conversation_id=None,
            bypass=False,
        )

        assert is_completed is True

    @pytest.mark.asyncio
    async def test_active_task_assignment_does_not_bypass_verify(self):
        """[收到任务] 是子节点真正接到的工作派单，**不应**走前缀 bypass。
        这里只断言「不会因前缀短路返回 True」——后续的真正 verify 流程
        需要 brain 实例，单测不覆盖到 LLM 调用，所以用足够多的工具结果
        让前期 deterministic 检查不会硬挡，这样如果命中前缀 bypass 会
        立刻返回 True，否则会因为 brain=None 在 LLM 调用阶段抛错。"""
        handler = ResponseHandler(brain=None, memory_manager=None)

        msg = (
            "[收到任务] 来自 editor-in-chief [任务链: 2026-04-28T0]:\n"
            "请帮我写一份openakita的SEO优化文档"
        )
        # 确认前缀豁免名单**不包含**「[收到任务]」。
        assert not msg.lstrip().startswith(handler._SYSTEM_REQUEST_PREFIXES)

