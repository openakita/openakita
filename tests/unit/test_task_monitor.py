"""L1 Unit Tests: TaskMonitor lifecycle and metrics."""

import time

from openakita.core.task_monitor import (
    IterationRecord,
    RetrospectRecord,
    RetrospectStorage,
    TaskMetrics,
    TaskMonitor,
    ToolCallRecord,
)


class TestTaskMonitorLifecycle:
    def test_create_monitor(self):
        tm = TaskMonitor(task_id="t1", description="Test task")
        assert tm is not None

    def test_start_and_complete(self):
        tm = TaskMonitor(task_id="t2", description="Quick task")
        tm.start(model="gpt-4")
        metrics = tm.complete(success=True, response="Done")
        assert isinstance(metrics, TaskMetrics)
        assert metrics.success is True
        assert metrics.task_id == "t2"

    def test_iteration_tracking(self):
        tm = TaskMonitor(task_id="t3", description="Multi-iter")
        tm.start(model="claude-3")
        tm.begin_iteration(1, model="claude-3")
        tm.end_iteration(llm_response_preview="thinking...")
        tm.begin_iteration(2, model="claude-3")
        tm.end_iteration(llm_response_preview="done")
        metrics = tm.complete(success=True, response="Final")
        assert metrics.total_iterations == 2


class TestToolCallRecording:
    def test_record_tool_call(self):
        tm = TaskMonitor(task_id="t4", description="Tool test")
        tm.start(model="gpt-4")
        tm.begin_iteration(1, model="gpt-4")
        tm.record_tool_call(
            tool_name="read_file",
            tool_input={"path": "/test.txt"},
            result="file contents",
            success=True,
            duration_ms=150,
        )
        tm.end_iteration()

    def test_record_tool_call_accepts_legacy_elapsed_success_args(self):
        tm = TaskMonitor(task_id="t4-legacy", description="Legacy tool test")
        tm.start(model="gpt-4")
        tm.begin_iteration(1, model="gpt-4")

        tm.record_tool_call("read_file", {"path": "/test.txt"}, 0.25, True)
        tm.end_iteration()

        [record] = tm.metrics.iterations[0].tool_calls
        assert record.name == "read_file"
        assert record.duration_ms == 250
        assert record.success is True

    def test_begin_end_tool_call(self):
        tm = TaskMonitor(task_id="t5", description="Tool begin/end")
        tm.start(model="gpt-4")
        tm.begin_iteration(1, model="gpt-4")
        tm.begin_tool_call("web_search", {"query": "test"})
        tm.end_tool_call("results found", success=True)
        tm.end_iteration()


class TestModelSwitch:
    def test_switch_model(self):
        tm = TaskMonitor(task_id="t6", description="Switch test")
        tm.start(model="gpt-4")
        tm.switch_model("claude-3", reason="timeout", reset_context=True)
        assert tm.current_model == "claude-3"

    def test_metrics_after_switch(self):
        tm = TaskMonitor(task_id="t7", description="Switch metrics")
        tm.start(model="gpt-4")
        tm.switch_model("claude-3", reason="error")
        metrics = tm.complete(success=True, response="ok")
        assert metrics.model_switched is True


class TestTimeout:
    def test_elapsed_seconds(self):
        tm = TaskMonitor(task_id="t8", description="Elapsed")
        tm.start(model="gpt-4")
        assert tm.elapsed_seconds >= 0

    def test_is_timeout(self):
        tm = TaskMonitor(task_id="t9", description="Timeout", timeout_seconds=0)
        tm.start(model="gpt-4")
        assert isinstance(tm.is_timeout, bool)

    def test_zero_timeout_disables_progress_timeout(self):
        tm = TaskMonitor(
            task_id="t9-zero",
            description="Zero timeout means disabled",
            timeout_seconds=0,
            fallback_model="backup-model",
            retry_before_switch=1,
        )
        tm.start(model="primary-model")

        # Simulate an old progress timestamp; timeout_seconds=0 must still be disabled.
        tm._last_progress_time = time.time() - 3600
        tm.begin_iteration(1, model="primary-model")

        assert tm.timeout_retry_count == 0
        assert tm.is_timeout is False
        assert tm.should_switch_model is False
        assert tm.current_model == "primary-model"

    def test_single_endpoint_timeout_is_not_logged_as_error(self, caplog):
        """单端点部署：超时重试耗尽后没有 fallback，应只记录 INFO 而非 ERROR。

        旧实现假设至少 2 个端点，一旦 fallback_model 为空就打 ERROR；
        现在单端点是合法配置，不应在日志里被当成错误。
        """
        import logging

        tm = TaskMonitor(
            task_id="t9-single",
            description="Single-endpoint timeout",
            timeout_seconds=1,
            fallback_model="",
            retry_before_switch=1,
        )
        tm.start(model="only-model")

        # 直接驱动 _handle_timeout，绕过 begin_iteration 里的 _touch_progress 重置。
        # 模拟"超时重试已用尽 + 没有 fallback"的瞬间状态。
        with caplog.at_level(logging.INFO, logger="openakita.core.task_monitor"):
            tm._handle_timeout()

        assert tm.is_timeout is True
        # fallback_model 为空时不应调用 switch_model，current_model 保持不变。
        assert tm.current_model == "only-model"

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, (
            f"单端点超时不应当作 ERROR 报告，实际看到: {[r.getMessage() for r in error_records]}"
        )
        info_messages = " ".join(
            r.getMessage()
            for r in caplog.records
            if r.name == "openakita.core.task_monitor" and r.levelno == logging.INFO
        )
        assert "single-endpoint" in info_messages.lower(), (
            f"期望出现单端点 INFO 提示，实际只看到: {info_messages!r}"
        )


class TestRetry:
    def test_record_error_and_retry(self):
        tm = TaskMonitor(task_id="t10", description="Retry")
        tm.start(model="gpt-4")
        can_retry = tm.record_error("connection timeout")
        assert isinstance(can_retry, bool)
        assert tm.retry_count >= 1
        assert tm.last_error == "connection timeout"


class TestRetrospect:
    def test_retrospect_context(self):
        tm = TaskMonitor(task_id="t11", description="Retro")
        tm.start(model="gpt-4")
        tm.begin_iteration(1, model="gpt-4")
        tm.end_iteration(llm_response_preview="step 1")
        ctx = tm.get_retrospect_context()
        assert isinstance(ctx, str)


class TestRetrospectStorage:
    def test_create_storage(self, tmp_path):
        storage = RetrospectStorage(storage_dir=tmp_path / "retrospect")
        assert storage is not None

    def test_save_and_load(self, tmp_path):
        storage = RetrospectStorage(storage_dir=tmp_path / "retrospect")
        record = RetrospectRecord(
            task_id="t1",
            session_id="s1",
            description="Test",
            duration_seconds=10.0,
            iterations=2,
            model_switched=False,
            initial_model="gpt-4",
            final_model="gpt-4",
            retrospect_result="All good",
        )
        saved = storage.save(record)
        assert saved is True


class TestDataclasses:
    def test_tool_call_record(self):
        r = ToolCallRecord(
            name="read_file",
            input_summary="path=/test",
            output_summary="content",
            duration_ms=100,
            success=True,
        )
        assert r.name == "read_file"

    def test_iteration_record(self):
        r = IterationRecord(iteration=1)
        assert r.iteration == 1
        assert r.tool_calls == []

    def test_task_metrics_summary(self):
        m = TaskMetrics(task_id="t", description="d")
        summary = m.to_summary()
        assert isinstance(summary, str)
