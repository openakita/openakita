from unittest.mock import patch

from openakita.attachments.adapter import resolve_attachment_model_context
from openakita.attachments.adapter import get_attachment_adaptation_policy
from openakita.api.attachment_store import AttachmentStore
from openakita.core.agent import Agent
from openakita.llm.runtime_context import ResolvedModelContext


class TestAgentAttachmentHelpers:
    def test_build_desktop_attachment_content_blocks_keeps_text_and_image(self):
        blocks = Agent._build_desktop_attachment_content_blocks(
            [
                {
                    "type": "image",
                    "name": "demo.png",
                    "url": "data:image/png;base64,ZmFrZQ==",
                    "mime_type": "image/png",
                }
            ],
            text="[12:34] 帮我看看这张图",
        )

        assert blocks[0]["type"] == "text"
        assert "帮我看看这张图" in blocks[0]["text"]
        assert blocks[1]["type"] == "image_url"
        assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_merge_llm_message_content_preserves_multimodal_parts(self):
        merged = Agent._merge_llm_message_content(
            "第一条消息",
            [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}],
        )

        assert isinstance(merged, list)
        assert merged[0]["type"] == "text"
        assert merged[-1]["type"] == "image_url"

    def test_build_desktop_attachment_content_blocks_does_not_fake_path_from_filename(self):
        blocks = Agent._build_desktop_attachment_content_blocks(
            [
                {
                    "id": "att-1",
                    "type": "document",
                    "name": "新建文本文档.txt",
                    "url": "/api/attachments/att-1/content",
                    "mime_type": "text/plain",
                    "text_preview": "889977",
                }
            ],
            text="这里面有什么内容",
        )

        assert blocks[1]["type"] == "text"
        assert "[已上传文档: 新建文本文档.txt (text/plain), attachment_id=att-1]" in blocks[1]["text"]
        assert "路径:" not in blocks[1]["text"]
        assert "不要把它误当成工作区文件再次 `read_file`" in blocks[1]["text"]
        assert "889977" in blocks[1]["text"]

    def test_build_desktop_attachment_content_blocks_uses_reference_for_large_text(self, tmp_path):
        store = AttachmentStore(root=tmp_path)
        record = store.save_uploaded_file(
            content=("hello world\n" * 4000).encode("utf-8"),
            filename="large.txt",
            mime_type="text/plain",
            conversation_id="conv-a",
        )

        with patch("openakita.core.agent.get_attachment_store", return_value=store):
            blocks = Agent._build_desktop_attachment_content_blocks(
                [{"id": record["id"], "type": "document", "name": "large.txt"}],
                text="总结一下",
                llm_client=None,
            )

        assert blocks[1]["type"] == "text"
        assert "read_attachment_summary" in blocks[1]["text"]
        assert "read_attachment_chunk" in blocks[1]["text"]
        assert "不要把它误当成工作区文件再次 `read_file`" not in blocks[1]["text"]

    def test_attachment_policy_uses_effective_context_window(self):
        policy = get_attachment_adaptation_policy(
            ResolvedModelContext(
                effective_context_window=64_000,
                allows_tools=True,
            )
        )

        assert policy["inline_chars"] == 8_000
        assert policy["snippet_chars"] == 24_000
        assert policy["preview_chars"] == 1_800

    def test_resolve_attachment_model_context_passes_request_semantics(self):
        class _FakeClient:
            def __init__(self):
                self.kwargs = None

            def resolve_model_context(self, **kwargs):
                self.kwargs = kwargs
                return ResolvedModelContext(effective_context_window=128_000, allows_tools=True)

        client = _FakeClient()
        ctx = resolve_attachment_model_context(
            [{"type": "image", "mime_type": "image/png"}],
            llm_client=client,
            conversation_id="conv-1",
            require_tools=True,
        )

        assert ctx.effective_context_window == 128_000
        assert client.kwargs == {
            "require_tools": True,
            "require_vision": True,
            "require_video": False,
            "conversation_id": "conv-1",
        }
