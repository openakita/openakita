import base64

from openakita.api.routes import upload
from openakita.core.agent import _format_desktop_attachment_reference


def test_non_media_data_uri_attachment_is_saved_not_inlined(monkeypatch, tmp_path):
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path)
    raw = b"hello,xlsx"
    encoded = base64.b64encode(raw).decode("ascii")

    text = _format_desktop_attachment_reference(
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
    text = _format_desktop_attachment_reference(
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
