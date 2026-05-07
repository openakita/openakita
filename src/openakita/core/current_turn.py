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
    r"刚上传|附件|图片|文件|链接|文档|它|其内容)"
)
_PATH_LIKE_RE = re.compile(
    r"(?:(?:[A-Za-z]:[\\/]|/|\.{1,2}[\\/])?[^\s，。！？；;:'\"`]+[\\/]"
    r"[^\s，。！？；;:'\"`]+|\b[\w.-]+\.(?:py|ts|tsx|js|jsx|md|json|yaml|yml|txt|pdf|png|jpg|jpeg|webp|gif)\b)"
)

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


@dataclass
class CurrentTurnInput:
    """Structured grounding state for the latest user turn."""

    text: str = ""
    urls: tuple[TurnObject, ...] = ()
    images: tuple[TurnObject, ...] = ()
    files: tuple[TurnObject, ...] = ()
    videos: tuple[TurnObject, ...] = ()
    audio: tuple[TurnObject, ...] = ()
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
        if not self.has_objects:
            return ""

        lines = ["[当前轮输入对象]"]
        if self.urls:
            lines.append("- 本轮 URL: " + "; ".join(obj.label or obj.value for obj in self.urls))
        if self.images:
            lines.append("- 本轮图片: " + "; ".join(_display_obj(obj) for obj in self.images))
        if self.files:
            lines.append("- 本轮文件/文档: " + "; ".join(_display_obj(obj) for obj in self.files))
        if self.videos:
            lines.append("- 本轮视频: " + "; ".join(_display_obj(obj) for obj in self.videos))
        if self.audio:
            lines.append("- 本轮音频: " + "; ".join(_display_obj(obj) for obj in self.audio))
        lines.append(
            "- 规则：用户说“这个/它/刚发的/附件/图片/文件/链接”时，默认指向本轮对象；"
            "只有用户明确说“上次/之前/历史里的”才使用历史对象。"
        )
        lines.append(
            "- 状态型工具（浏览器/桌面等）不能直接复用旧状态；如本轮有明确 URL 或附件，"
            "先切换/导航/读取到本轮对象再分析。"
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
        if not self.has_objects or self.allows_history_reference:
            return None

        if tool_name in _URL_TOOLS:
            requested_url = str(
                tool_input.get("url")
                or tool_input.get("href")
                or tool_input.get("link")
                or ""
            ).strip()
            if requested_url:
                return self._validate_url_tool(tool_name, requested_url)

        if tool_name in _BROWSER_CURRENT_PAGE_TOOLS and self.urls:
            if not self.urls_grounded and not self._matches_current_url(self.browser_current_url):
                return (
                    "⚠️ 当前轮有明确 URL，但浏览器当前页尚未确认是本轮 URL。\n"
                    f"本轮 URL: {self._url_list_text()}\n"
                    "请先调用 browser_navigate 导航到本轮 URL，再读取页面内容或操作页面。"
                )

        if tool_name == "view_image" and self.images:
            image_ref = str(tool_input.get("path") or tool_input.get("url") or "").strip()
            if image_ref and not self._matches_ref(image_ref, self.images):
                return (
                    "⚠️ 当前轮用户发送了图片，但 view_image 正在读取非本轮图片。\n"
                    f"本轮图片: {self._object_list_text(self.images)}\n"
                    "请改用本轮图片路径/URL；只有用户明确要求历史图片时才读取旧图片。"
                )

        if (
            tool_name == "read_file"
            and self.files
            and self.has_implicit_reference
            and not self.has_explicit_path_like_text
        ):
            path = str(tool_input.get("path") or tool_input.get("file_path") or "").strip()
            if path and not self._matches_ref(path, self.files):
                return (
                    "⚠️ 当前轮用户发送了文件/文档，但 read_file 正在读取非本轮文件。\n"
                    f"本轮文件: {self._object_list_text(self.files)}\n"
                    "请优先读取本轮文件；只有用户明确要求历史文件或其它路径时才读取旧文件。"
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
            if self._matches_current_url(url):
                self.urls_grounded = True
            if tool_name in {"browser_navigate", "browser_new_tab"}:
                self.browser_current_url = normalized

    def _validate_url_tool(self, tool_name: str, requested_url: str) -> str | None:
        if self._matches_current_url(requested_url):
            return None
        if self.urls_grounded:
            return None
        return (
            f"⚠️ 当前轮用户发送了明确 URL，但 {tool_name} 正在使用非本轮 URL。\n"
            f"本轮 URL: {self._url_list_text()}\n"
            f"工具参数 URL: {requested_url}\n"
            "请改用本轮 URL；只有用户明确要求“上次/之前/历史里的链接”时才使用旧链接。"
        )

    def _matches_current_url(self, url: str) -> bool:
        normalized = _normalize_url(url)
        return any(_normalize_url(obj.value) == normalized for obj in self.urls)

    def _matches_ref(self, value: str, candidates: tuple[TurnObject, ...]) -> bool:
        normalized = _normalize_ref(value)
        return any(_normalize_ref(obj.value) == normalized for obj in candidates)

    def _url_list_text(self) -> str:
        return "; ".join(obj.label or obj.value for obj in self.urls)

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
