from openakita.core.context_manager import ContextManager


class DummyBrain:
    model = "test-model"


def _image_block(data: str = "abc") -> dict:
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{data}"},
    }


def _has_image_block(content: object) -> bool:
    return isinstance(content, list) and any(
        isinstance(item, dict) and item.get("type") == "image_url" for item in content
    )


def test_hard_truncate_preserves_current_turn_image():
    cm = ContextManager(DummyBrain())
    messages = [
        {"role": "assistant", "content": "old context " * 5000},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "识别这张图"},
                _image_block(),
            ],
        },
    ]

    result = cm._hard_truncate_if_needed(messages, hard_limit=100)

    assert _has_image_block(result[-1]["content"])
    assert "图片内容已移除以节省上下文空间" not in str(result[-1]["content"])


def test_payload_guard_strips_history_media_but_keeps_current_turn_image():
    cm = ContextManager(DummyBrain())
    history_image = _image_block("a" * 220_000)
    current_image = _image_block("b" * 20)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "旧图片"}, history_image]},
        {"role": "assistant", "content": "之前的回复"},
        {"role": "user", "content": [{"type": "text", "text": "识别这张图"}, current_image]},
    ]

    result = cm._strip_oversized_payload(messages, overhead_bytes=2_000_000)

    assert "图片内容已移除以节省上下文空间" in str(result[0]["content"])
    assert _has_image_block(result[-1]["content"])


def test_payload_guard_reports_current_turn_image_when_it_cannot_fit():
    cm = ContextManager(DummyBrain())
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "识别这张图"},
                _image_block("c" * 230_000),
            ],
        }
    ]

    result = cm._strip_oversized_payload(messages, overhead_bytes=2_000_000)

    assert not _has_image_block(result[-1]["content"])
    assert "本轮图片内容过大" in str(result[-1]["content"])
    assert "请压缩图片后重新上传" in str(result[-1]["content"])
