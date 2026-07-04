"""JSON-file checkpoint backend.

Used in developer-mode debugging (and in some integration tests) where
a human wants to read a checkpoint with their eyes. The on-disk layout
mirrors ADR-0010:

    <root>/
        <command_id>/
            <checkpoint_id>.json

Each file is a pretty-printed JSON document with the same schema
envelope as the SQLite backend; they are interchangeable. Files are
written atomically via a temp-and-rename swap so a crash mid-write
never leaves a half-written checkpoint on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from ..checkpoint import (
    BaseCheckpointer,
    Checkpoint,
    CheckpointId,
    CheckpointMetadata,
    CommandId,
    decode_state,
    encode_state,
)

__all__ = ["JsonFileCheckpointer"]


class JsonFileCheckpointer(BaseCheckpointer):
    """Per-checkpoint JSON file backend rooted at ``root_dir``."""

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _command_dir(self, command_id: CommandId) -> Path:
        return self._root / command_id

    def _checkpoint_path(
        self, command_id: CommandId, checkpoint_id: CheckpointId
    ) -> Path:
        return self._command_dir(command_id) / f"{checkpoint_id}.json"

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def aput(self, checkpoint: Checkpoint) -> CheckpointMetadata:
        return await asyncio.to_thread(self._put_sync, checkpoint)

    def _put_sync(self, checkpoint: Checkpoint) -> CheckpointMetadata:
        m = checkpoint.metadata
        cmd_dir = self._command_dir(m.command_id)
        cmd_dir.mkdir(parents=True, exist_ok=True)
        # We canonicalise through encode_state to share the schema
        # validation path with the other backends, then pretty-print
        # back out for human consumption.
        validated = decode_state(encode_state(checkpoint.state))
        document = {
            "metadata": m.to_jsonable(),
            "state": validated,
            "pending_writes": list(checkpoint.pending_writes),
        }
        target = self._checkpoint_path(m.command_id, m.checkpoint_id)
        self._atomic_write_json(target, document)
        return m

    @staticmethod
    def _atomic_write_json(path: Path, payload: dict) -> None:
        """Write ``payload`` to ``path`` atomically.

        The temp file is created in the same directory as ``path`` so
        the rename stays on the same filesystem and is therefore
        atomic. We deliberately do not use shutil.move here because
        cross-device fall-back would break the atomicity guarantee.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            with __import__("contextlib").suppress(FileNotFoundError):
                os.unlink(tmp_name)
            raise

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def aget(self, checkpoint_id: CheckpointId) -> Checkpoint | None:
        return await asyncio.to_thread(self._get_sync, checkpoint_id)

    def _get_sync(self, checkpoint_id: CheckpointId) -> Checkpoint | None:
        # We do not store a reverse index; finding by id alone means a
        # single directory walk. JSON-file backend is for debug, not
        # production hot path.
        for cmd_dir in self._root.iterdir():
            if not cmd_dir.is_dir():
                continue
            target = cmd_dir / f"{checkpoint_id}.json"
            if target.is_file():
                return self._load_file(target)
        return None

    async def aget_latest(self, command_id: CommandId) -> Checkpoint | None:
        return await asyncio.to_thread(self._get_latest_sync, command_id)

    def _get_latest_sync(self, command_id: CommandId) -> Checkpoint | None:
        cmd_dir = self._command_dir(command_id)
        if not cmd_dir.is_dir():
            return None
        files = sorted(p for p in cmd_dir.iterdir() if p.suffix == ".json")
        if not files:
            return None
        return self._load_file(files[-1])

    async def alist(
        self,
        command_id: CommandId,
        *,
        limit: int = 64,
    ) -> AsyncIterator[CheckpointMetadata]:
        cmd_dir = self._command_dir(command_id)
        if not cmd_dir.is_dir():
            return
        files = sorted(p for p in cmd_dir.iterdir() if p.suffix == ".json")
        for path in reversed(files[-limit:]):
            ck = await asyncio.to_thread(self._load_file, path)
            yield ck.metadata

    @staticmethod
    def _load_file(path: Path) -> Checkpoint:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # decode_state validates the envelope.
        state = decode_state(encode_state(data["state"]))
        return Checkpoint(
            metadata=CheckpointMetadata.from_jsonable(data["metadata"]),
            state=state,
            pending_writes=list(data.get("pending_writes", [])),
        )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def adelete_command(self, command_id: CommandId) -> int:
        return await asyncio.to_thread(self._delete_command_sync, command_id)

    def _delete_command_sync(self, command_id: CommandId) -> int:
        cmd_dir = self._command_dir(command_id)
        if not cmd_dir.is_dir():
            return 0
        n = 0
        for p in list(cmd_dir.iterdir()):
            if p.suffix == ".json":
                p.unlink()
                n += 1
        try:
            cmd_dir.rmdir()
        except OSError:
            # Directory not empty (e.g. user dropped notes); leave it.
            pass
        return n
