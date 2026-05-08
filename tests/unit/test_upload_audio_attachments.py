from fastapi import FastAPI
from fastapi.testclient import TestClient

from openakita.api.routes import upload
from openakita.core.agent import _format_desktop_attachment_reference


def _client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path)
    app = FastAPI()
    app.include_router(upload.router)
    return TestClient(app)


def test_audio_upload_uses_relaxed_audio_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAKITA_MAX_UPLOAD_MB", "0.000001")
    monkeypatch.setenv("OPENAKITA_MAX_AUDIO_UPLOAD_MB", "1")
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/upload",
        files={"file": ("meeting.wav", b"voice-bytes", "audio/wav")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["upload_id"] == body["filename"]
    assert body["local_path"]
    assert (tmp_path / body["filename"]).read_bytes() == b"voice-bytes"


def test_non_audio_upload_still_has_general_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAKITA_MAX_UPLOAD_MB", "0.000001")
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/api/upload",
        files={"file": ("notes.txt", b"too-large-for-test", "text/plain")},
    )

    assert response.status_code == 413
    assert not list(tmp_path.iterdir())


def test_desktop_uploaded_audio_reference_includes_local_path(tmp_path, monkeypatch):
    monkeypatch.setattr(upload, "UPLOAD_DIR", tmp_path)
    saved = tmp_path / "123_meeting.wav"
    saved.write_bytes(b"voice")

    text = _format_desktop_attachment_reference(
        att_type="voice",
        att_name="meeting.wav",
        att_mime="audio/wav",
        att_url="/api/uploads/123_meeting.wav",
    )

    assert "[音频: meeting.wav (audio/wav)" in text
    assert str(saved) in text
    assert "请直接使用文件/音频处理工具打开该本地路径" in text
