import base64

from openakita.api.routes import upload
from openakita.runtime.desktop.attachments import (
    format_desktop_attachment_reference,
    format_vision_unavailable_notice,
    has_pending_media_or_attachments,
)


def test_non_media_data_uri_attachment_is_saved_not_inlined(monkeypatch, tmp_path):
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path)
    raw = b"hello,xlsx"
    encoded = base64.b64encode(raw).decode("ascii")

    text = format_desktop_attachment_reference(
        att_type="document",
        att_name="report.xlsx",
        att_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        att_url=f"data:application/octet-stream;base64,{encoded}",
    )

    saved_files = list(tmp_path.iterdir())
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == raw
    assert "data:application/octet-stream;base64" not in text
    assert encoded not in text
    assert "/api/uploads/" in text
    assert str(saved_files[0]) in text


def test_uploaded_attachment_url_is_kept_as_short_reference():
    text = format_desktop_attachment_reference(
        att_type="document",
        att_name="report.xlsx",
        att_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        att_url="/api/uploads/123_report.xlsx",
    )

    assert text == (
        "[文档: report.xlsx "
        "(application/vnd.openxmlformats-officedocument.spreadsheetml.sheet)] "
        "URL: /api/uploads/123_report.xlsx"
    )


def test_vision_unavailable_notice_tells_model_not_to_guess_image_content():
    text = format_vision_unavailable_notice(
        count=1,
        names=["hippo.png"],
        paths=["C:/tmp/hippo.png"],
    )

    assert "用户本轮发送了 1 张图片" in text
    assert "hippo.png" in text
    assert "当前所有可用 LLM 端点都没有 vision/图片理解能力" in text
    assert "无法查看、识别或描述图片内容" in text
    assert "不要猜测图片内容" in text
    assert "不要回答成自我介绍或闲聊" in text
    assert "OpenAkita 设置中心配置带 vision 能力" in text


def test_pending_media_or_attachments_are_detected():
    assert has_pending_media_or_attachments(attachments=[{"type": "image"}]) is True
    assert has_pending_media_or_attachments(pending_images=[{"local_path": "x.png"}]) is True
    assert has_pending_media_or_attachments() is False
