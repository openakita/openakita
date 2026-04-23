from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from openakita.agents.cli_providers import _common

_STREAM_TIMEOUT = 5.0
_PROCESS_WAIT_TIMEOUT = 2.0


async def _close_process_transport(proc: asyncio.subprocess.Process) -> None:
    transport = getattr(proc, "_transport", None)
    if transport is not None:
        transport.close()
    await asyncio.sleep(0.01)


async def _cleanup_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=_PROCESS_WAIT_TIMEOUT)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=_PROCESS_WAIT_TIMEOUT)
    await _close_process_transport(proc)


async def _consume_stream(
    argv: list[str],
    cwd: Path,
    *,
    on_stderr: Callable[[bytes], None] | None = None,
    timeout: float = _STREAM_TIMEOUT,
) -> tuple[list[bytes], asyncio.subprocess.Process]:
    spawned: dict[str, asyncio.subprocess.Process] = {}
    lines: list[bytes] = []

    async def consume() -> None:
        async for line in _common.stream_cli_subprocess(
            argv,
            {},
            cwd,
            asyncio.Event(),
            on_spawn=lambda proc: spawned.setdefault("proc", proc),
            on_stderr=on_stderr,
        ):
            lines.append(line)

    try:
        await asyncio.wait_for(consume(), timeout=timeout)
    except BaseException:
        proc = spawned.get("proc")
        if proc is not None:
            await _cleanup_process(proc)
        raise
    return lines, spawned["proc"]


async def _wait_for_process(proc: asyncio.subprocess.Process) -> None:
    await asyncio.wait_for(proc.wait(), timeout=_PROCESS_WAIT_TIMEOUT)
    await _close_process_transport(proc)


async def _terminate_pid_from_file(pid_file: Path) -> None:
    if not pid_file.exists():
        return
    pid_text = pid_file.read_text().strip()
    if not pid_text:
        return
    pid = int(pid_text)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _assert_bounded_stderr(
    stderr_chunks: list[bytes],
    *,
    expected_total: int | None = None,
) -> None:
    total = 0
    for chunk in stderr_chunks:
        remaining = _common._CLI_STDERR_BUFFER_LIMIT - total
        assert len(chunk) <= _common._CLI_STDERR_CHUNK_SIZE
        assert len(chunk) <= remaining
        total += len(chunk)
    assert total <= _common._CLI_STDERR_BUFFER_LIMIT
    if expected_total is not None:
        assert total == expected_total


@pytest.mark.asyncio
async def test_stream_cli_subprocess_drains_large_stderr(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\nsys.stderr.write('x' * (2 * 1024 * 1024))\nsys.stderr.flush()\nprint('ok')\n"
    )

    stderr_chunks: list[bytes] = []
    lines, proc = await _consume_stream(
        [sys.executable, str(script)],
        tmp_path,
        on_stderr=stderr_chunks.append,
    )

    await _wait_for_process(proc)
    assert lines == [b"ok\n"]
    _assert_bounded_stderr(
        stderr_chunks,
        expected_total=_common._CLI_STDERR_BUFFER_LIMIT,
    )
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_stream_cli_subprocess_drains_stderr_after_stdout_eof(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('ok\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.close()\n"
        "sys.stderr.write('x' * (2 * 1024 * 1024))\n"
        "sys.stderr.flush()\n"
    )

    stderr_chunks: list[bytes] = []
    lines, proc = await _consume_stream(
        [sys.executable, str(script)],
        tmp_path,
        on_stderr=stderr_chunks.append,
    )

    await _wait_for_process(proc)
    assert lines == [b"ok\n"]
    _assert_bounded_stderr(
        stderr_chunks,
        expected_total=_common._CLI_STDERR_BUFFER_LIMIT,
    )
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_stream_cli_subprocess_returns_when_process_exits_with_inherited_stderr(tmp_path):
    script = tmp_path / "child.py"
    helper_pid_file = tmp_path / "helper.pid"
    helper_sentinel = tmp_path / "helper.alive"
    script.write_text(
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "pid_path = Path(sys.argv[1])\n"
        "sentinel_path = Path(sys.argv[2])\n"
        "sentinel_path.write_text('alive')\n"
        "helper_code = (\n"
        "    'import signal, sys, time\\n'\n"
        "    'from pathlib import Path\\n'\n"
        "    'sentinel_path = Path(sys.argv[1])\\n'\n"
        "    'def stop(_signum, _frame):\\n'\n"
        "    '    raise SystemExit(0)\\n'\n"
        "    'signal.signal(signal.SIGTERM, stop)\\n'\n"
        "    'deadline = time.monotonic() + 30\\n'\n"
        "    'while sentinel_path.exists() and time.monotonic() < deadline:\\n'\n"
        "    '    time.sleep(0.05)\\n'\n"
        ")\n"
        "helper = subprocess.Popen(\n"
        "    [sys.executable, '-c', helper_code, str(sentinel_path)],\n"
        "    stdin=subprocess.DEVNULL,\n"
        "    stdout=subprocess.DEVNULL,\n"
        "    stderr=sys.stderr,\n"
        ")\n"
        "pid_path.write_text(str(helper.pid))\n"
        "sys.stdout.write('ok\\n')\n"
        "sys.stdout.flush()\n"
    )

    try:
        lines, proc = await _consume_stream(
            [sys.executable, str(script), str(helper_pid_file), str(helper_sentinel)],
            tmp_path,
            timeout=1.0,
        )
    finally:
        helper_sentinel.unlink(missing_ok=True)
        await _terminate_pid_from_file(helper_pid_file)

    await _wait_for_process(proc)
    assert lines == [b"ok\n"]
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_stream_cli_subprocess_keeps_draining_when_stderr_callback_raises(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(
        "import sys\nsys.stderr.write('x' * (2 * 1024 * 1024))\nsys.stderr.flush()\nprint('ok')\n"
    )

    calls = 0
    stderr_chunks: list[bytes] = []

    def capture_stderr(chunk: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")
        stderr_chunks.append(chunk)

    lines, proc = await _consume_stream(
        [sys.executable, str(script)],
        tmp_path,
        on_stderr=capture_stderr,
    )

    await _wait_for_process(proc)
    assert lines == [b"ok\n"]
    assert calls > 1
    assert 0 < sum(len(chunk) for chunk in stderr_chunks) < _common._CLI_STDERR_BUFFER_LIMIT
    _assert_bounded_stderr(stderr_chunks)
    assert proc.returncode == 0
