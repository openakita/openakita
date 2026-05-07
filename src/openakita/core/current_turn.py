"""Current-turn grounding for user-provided objects.

This module keeps URLs, images, files, and other attachments from the latest
user turn as structured state.  The goal is to make "this / it / the link I
just sent" bind to the current turn by default, instead of letting long history
or stateful tools accidentally reuse older objects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

_URL_RE = re.compile(r"https?://[^\s<>'\"`，。！？、；；）)\]}】]+", re.IGNORECASE)
_HISTORY_REF_RE = re.compile(
    r"(上次|之前|以前|历史|旧的|老的|上午|下午|昨天|前面|先前|上一[个张份条]|刚才那个)"
)
_IMPLICIT_REF_RE = re.compile(
    r"(这个|这个链接|这条链接|这个文件|这个附件|这张图|这张图片|这份文档|刚发|刚发送|"
    r"刚上传|附件|图片|文件|链接|文档|它|其内容|上面|前面|继续|接着|刚才)"
)
_PATH_LIKE_RE = re.compile(
    r"(?:(?:[A-Za-z]:[\\/]|/|\.{1,2}[\\/])?[^\s，。！？；;:'\"`]+[\\/]"
    r"[^\s，。！？；;:'\"`]+|\b[\w.-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|txt|pdf|png|jpg|jpeg|webp|gif)\b)"
)
_URL_REF_RE = re.compile(r"(链接|网址|URL|url|网页|网站|页面)")
_IMAGE_REF_RE = re.compile(r"(图|图片|截图|照片|画面|image|img)", re.IGNORECASE)
_FILE_REF_RE = re.compile(r"(文件|文档|附件|PDF|pdf|表格|报告|file|document)", re.IGNORECASE)
_VIDEO_REF_RE = re.compile(r"(视频|录像|video)", re.IGNORECASE)
_AUDIO_REF_RE = re.compile(r"(音频|语音|录音|audio)", re.IGNORECASE)

_URL_TOOLS = {"web_fetch", "browser_navigate", "browser_new_tab"}
_BROWSER_CURRENT_PAGE_TOOLS = {
    "browser_get_content",
    "browser_screenshot",
    "browser_click",
    "browser_type",
    "browser_scroll",
    "browser_wait",
    "browser_execute_js",
}


@dataclass(frozen=True)
class TurnObject:
    """A concrete object supplied by the current user turn."""

    kind: str
    value: str
    label: str = ""
    mime_type: str = ""
    source_turn: int = 0


@dataclass
class SessionObjectRegistry:
    """Session-scoped index of user-provided objects.

    This is runtime state, intentionally separate from transcript text.  The
    registry gives follow-up turns a structured "recent object" to bind to when
    the user says "continue with that image/link/file" without resending it.
    """

    objects: list[TurnObject] = field(default_factory=list)
    turn_index: int = 0
    max_objects: int = 80

    def resolve_for_turn(self, turn: CurrentTurnInput) -> tuple[TurnObject, ...]:
        """Resolve likely historical objects for a follow-up turn."""
        if not self.objects:
            return ()
        if turn.has_objects and not turn.allows_history_reference:
            return ()
        if not turn.has_objects and not (turn.has_implicit_reference or turn.allows_history_reference):
            return ()

        kinds = _requested_kinds(turn.text)
        if not kinds:
            kinds = ("url", "image", "file", "video", "audio")

        resolved: list[TurnObject] = []
        for kind in kinds:
            latest = self.latest(kind)
            if latest is not None:
                resolved.append(latest)
        return tuple(resolved)

    def register_turn(self, turn: CurrentTurnInput) -> None:
        """Record concrete objects from the current user turn."""
        if not turn.has_objects:
            return

        self.turn_index += 1
        for obj in turn.iter_current_objects():
            stamped = TurnObject(
                kind=obj.kind,
                value=obj.value,
                label=obj.label,
                mime_type=obj.mime_type,
                source_turn=self.turn_index,
            )
            self._append(stamped)

        if len(self.objects) > self.max_objects:
            self.objects = self.objects[-self.max_objects :]

    def latest(self, kind: str) -> TurnObject | None:
        for obj in reversed(self.objects):
            if obj.kind == kind:
                return obj
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "max_objects": self.max_objects,
            "objects": [
                {
                    "kind": obj.kind,
                    "value": obj.value,
                    "label": obj.label,
                    "mime_type": obj.mime_type,
                    "source_turn": obj.source_turn,
                }
                for obj in self.objects[-self.max_objects :]
            ],
        }

    @classmethod
    def from_dict(cls, data: Any) -> SessionObjectRegistry:
        if not isinstance(data, dict):
            return cls()
        registry = cls(
            turn_index=_safe_int(data.get("turn_index")),
            max_objects=max(1, _safe_int(data.get("max_objects"), 80)),
        )
        objects: list[TurnObject] = []
        for raw in data.get("objects") or []:
            if not isinstance(raw, dict):
                continue
            value = str(raw.get("value") or "")
            kind = str(raw.get("kind") or "")
            if not value or not kind:
                continue
            objects.append(
                TurnObject(
                    kind=kind,
                    value=value,
                    label=str(raw.get("label") or ""),
                    mime_type=str(raw.get("mime_type") or ""),
                    source_turn=_safe_int(raw.get("source_turn")),
                )
            )
        registry.objects = objects[-registry.max_objects :]
        return registry

    def _append(self, obj: TurnObject) -> None:
        normalized = _normalize_ref(obj.value)
        self.objects = [
            existing
            for existing in self.objects
            if not (existing.kind == obj.kind and _normalize_ref(existing.value) == normalized)
        ]
        self.objects.append(obj)


@dataclass
class CurrentTurnInput:
    """Structured grounding state for the latest user turn."""

    text: str = ""
    urls: tuple[TurnObject, ...] = ()
    images: tuple[TurnObject, ...] = ()
    files: tuple[TurnObject, ...] = ()
    videos: tuple[TurnObject, ...] = ()
    audio: tuple[TurnObject, ...] = ()
    recent_objects: tuple[TurnObject, ...] = ()
    browser_current_url: str = ""
    urls_grounded: bool = False
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def from_inputs(
        cls,
        text: str,
        *,
        pending_images: Any = None,
        pending_videos: Any = None,
        pending_audio: Any = None,
        pending_files: Any = None,
        attachments: Any = None,
    ) -> CurrentTurnInput:
        """Build current-turn state from text plus Desktop/IM attachment shapes."""
        text_value = text if isinstance(text, str) else ""
        urls = tuple(
            TurnObject(kind="url", value=_normalize_url(m.group(0)), label=m.group(0))
            for m in _URL_RE.finditer(text_value)
        )

        images: list[TurnObject] = []
        videos: list[TurnObject] = []
        audio: list[TurnObject] = []
        files: list[TurnObject] = []

        def add_media(raw_items: Any, target: list[TurnObject], kind: str) -> None:
            for item in _iter_items(raw_items):
                value = _item_value(item)
                if not value:
                    continue
                target.append(
                    TurnObject(
                        kind=kind,
                        value=_normalize_ref(value),
                        label=_item_label(item) or value,
                        mime_type=_item_mime(item),
                    )
                )

        add_media(pending_images, images, "image")
        add_media(pending_videos, videos, "video")
        add_media(pending_audio, audio, "audio")
        add_media(pending_files, files, "file")

        for att in attachments or []:
            att_type = str(getattr(att, "type", "") or "").lower()
            mime = str(getattr(att, "mime_type", "") or "")
            value = _item_value(att)
            if not value:
                continue
            obj = TurnObject(
                kind=att_type or "attachment",
                value=_normalize_ref(value),
                label=_item_label(att) or value,
                mime_type=mime,
            )
            if att_type == "image" or mime.startswith("image/"):
                images.append(obj)
            elif att_type == "video" or mime.startswith("video/"):
                videos.append(obj)
            elif att_type == "audio" or mime.startswith("audio/"):
                audio.append(obj)
            else:
                files.append(obj)

        return cls(
            text=text_value,
            urls=tuple(_dedupe_objects(urls)),
            images=tuple(_dedupe_objects(images)),
            files=tuple(_dedupe_objects(files)),
            videos=tuple(_dedupe_objects(videos)),
            audio=tuple(_dedupe_objects(audio)),
        )

    @property
    def has_objects(self) -> bool:
        return bool(self.urls or self.images or self.files or self.videos or self.audio)

    @property
    def has_resolved_references(self) -> bool:
        return bool(self.recent_objects)

    @property
    def reference_urls(self) -> tuple[TurnObject, ...]:
        return tuple(obj for obj in self.recent_objects if obj.kind == "url")

    @property
    def reference_images(self) -> tuple[TurnObject, ...]:
        return tuple(obj for obj in self.recent_objects if obj.kind == "image")

    @property
    def reference_files(self) -> tuple[TurnObject, ...]:
        return tuple(obj for obj in self.recent_objects if obj.kind == "file")

    @property
    def reference_videos(self) -> tuple[TurnObject, ...]:
        return tuple(obj for obj in self.recent_objects if obj.kind == "video")

    @property
    def reference_audio(self) -> tuple[TurnObject, ...]:
        return tuple(obj for obj in self.recent_objects if obj.kind == "audio")

    @property
    def allows_history_reference(self) -> bool:
        return bool(_HISTORY_REF_RE.search(self.text or ""))

    @property
    def has_implicit_reference(self) -> bool:
        return bool(_IMPLICIT_REF_RE.search(self.text or "")) or (
            self.has_objects and len((self.text or "").strip()) <= 40
        )

    @property
    def has_explicit_path_like_text(self) -> bool:
        return bool(_PATH_LIKE_RE.search(self.text or ""))

    def prompt_block(self) -> str:
        """Render a compact, model-visible description of current-turn objects."""
        if not self.has_objects and not self.has_resolved_references:
            return ""

        lines: list[str] = []
        if self.has_objects:
            lines.append("[当前轮输入对象]")
            self._append_object_lines(lines, prefix="本轮")
            lines.append(
                "- 规则：用户说“这个/它/刚发的/附件/图片/文件/链接”时，默认指向本轮对象；"
                "只有用户明确说“上次/之前/历史里的”才使用历史对象。"
            )
            lines.append(
                "- 状态型工具（浏览器/桌面等）不能直接复用旧状态；如本轮有明确 URL 或附件，"
                "先切换/导航/读取到本轮对象再分析。"
            )

        if self.has_resolved_references:
            if lines:
                lines.append("")
            lines.append("[最近可引用对象]")
            if self.reference_urls:
                lines.append(
                    "- 最近 URL: "
                    + "; ".join(obj.label or obj.value for obj in self.reference_urls)
                )
            if self.reference_images:
                lines.append(
                    "- 最近图片: " + "; ".join(_display_obj(obj) for obj in self.reference_images)
                )
            if self.reference_files:
                lines.append(
                    "- 最近文件/文档: "
                    + "; ".join(_display_obj(obj) for obj in self.reference_files)
                )
            if self.reference_videos:
                lines.append(
                    "- 最近视频: " + "; ".join(_display_obj(obj) for obj in self.reference_videos)
                )
            if self.reference_audio:
                lines.append(
                    "- 最近音频: " + "; ".join(_display_obj(obj) for obj in self.reference_audio)
                )
            lines.append(
                "- 规则：本轮没有新对象时，用户说“继续/上面/刚才/那个/它”默认指向这些最近对象；"
                "如需更早历史对象，应让用户明确指出。"
            )
        return "\n".join(lines)

    def inject_into_message(self, message: str) -> str:
        block = self.prompt_block()
        if not block:
            return message
        latest_marker = "[最新消息]\n"
        if message.startswith(latest_marker):
            return f"{latest_marker}{block}\n\n{message[len(latest_marker):]}"
        return f"{block}\n\n{message}" if message else block

    def validate_tool_call(self, tool_name: str, tool_input: dict[str, Any]) -> str | None:
        """Return an instructional block if a tool call is grounded to stale objects."""
        if not self.has_objects and not self.has_resolved_references:
            return None
        if self.has_objects and self.allows_history_reference:
            return None

        active_urls = self.urls or self.reference_urls
        active_images = self.images or self.reference_images
        active_files = self.files or self.reference_files

        if tool_name in _URL_TOOLS:
            requested_url = str(
                tool_input.get("url")
                or tool_input.get("href")
                or tool_input.get("link")
                or ""
            ).strip()
            if requested_url:
                return self._validate_url_tool(tool_name, requested_url, active_urls)

        if tool_name in _BROWSER_CURRENT_PAGE_TOOLS and active_urls:
            if not self.urls_grounded and not self._matches_url(self.browser_current_url, active_urls):
                return (
                    "⚠️ 当前对话有明确引用 URL，但浏览器当前页尚未确认是该 URL。\n"
                    f"应使用的 URL: {self._url_list_text(active_urls)}\n"
                    "请先调用 browser_navigate 导航到该 URL，再读取页面内容或操作页面。"
                )

        if tool_name == "view_image" and active_images:
            image_ref = str(tool_input.get("path") or tool_input.get("url") or "").strip()
            if image_ref and not self._matches_ref(image_ref, active_images):
                return (
                    "⚠️ 用户当前引用了明确图片，但 view_image 正在读取其它图片。\n"
                    f"应使用的图片: {self._object_list_text(active_images)}\n"
                    "请改用该图片路径/URL；只有用户明确要求其它历史图片时才读取旧图片。"
                )

        if (
            tool_name == "read_file"
            and active_files
            and self.has_implicit_reference
            and not self.has_explicit_path_like_text
        ):
            path = str(tool_input.get("path") or tool_input.get("file_path") or "").strip()
            if path and not self._matches_ref(path, active_files):
                return (
                    "⚠️ 用户当前引用了明确文件/文档，但 read_file 正在读取其它文件。\n"
                    f"应使用的文件: {self._object_list_text(active_files)}\n"
                    "请优先读取该文件；只有用户明确要求其它路径时才读取旧文件。"
                )

        return None

    def observe_tool_result(self, tool_name: str, tool_input: dict[str, Any], result: Any) -> None:
        """Update current-turn state after successful state-changing tools."""
        if _is_error_result(result):
            return
        if tool_name in _URL_TOOLS:
            url = str(
                tool_input.get("url")
                or tool_input.get("href")
                or tool_input.get("link")
                or ""
            ).strip()
            if not url:
                return
            normalized = _normalize_url(url)
            active_urls = self.urls or self.reference_urls
            if self._matches_url(url, active_urls):
                self.urls_grounded = True
            if tool_name in {"browser_navigate", "browser_new_tab"}:
                self.browser_current_url = normalized

    def with_recent_objects(self, objects: tuple[TurnObject, ...]) -> CurrentTurnInput:
        self.recent_objects = objects
        return self

    def iter_current_objects(self) -> tuple[TurnObject, ...]:
        return self.urls + self.images + self.files + self.videos + self.audio

    def _append_object_lines(self, lines: list[str], *, prefix: str) -> None:
        if self.urls:
            lines.append(f"- {prefix} URL: " + "; ".join(obj.label or obj.value for obj in self.urls))
        if self.images:
            lines.append(f"- {prefix}图片: " + "; ".join(_display_obj(obj) for obj in self.images))
        if self.files:
            lines.append(f"- {prefix}文件/文档: " + "; ".join(_display_obj(obj) for obj in self.files))
        if self.videos:
            lines.append(f"- {prefix}视频: " + "; ".join(_display_obj(obj) for obj in self.videos))
        if self.audio:
            lines.append(f"- {prefix}音频: " + "; ".join(_display_obj(obj) for obj in self.audio))

    def _validate_url_tool(
        self,
        tool_name: str,
        requested_url: str,
        active_urls: tuple[TurnObject, ...],
    ) -> str | None:
        if not active_urls:
            return None
        if self._matches_url(requested_url, active_urls):
            return None
        if self.urls_grounded:
            return None
        return (
            f"⚠️ 用户当前引用了明确 URL，但 {tool_name} 正在使用其它 URL。\n"
            f"应使用的 URL: {self._url_list_text(active_urls)}\n"
            f"工具参数 URL: {requested_url}\n"
            "请改用该 URL；只有用户明确要求其它历史链接时才使用旧链接。"
        )

    def _matches_current_url(self, url: str) -> bool:
        return self._matches_url(url, self.urls)

    def _matches_url(self, url: str, candidates: tuple[TurnObject, ...]) -> bool:
        normalized = _normalize_url(url)
        return any(_normalize_url(obj.value) == normalized for obj in candidates)

    def _matches_ref(self, value: str, candidates: tuple[TurnObject, ...]) -> bool:
        normalized = _normalize_ref(value)
        return any(_normalize_ref(obj.value) == normalized for obj in candidates)

    def _url_list_text(self, urls: tuple[TurnObject, ...]) -> str:
        return "; ".join(obj.label or obj.value for obj in urls)

    @staticmethod
    def _object_list_text(objects: tuple[TurnObject, ...]) -> str:
        return "; ".join(_display_obj(obj) for obj in objects)


def _iter_items(value: Any) -> list[Any]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _item_value(item: Any) -> str:
    if isinstance(item, dict):
        return str(
            item.get("local_path")
            or item.get("path")
            or item.get("url")
            or item.get("file_url")
            or ""
        )
    return str(
        getattr(item, "local_path", None)
        or getattr(item, "path", None)
        or getattr(item, "url", None)
        or getattr(item, "file_url", None)
        or ""
    )


def _item_label(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("filename") or item.get("name") or item.get("display_name") or "")
    return str(getattr(item, "filename", None) or getattr(item, "name", None) or "")


def _item_mime(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("mime_type") or item.get("media_type") or "")
    return str(getattr(item, "mime_type", None) or getattr(item, "media_type", None) or "")


def _dedupe_objects(items: tuple[TurnObject, ...] | list[TurnObject]) -> list[TurnObject]:
    seen: set[tuple[str, str]] = set()
    result: list[TurnObject] = []
    for item in items:
        key = (item.kind, _normalize_ref(item.value))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _requested_kinds(text: str) -> tuple[str, ...]:
    value = text or ""
    kinds: list[str] = []
    for kind, pattern in (
        ("url", _URL_REF_RE),
        ("image", _IMAGE_REF_RE),
        ("file", _FILE_REF_RE),
        ("video", _VIDEO_REF_RE),
        ("audio", _AUDIO_REF_RE),
    ):
        if pattern.search(value):
            kinds.append(kind)
    return tuple(kinds)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_url(url: str) -> str:
    raw = (url or "").strip().rstrip(".,;:!?'\"`)）】")
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            parsed.query,
            "",
        )
    )


def _normalize_ref(value: str) -> str:
    raw = (value or "").strip()
    if raw.startswith(("http://", "https://")):
        return _normalize_url(raw)
    try:
        return str(Path(raw).expanduser().resolve())
    except Exception:
        return raw


def _display_obj(obj: TurnObject) -> str:
    if obj.label and obj.label != obj.value:
        return f"{obj.label} ({obj.value})"
    return obj.value


def _is_error_result(result: Any) -> bool:
    if isinstance(result, str):
        return result.strip().startswith(("❌", "错误", "Error"))
    if isinstance(result, dict):
        return result.get("success") is False or bool(result.get("error"))
    return False
