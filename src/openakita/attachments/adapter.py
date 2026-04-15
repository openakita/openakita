from __future__ import annotations

import contextlib

from ..api.attachment_store import get_attachment_store
from ..llm.runtime_context import ResolvedModelContext


def resolve_attachment_model_context(
    attachments: list[dict] | None,
    *,
    llm_client: object | None = None,
    conversation_id: str | None = None,
    require_tools: bool = False,
) -> ResolvedModelContext:
    """按本轮附件需求解析模型上下文。"""
    if llm_client is None or not hasattr(llm_client, "resolve_model_context"):
        return ResolvedModelContext()

    has_image = False
    has_video = False
    for att in attachments or []:
        att_type = str(att.get("type", "") or "")
        mime_type = str(att.get("mime_type", "") or "")
        url = str(att.get("url", "") or "")
        if att_type == "image" or mime_type.startswith("image/") or url.startswith("data:image/"):
            has_image = True
        if att_type == "video" or mime_type.startswith("video/") or url.startswith("data:video/"):
            has_video = True

    with contextlib.suppress(Exception):
        return llm_client.resolve_model_context(
            require_tools=require_tools,
            require_vision=has_image,
            require_video=has_video,
            conversation_id=conversation_id,
        )
    return ResolvedModelContext()


def get_attachment_adaptation_policy(model_context: ResolvedModelContext | None = None) -> dict:
    """根据本轮解析出的模型上下文决定附件正文注入预算。"""
    ctx = int(getattr(model_context, "effective_context_window", 0) or 0)
    has_tools = bool(getattr(model_context, "has_tools", False))

    if ctx >= 160_000:
        inline_chars = 12_000
        snippet_chars = 36_000
        preview_chars = 2_400
    elif ctx >= 64_000:
        inline_chars = 8_000
        snippet_chars = 24_000
        preview_chars = 1_800
    else:
        inline_chars = 4_000
        snippet_chars = 12_000
        preview_chars = 1_200

    if not has_tools:
        inline_chars = int(inline_chars * 1.5)
        snippet_chars = int(snippet_chars * 1.75)
        preview_chars = int(preview_chars * 1.5)

    return {
        "resolved_model_context": model_context,
        "has_tools": has_tools,
        "inline_chars": inline_chars,
        "snippet_chars": snippet_chars,
        "preview_chars": preview_chars,
        "snippet_head_chars": max(800, inline_chars // 2),
        "snippet_tail_chars": max(400, inline_chars // 3),
    }


def build_attachment_content_blocks(
    attachments: list[dict] | None,
    *,
    text: str = "",
    llm_client: object | None = None,
    conversation_id: str | None = None,
    require_tools: bool = False,
) -> list[dict]:
    """把已解析附件转换成供模型消费的内容块。"""
    content_blocks: list[dict] = []
    if text:
        content_blocks.append({"type": "text", "text": text})

    store = get_attachment_store()
    model_context = resolve_attachment_model_context(
        attachments,
        llm_client=llm_client,
        conversation_id=conversation_id,
        require_tools=require_tools,
    )
    policy = get_attachment_adaptation_policy(model_context)

    for att in attachments or []:
        att_type = str(att.get("type", "") or "file")
        att_url = str(att.get("url", "") or "")
        att_name = str(att.get("name", "") or "file")
        att_mime = str(att.get("mime_type", "") or "application/octet-stream")
        display_path = str(att.get("display_path", "") or att.get("source_path", "") or "")

        is_image = (
            att_type == "image"
            or att_mime.startswith("image/")
            or att_url.startswith("data:image/")
        )
        is_video = (
            att_type == "video"
            or att_mime.startswith("video/")
            or att_url.startswith("data:video/")
        )
        if att.get("id") and att_url and not att_url.startswith("data:"):
            data_url = store.to_data_url(att)
            if data_url:
                att_url = data_url

        if is_image and att_url:
            content_blocks.append({"type": "image_url", "image_url": {"url": att_url}})
            continue
        if is_video and att_url:
            content_blocks.append({"type": "video_url", "video_url": {"url": att_url}})
            continue

        derived = store.describe_attachment(
            att,
            preview_chars=policy["preview_chars"],
            inline_chars=policy["inline_chars"],
            snippet_chars=policy["snippet_chars"],
            snippet_head_chars=policy["snippet_head_chars"],
            snippet_tail_chars=policy["snippet_tail_chars"],
        )
        derived_name = str(derived.get("name") or att_name)
        derived_mime = str(derived.get("mime_type") or att_mime or "application/octet-stream")
        attachment_ref = f", attachment_id={derived['id']}" if derived.get("id") else ""
        label = "已上传文档" if att_type == "document" else "已上传附件"
        tool_hint = (
            "\n如需继续读取该上传附件，请使用专用附件工具："
            f"\n{derived['tool_hint']}\n不要把它改写成工作区路径再去调用 `read_file`。"
            if derived.get("tool_hint")
            else ""
        )

        if att_type == "directory":
            entries = derived.get("entries") or []
            listing = "\n".join(str(item) for item in entries[:50])
            path_text = f"\n显示路径: {display_path}" if display_path else ""
            content_blocks.append({
                "type": "text",
                "text": (
                    f"[目录引用: {derived_name}{attachment_ref}]"
                    f"{path_text}\n这是用户上传的目录元数据，不代表当前工作区可直接访问的真实路径。"
                    + (f"\n目录内容:\n{listing}" if listing else "")
                ),
            })
        elif derived.get("mode") == "inline":
            content_blocks.append({
                "type": "text",
                "text": (
                    f"[{label}: {derived_name} ({derived_mime}){attachment_ref}]\n"
                    "以下正文已经随本条消息提供，请优先直接基于附件内容回答，"
                    "不要把它误当成工作区文件再次 `read_file`：\n"
                    f"{derived.get('inline_text', '')}"
                ),
            })
        elif derived.get("mode") == "snippet":
            content_blocks.append({
                "type": "text",
                "text": (
                    f"[{label}: {derived_name} ({derived_mime}){attachment_ref}]\n"
                    "附件正文较长，以下仅提供节选：\n"
                    f"{derived.get('snippet_text', '')}"
                    f"{tool_hint}"
                ),
            })
        else:
            preview = str(derived.get("preview", "") or "")
            preview_text = f"\n预览:\n{preview}" if preview else ""
            capability_text = (
                "该附件未直接内联完整正文。"
                if derived.get("text_extractable")
                else "该附件当前没有可直接内联的正文，先根据元数据/预览理解其内容。"
            )
            content_blocks.append({
                "type": "text",
                "text": (
                    f"[{label}: {derived_name} ({derived_mime}){attachment_ref}]\n"
                    f"{capability_text}{preview_text}{tool_hint}"
                ),
            })

    return content_blocks
