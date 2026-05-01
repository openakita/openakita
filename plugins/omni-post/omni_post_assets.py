"""omni-post asset pipeline — chunked upload, MD5 dedup, ffprobe, thumbnails.

The HTTP layer (see ``plugin.py``) exposes three endpoints that drive
this module:

    POST  /upload/init      — returns an ``upload_id`` + expected total
                              chunk count for a given filename/filesize.
    PUT   /upload/chunk     — append one chunk (``chunk_index`` starts
                              at 0) to the in-progress upload.
    POST  /upload/finalize  — after all chunks arrive, compute the MD5
                              of the reassembled file, probe its
                              metadata with ffprobe, generate a
                              thumbnail, and INSERT an ``assets`` row.

MD5 dedup
---------

The finalize step looks up the md5 in the ``assets`` table first. When a
matching row already exists we "秒传"-return its existing ``asset_id``
without writing a second copy — this mirrors the behaviour users expect
from network drives and prevents runaway disk use when the same 500 MB
video is re-uploaded.

Missing ffmpeg / ffprobe
------------------------

Both binaries are *system-level* dependencies and not in
``requirements.txt``. When either is absent we still allow the upload
to finish, but we downgrade ``assets.duration_ms`` / width / height /
thumb_path to ``NULL`` and record the reason once per process. The
publish pipeline surfaces this via :class:`ErrorKind.dependency` when a
platform adapter specifically needs the metadata (e.g. YouTube requires
a thumbnail; Douyin does not).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("openakita.plugins.omni-post")


_DEFAULT_CHUNK_BYTES = 5 * 1024 * 1024


@dataclass
class UploadSession:
    """In-flight upload state held in memory between chunk requests.

    We deliberately keep this in-memory (not in SQLite) because the
    uploads_dir is the authoritative source of truth — if the host is
    restarted mid-upload the partial file is discarded on sweep
    (see :meth:`UploadPipeline.sweep_stale_uploads`).
    """

    upload_id: str
    filename: str
    filesize: int
    kind: str  # video / image / audio / cover
    tmp_dir: Path
    total_chunks: int
    received_chunks: set[int] = field(default_factory=set)
    md5_expected: str | None = None  # optional client-provided hint

    @property
    def is_complete(self) -> bool:
        return len(self.received_chunks) == self.total_chunks


class UploadPipeline:
    """Orchestrates chunked upload, dedup, ffprobe, thumbnailing."""

    def __init__(
        self,
        *,
        uploads_dir: Path,
        thumbs_dir: Path,
        task_manager,  # OmniPostTaskManager (duck-typed)
        chunk_bytes: int = _DEFAULT_CHUNK_BYTES,
    ) -> None:
        self._uploads = Path(uploads_dir)
        self._thumbs = Path(thumbs_dir)
        self._uploads.mkdir(parents=True, exist_ok=True)
        self._thumbs.mkdir(parents=True, exist_ok=True)
        self._task_manager = task_manager
        self._chunk_bytes = int(chunk_bytes)
        self._sessions: dict[str, UploadSession] = {}

        self._ffprobe_bin: str | None = None
        self._ffmpeg_bin: str | None = None
        self.refresh_system_bins()

    # ── Capability probe ────────────────────────────────────────────

    def refresh_system_bins(self) -> dict[str, str | None]:
        """Re-read ffmpeg/ffprobe from PATH after an in-plugin install."""

        self._ffprobe_bin = shutil.which("ffprobe")
        self._ffmpeg_bin = shutil.which("ffmpeg")
        if self._ffprobe_bin is None:
            logger.warning("omni-post: ffprobe not found in PATH — uploads will skip metadata")
        if self._ffmpeg_bin is None:
            logger.warning("omni-post: ffmpeg not found in PATH — uploads will skip thumbnails")
        return {"ffmpeg": self._ffmpeg_bin, "ffprobe": self._ffprobe_bin}

    def ffmpeg_available(self) -> bool:
        return self._ffmpeg_bin is not None

    def ffprobe_available(self) -> bool:
        return self._ffprobe_bin is not None

    # ── Chunked upload endpoints ────────────────────────────────────

    async def init_upload(
        self,
        *,
        filename: str,
        filesize: int,
        kind: str,
        md5_hint: str | None = None,
    ) -> dict:
        """Open a new chunked upload session.

        Returns a JSON dict the client can stash and replay on future
        chunk PUTs: ``{upload_id, total_chunks, chunk_bytes}``.

        When the client supplies ``md5_hint`` and that md5 is already in
        the ``assets`` table, we short-circuit with
        ``{deduped: True, asset_id: ...}`` so the UI can skip the chunked
        upload loop entirely — this is the "秒传" fast-path the spec calls
        out in §20 "同 MD5 素材已存在".
        """

        if filesize <= 0:
            raise ValueError("filesize must be positive")
        if kind not in {"video", "image", "audio", "cover"}:
            raise ValueError(f"unknown asset kind: {kind!r}")

        if md5_hint:
            existing = await self._task_manager.find_asset_by_md5(md5_hint)
            if existing is not None:
                return {
                    "deduped": True,
                    "asset_id": existing["id"],
                    "md5": md5_hint,
                    "kind": existing["kind"],
                    "filesize": existing["filesize"],
                    "storage_path": existing["storage_path"],
                    "thumb_path": existing.get("thumb_path"),
                }

        upload_id = f"upl_{uuid.uuid4().hex[:16]}"
        tmp_dir = self._uploads / "_tmp" / upload_id
        tmp_dir.mkdir(parents=True, exist_ok=True)
        total_chunks = max(1, (filesize + self._chunk_bytes - 1) // self._chunk_bytes)
        self._sessions[upload_id] = UploadSession(
            upload_id=upload_id,
            filename=filename,
            filesize=int(filesize),
            kind=kind,
            tmp_dir=tmp_dir,
            total_chunks=total_chunks,
            md5_expected=md5_hint,
        )
        return {
            "upload_id": upload_id,
            "total_chunks": total_chunks,
            "chunk_bytes": self._chunk_bytes,
        }

    def write_chunk(
        self,
        *,
        upload_id: str,
        chunk_index: int,
        payload: bytes,
    ) -> dict:
        """Persist one chunk of the upload.

        Chunks are written atomically (``.part`` rename) so a crash mid
        write doesn't leave a partial file on disk.
        """

        session = self._sessions.get(upload_id)
        if session is None:
            raise KeyError(f"upload session not found: {upload_id}")
        if chunk_index < 0 or chunk_index >= session.total_chunks:
            raise ValueError(f"chunk_index {chunk_index} out of range [0, {session.total_chunks})")

        chunk_path = session.tmp_dir / f"chunk_{chunk_index:06d}.part"
        tmp_path = chunk_path.with_suffix(".tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(chunk_path)
        session.received_chunks.add(chunk_index)
        return {
            "upload_id": upload_id,
            "chunk_index": chunk_index,
            "received": len(session.received_chunks),
            "total": session.total_chunks,
            "done": session.is_complete,
        }

    async def finalize(
        self,
        *,
        upload_id: str,
        tags: list[str] | None = None,
    ) -> dict:
        """Reassemble, dedup, probe, thumbnail, and insert an asset row.

        Returns ``{"asset_id": ..., "deduped": bool, ...}``.
        """

        session = self._sessions.pop(upload_id, None)
        if session is None:
            raise KeyError(f"upload session not found: {upload_id}")
        if not session.is_complete:
            raise RuntimeError(
                f"cannot finalize {upload_id}: {len(session.received_chunks)} "
                f"of {session.total_chunks} chunks received"
            )

        # Reassemble + MD5 in one pass to avoid reading the file twice.
        md5_hasher = hashlib.md5()  # noqa: S324 - dedup not security
        target_dir = self._uploads / f"{session.kind}s"
        target_dir.mkdir(parents=True, exist_ok=True)
        stage_path = target_dir / f".{upload_id}.staged"

        def _reassemble() -> None:
            with stage_path.open("wb") as out:
                for i in range(session.total_chunks):
                    chunk_path = session.tmp_dir / f"chunk_{i:06d}.part"
                    data = chunk_path.read_bytes()
                    md5_hasher.update(data)
                    out.write(data)

        await asyncio.to_thread(_reassemble)
        md5 = md5_hasher.hexdigest()

        # Dedup — if md5 is already in the table, drop the staged file
        # and return the existing row. "秒传" by design: saves disk,
        # saves the user a second ffprobe run.
        existing = await self._task_manager.find_asset_by_md5(md5)
        if existing is not None:
            try:
                stage_path.unlink(missing_ok=True)
                shutil.rmtree(session.tmp_dir, ignore_errors=True)
            except OSError:
                pass
            return {
                "asset_id": existing["id"],
                "deduped": True,
                "md5": md5,
                "kind": existing["kind"],
                "filesize": existing["filesize"],
                "storage_path": existing["storage_path"],
                "thumb_path": existing.get("thumb_path"),
            }

        # Rename staged → final by md5 (eliminates filename collisions
        # between different users uploading the same name).
        suffix = Path(session.filename).suffix.lower() or _default_suffix(session.kind)
        final_path = target_dir / f"{md5}{suffix}"
        if final_path.exists():
            # Concurrent finalize of the same file: trust the existing
            # and discard ours.
            stage_path.unlink(missing_ok=True)
        else:
            stage_path.rename(final_path)
        shutil.rmtree(session.tmp_dir, ignore_errors=True)

        # Metadata + thumbnail (best-effort).
        meta = await self._probe_metadata(final_path, session.kind)
        thumb_rel = await self._make_thumbnail(final_path, md5, session.kind)

        asset_id = await self._task_manager.create_asset(
            kind=session.kind,
            filename=session.filename,
            filesize=session.filesize,
            md5=md5,
            storage_path=str(final_path),
            duration_ms=meta.get("duration_ms"),
            width=meta.get("width"),
            height=meta.get("height"),
            codec=meta.get("codec"),
            bitrate=meta.get("bitrate"),
            thumb_path=thumb_rel,
            tags=tags or [],
        )
        return {
            "asset_id": asset_id,
            "deduped": False,
            "md5": md5,
            "kind": session.kind,
            "filesize": session.filesize,
            "storage_path": str(final_path),
            "thumb_path": thumb_rel,
            **meta,
        }

    def sweep_stale_uploads(self, *, older_than_seconds: int = 3600) -> int:
        """Drop partial uploads whose tmp dir is older than ``older_than``.

        Called by :meth:`OmniPostPipeline.on_startup` to reclaim space
        if the host restarted mid-upload. Returns the number of sessions
        evicted.
        """

        import time

        cutoff = time.time() - float(older_than_seconds)
        tmp_root = self._uploads / "_tmp"
        if not tmp_root.exists():
            return 0
        removed = 0
        for child in tmp_root.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
            except OSError:
                continue
        # Drop in-memory sessions whose tmp_dir vanished.
        stale = [
            uid for uid, session in list(self._sessions.items()) if not session.tmp_dir.exists()
        ]
        for uid in stale:
            self._sessions.pop(uid, None)
        return removed

    # ── Private: ffprobe / ffmpeg wrappers ──────────────────────────

    async def _probe_metadata(self, path: Path, kind: str) -> dict:
        if self._ffprobe_bin is None:
            return {}
        cmd = [
            self._ffprobe_bin,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("ffprobe failed for %s: %s", path, e)
            return {}
        if result.returncode != 0:
            logger.warning("ffprobe returned %s for %s", result.returncode, path)
            return {}
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return {}
        return _extract_meta(data, kind)

    async def _make_thumbnail(self, path: Path, md5: str, kind: str) -> str | None:
        if kind not in {"video", "cover", "image"}:
            return None
        if kind in {"cover", "image"}:
            # Use the image itself as its own thumbnail — saves disk.
            return str(path)
        if self._ffmpeg_bin is None:
            return None
        thumb_path = self._thumbs / f"{md5}.webp"
        if thumb_path.exists():
            return str(thumb_path)
        cmd = [
            self._ffmpeg_bin,
            "-y",
            "-i",
            str(path),
            "-ss",
            "00:00:01.000",
            "-vframes",
            "1",
            "-vf",
            "scale='min(480,iw)':-2",
            str(thumb_path),
        ]
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            logger.warning("ffmpeg thumbnail failed for %s: %s", path, e)
            return None
        if result.returncode != 0:
            logger.warning("ffmpeg returned %s for %s", result.returncode, path)
            return None
        return str(thumb_path)


def _default_suffix(kind: str) -> str:
    return {
        "video": ".mp4",
        "image": ".jpg",
        "audio": ".mp3",
        "cover": ".jpg",
    }.get(kind, "")


def _extract_meta(data: dict, kind: str) -> dict:
    out: dict = {}
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []

    # Duration lives in format.duration; fall back to the first stream's.
    dur = fmt.get("duration")
    if dur is None and streams:
        dur = streams[0].get("duration")
    if dur is not None:
        try:
            out["duration_ms"] = int(float(dur) * 1000)
        except (TypeError, ValueError):
            pass

    br = fmt.get("bit_rate")
    if br is not None:
        try:
            out["bitrate"] = int(br)
        except (TypeError, ValueError):
            pass

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is not None:
        if "width" in video:
            out["width"] = int(video["width"])
        if "height" in video:
            out["height"] = int(video["height"])
        if "codec_name" in video:
            out["codec"] = str(video["codec_name"])

    if kind == "audio":
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if audio is not None and "codec_name" in audio:
            out["codec"] = str(audio["codec_name"])

    return out


__all__ = [
    "UploadPipeline",
    "UploadSession",
]
