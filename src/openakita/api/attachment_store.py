from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import shutil
import uuid
from contextlib import suppress
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_TEXT_PREVIEW_LIMIT = 32_000
_MAX_DIRECTORY_ENTRIES = 1000
_DEFAULT_CHUNK_LINES = 200
_DEFAULT_SEARCH_RESULTS = 20
_DOCUMENT_MIME_PREFIXES = (
    "text/",
    "application/pdf",
    "application/json",
    "application/xml",
    "application/yaml",
)
_DOCUMENT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".csv",
    ".py", ".ts", ".tsx", ".js", ".jsx", ".html", ".css", ".scss",
    ".java", ".go", ".rs", ".sh", ".bat", ".ps1", ".toml", ".ini",
    ".cfg", ".log", ".sql",
}
_TEXT_EXTRACTABLE_MIME_TYPES = {
    "application/json",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/javascript",
}


def _get_default_root() -> Path:
    try:
        from openakita.config import settings

        base = settings.data_dir
    except Exception:
        import os

        root = os.environ.get("OPENAKITA_ROOT", "").strip()
        base = Path(root) if root else Path.home() / ".openakita"
    path = Path(base) / "attachments"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_conversation_id(conversation_id: str | None) -> str:
    raw = (conversation_id or "shared").strip() or "shared"
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)


def _guess_mime_type(filename: str, explicit_mime: str | None = None) -> str:
    if explicit_mime:
        return explicit_mime
    guessed = mimetypes.guess_type(filename)[0]
    return guessed or "application/octet-stream"


def classify_attachment_type(
    mime_type: str | None,
    filename: str = "",
    hinted_type: str | None = None,
) -> str:
    if hinted_type == "directory":
        return "directory"
    mime = (mime_type or "").lower()
    if hinted_type in {"image", "video", "voice", "audio", "document", "file"}:
        if hinted_type == "audio":
            return "voice"
        return hinted_type
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "voice"
    ext = Path(filename).suffix.lower()
    if mime in _DOCUMENT_MIME_PREFIXES or any(
        mime.startswith(prefix) for prefix in ("text/",)
    ) or ext in _DOCUMENT_EXTENSIONS:
        return "document"
    return "file"


def _is_text_like(mime_type: str, filename: str) -> bool:
    mime = (mime_type or "").lower()
    ext = Path(filename).suffix.lower()
    return mime.startswith("text/") or mime in _DOCUMENT_MIME_PREFIXES or ext in _DOCUMENT_EXTENSIONS


def _is_text_extractable(mime_type: str, filename: str) -> bool:
    mime = (mime_type or "").lower()
    ext = Path(filename).suffix.lower()
    if mime == "application/pdf":
        return False
    return mime.startswith("text/") or mime in _TEXT_EXTRACTABLE_MIME_TYPES or ext in _DOCUMENT_EXTENSIONS


class AttachmentStore:
    def __init__(self, root: Path | None = None):
        self.root = root or _get_default_root()
        self.records_dir = self.root / "records"
        self.files_dir = self.root / "files"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def _record_path(self, attachment_id: str) -> Path:
        return self.records_dir / f"{attachment_id}.json"

    def _conversation_dir(self, conversation_id: str | None) -> Path:
        path = self.files_dir / _safe_conversation_id(conversation_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_record(self, record: dict) -> dict:
        self._record_path(record["id"]).write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return record

    def get(self, attachment_id: str) -> dict | None:
        record_path = self._record_path(attachment_id)
        if not record_path.exists():
            return None
        try:
            return json.loads(record_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load attachment record %s: %s", attachment_id, exc)
            return None

    def resolve_content_path(self, record: dict) -> Path | None:
        storage_path = record.get("storage_path")
        if not storage_path:
            return None
        path = Path(storage_path)
        return path if path.exists() and path.is_file() else None

    def read_bytes(self, record: dict) -> bytes | None:
        path = self.resolve_content_path(record)
        if not path:
            return None
        try:
            return path.read_bytes()
        except Exception as exc:
            logger.warning("Failed to read attachment bytes %s: %s", record.get("id", ""), exc)
            return None

    def to_data_url(self, record: dict) -> str | None:
        data = self.read_bytes(record)
        if data is None:
            return None
        mime_type = record.get("mime_type") or "application/octet-stream"
        return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"

    def _resolve_record(self, attachment: str | dict | None) -> dict | None:
        if isinstance(attachment, dict):
            return attachment
        if isinstance(attachment, str) and attachment.strip():
            return self.get(attachment.strip())
        return None

    def extract_text(self, attachment: str | dict, *, max_chars: int | None = None) -> str:
        record = self._resolve_record(attachment)
        if not record:
            return ""

        if record.get("type") == "directory":
            listing = "\n".join(str(item) for item in (record.get("entries") or []))
            return listing[:max_chars] if max_chars and listing else listing

        preview = str(record.get("text_preview", "") or "")
        mime_type = str(record.get("mime_type", "") or "")
        name = str(record.get("name", "") or "")
        if not _is_text_extractable(mime_type, name):
            return preview[:max_chars] if max_chars and preview else preview

        data = self.read_bytes(record)
        if data is None:
            return preview[:max_chars] if max_chars and preview else preview

        text = data.decode("utf-8", errors="replace")
        return text[:max_chars] if max_chars and text else text

    @staticmethod
    def _build_snippet(text: str, *, head_chars: int, tail_chars: int) -> str:
        if len(text) <= head_chars + tail_chars + 64:
            return text
        head = text[:head_chars].rstrip()
        tail = text[-tail_chars:].lstrip()
        omitted = max(0, len(text) - len(head) - len(tail))
        return f"{head}\n\n...(中间省略 {omitted} 个字符)...\n\n{tail}"

    def describe_attachment(
        self,
        attachment: str | dict,
        *,
        preview_chars: int = 1600,
        inline_chars: int = 8000,
        snippet_chars: int = 24000,
        snippet_head_chars: int = 2400,
        snippet_tail_chars: int = 1200,
        chunk_lines: int = _DEFAULT_CHUNK_LINES,
    ) -> dict:
        record = self._resolve_record(attachment)
        if not record:
            return {}

        att_id = str(record.get("id", "") or "")
        text = self.extract_text(record)
        text_extractable = bool(text) or _is_text_extractable(
            str(record.get("mime_type", "") or ""),
            str(record.get("name", "") or ""),
        )
        line_count = text.count("\n") + 1 if text else 0
        chunk_count = ((line_count - 1) // max(1, chunk_lines) + 1) if line_count else 0

        mode = "summary"
        inline_text = ""
        snippet_text = ""
        if record.get("type") == "directory":
            mode = "directory"
        elif text:
            if len(text) <= inline_chars:
                mode = "inline"
                inline_text = text
            elif len(text) <= snippet_chars:
                mode = "snippet"
                snippet_text = self._build_snippet(
                    text,
                    head_chars=snippet_head_chars,
                    tail_chars=snippet_tail_chars,
                )
            else:
                mode = "reference"

        preview = text[:preview_chars] if text else str(record.get("text_preview", "") or "")[:preview_chars]
        return {
            "id": att_id,
            "name": str(record.get("name", "") or "file"),
            "type": str(record.get("type", "") or "file"),
            "mime_type": str(record.get("mime_type", "") or "application/octet-stream"),
            "size": int(record.get("size", 0) or 0),
            "display_path": str(record.get("display_path", "") or ""),
            "entries": list(record.get("entries") or []),
            "text_extractable": text_extractable,
            "text_length": len(text),
            "line_count": line_count,
            "chunk_count": chunk_count,
            "mode": mode,
            "preview": preview,
            "inline_text": inline_text,
            "snippet_text": snippet_text,
            "has_tool_reference": bool(att_id),
            "tool_hint": (
                f'read_attachment_summary(attachment_id="{att_id}") / '
                f'read_attachment_chunk(attachment_id="{att_id}", offset=1) / '
                f'search_attachment(attachment_id="{att_id}", query="关键词")'
                if att_id
                else ""
            ),
        }

    def read_text_chunk(
        self,
        attachment: str | dict,
        *,
        offset: int = 1,
        limit: int = _DEFAULT_CHUNK_LINES,
    ) -> dict:
        record = self._resolve_record(attachment)
        if not record:
            return {"error": "Attachment not found"}

        text = self.extract_text(record)
        if not text:
            return {"error": "Attachment is not text-readable"}

        lines = text.splitlines()
        total_lines = len(lines)
        try:
            offset = max(1, int(offset))
            limit = max(1, int(limit))
        except (TypeError, ValueError):
            offset, limit = 1, _DEFAULT_CHUNK_LINES

        start = offset - 1
        if start >= total_lines:
            return {
                "error": f"offset={offset} out of range",
                "total_lines": total_lines,
            }
        end = min(start + limit, total_lines)
        return {
            "attachment_id": str(record.get("id", "") or ""),
            "name": str(record.get("name", "") or "file"),
            "start_line": start + 1,
            "end_line": end,
            "total_lines": total_lines,
            "content": "\n".join(lines[start:end]),
            "has_more": end < total_lines,
            "next_offset": end + 1 if end < total_lines else None,
        }

    def search_text(
        self,
        attachment: str | dict,
        *,
        query: str,
        case_insensitive: bool = True,
        context_lines: int = 1,
        max_results: int = _DEFAULT_SEARCH_RESULTS,
    ) -> dict:
        record = self._resolve_record(attachment)
        if not record:
            return {"error": "Attachment not found"}

        text = self.extract_text(record)
        if not text:
            return {"error": "Attachment is not text-searchable"}
        if not query:
            return {"error": "Missing query"}

        lines = text.splitlines()
        flags = re.IGNORECASE if case_insensitive else 0
        pattern = re.compile(re.escape(query), flags)
        matches: list[dict] = []
        for idx, line in enumerate(lines):
            if not pattern.search(line):
                continue
            start = max(0, idx - max(0, context_lines))
            end = min(len(lines), idx + max(0, context_lines) + 1)
            matches.append(
                {
                    "line": idx + 1,
                    "text": line,
                    "context_before": lines[start:idx],
                    "context_after": lines[idx + 1:end],
                }
            )
            if len(matches) >= max_results:
                break

        return {
            "attachment_id": str(record.get("id", "") or ""),
            "name": str(record.get("name", "") or "file"),
            "query": query,
            "matches": matches,
            "truncated": len(matches) >= max_results,
        }

    def build_client_attachment(self, record: dict) -> dict:
        return {
            "id": record["id"],
            "type": record["type"],
            "name": record["name"],
            "url": record.get("content_url"),
            "mime_type": record.get("mime_type"),
            "size": record.get("size"),
            "source_path": record.get("source_path"),
            "display_path": record.get("display_path"),
            "entries": record.get("entries"),
            "text_preview": record.get("text_preview"),
        }

    def _build_record(
        self,
        *,
        conversation_id: str | None,
        name: str,
        mime_type: str,
        attachment_type: str,
        size: int = 0,
        storage_path: str = "",
        source_path: str = "",
        display_path: str = "",
        entries: list[str] | None = None,
        text_preview: str = "",
        original_name: str = "",
    ) -> dict:
        attachment_id = uuid.uuid4().hex[:12]
        record = {
            "id": attachment_id,
            "conversation_id": _safe_conversation_id(conversation_id),
            "name": name,
            "original_name": original_name or name,
            "type": attachment_type,
            "mime_type": mime_type,
            "size": size,
            "storage_path": storage_path,
            "source_path": source_path,
            "display_path": display_path,
            "entries": entries or [],
            "text_preview": text_preview,
            "created_at": datetime.now().isoformat(),
            "content_url": f"/api/attachments/{attachment_id}/content" if storage_path else None,
        }
        return record

    def save_uploaded_file(
        self,
        *,
        content: bytes,
        filename: str,
        mime_type: str | None,
        conversation_id: str | None,
        hinted_type: str | None = None,
    ) -> dict:
        name = Path(filename or "file").name or "file"
        resolved_mime = _guess_mime_type(name, mime_type)
        attachment_type = classify_attachment_type(resolved_mime, name, hinted_type)
        ext = Path(name).suffix or mimetypes.guess_extension(resolved_mime) or ""
        attachment_id = uuid.uuid4().hex[:12]
        target = self._conversation_dir(conversation_id) / f"{attachment_id}{ext}"
        target.write_bytes(content)
        text_preview = ""
        if _is_text_extractable(resolved_mime, name):
            try:
                text_preview = content[:_TEXT_PREVIEW_LIMIT].decode("utf-8", errors="replace")
            except Exception:
                text_preview = ""
        record = {
            "id": attachment_id,
            "conversation_id": _safe_conversation_id(conversation_id),
            "name": name,
            "original_name": name,
            "type": attachment_type,
            "mime_type": resolved_mime,
            "size": len(content),
            "storage_path": str(target),
            "source_path": "",
            "display_path": "",
            "entries": [],
            "text_preview": text_preview,
            "created_at": datetime.now().isoformat(),
            "content_url": f"/api/attachments/{attachment_id}/content",
        }
        return self._write_record(record)

    def assign_to_conversation(self, attachment_id: str, conversation_id: str | None) -> dict | None:
        record = self.get(attachment_id)
        if not record:
            return None
        target_conversation = _safe_conversation_id(conversation_id)
        if record.get("conversation_id") == target_conversation:
            return record
        storage_path = record.get("storage_path", "")
        if storage_path:
            src = Path(storage_path)
            if src.exists() and src.is_file():
                target_dir = self._conversation_dir(target_conversation)
                target = target_dir / src.name
                if src.resolve() != target.resolve():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(target))
                    record["storage_path"] = str(target)
        record["conversation_id"] = target_conversation
        return self._write_record(record)

    def import_local_path(self, *, path: str, conversation_id: str | None) -> dict:
        source = Path(path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(path)
        if source.is_dir():
            all_entries = sorted(p.name for p in source.iterdir())
            entries = all_entries
            if len(all_entries) > _MAX_DIRECTORY_ENTRIES:
                entries = all_entries[:_MAX_DIRECTORY_ENTRIES] + [
                    f"... and {len(all_entries) - _MAX_DIRECTORY_ENTRIES} more entries",
                ]
            record = self._build_record(
                conversation_id=conversation_id,
                name=source.name or str(source),
                mime_type="inode/directory",
                attachment_type="directory",
                source_path=str(source),
                display_path=str(source),
                entries=entries,
            )
            return self._write_record(record)

        resolved_mime = _guess_mime_type(source.name, None)
        attachment_type = classify_attachment_type(resolved_mime, source.name, None)
        attachment_id = uuid.uuid4().hex[:12]
        target = self._conversation_dir(conversation_id) / f"{attachment_id}{source.suffix}"
        shutil.copy2(source, target)
        text_preview = ""
        if _is_text_extractable(resolved_mime, source.name):
            try:
                text_preview = source.read_text(encoding="utf-8", errors="replace")[:_TEXT_PREVIEW_LIMIT]
            except Exception:
                text_preview = ""
        record = {
            "id": attachment_id,
            "conversation_id": _safe_conversation_id(conversation_id),
            "name": source.name,
            "original_name": source.name,
            "type": attachment_type,
            "mime_type": resolved_mime,
            "size": source.stat().st_size,
            "storage_path": str(target),
            "source_path": str(source),
            "display_path": str(source),
            "entries": [],
            "text_preview": text_preview,
            "created_at": datetime.now().isoformat(),
            "content_url": f"/api/attachments/{attachment_id}/content",
        }
        return self._write_record(record)

    def delete_conversation(self, conversation_id: str | None) -> int:
        target_conversation = _safe_conversation_id(conversation_id)
        deleted = 0
        for record_path in self.records_dir.glob("*.json"):
            with suppress(Exception):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("conversation_id") != target_conversation:
                    continue
                storage_path = record.get("storage_path", "")
                if storage_path:
                    with suppress(FileNotFoundError):
                        Path(storage_path).unlink()
                record_path.unlink(missing_ok=True)
                deleted += 1
        conv_dir = self.files_dir / target_conversation
        with suppress(Exception):
            shutil.rmtree(conv_dir, ignore_errors=True)
        return deleted

    def cleanup_stale_shared(self, max_age_hours: int = 24) -> int:
        cutoff = datetime.now().timestamp() - max_age_hours * 3600
        deleted = 0
        for record_path in self.records_dir.glob("*.json"):
            with suppress(Exception):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if record.get("conversation_id") != "shared":
                    continue
                created_at = datetime.fromisoformat(record.get("created_at", datetime.now().isoformat()))
                if created_at.timestamp() >= cutoff:
                    continue
                storage_path = record.get("storage_path", "")
                if storage_path:
                    with suppress(FileNotFoundError):
                        Path(storage_path).unlink()
                record_path.unlink(missing_ok=True)
                deleted += 1
        if deleted:
            with suppress(Exception):
                shutil.rmtree(self.files_dir / "shared", ignore_errors=True)
        return deleted


_STORE: AttachmentStore | None = None


def get_attachment_store() -> AttachmentStore:
    global _STORE
    if _STORE is None:
        _STORE = AttachmentStore()
        _STORE.cleanup_stale_shared()
    return _STORE
