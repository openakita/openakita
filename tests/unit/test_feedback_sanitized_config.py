import json
import zipfile
from io import BytesIO

import pytest

from openakita.api.routes import bug_report
from openakita.utils.redaction import REDACTION


def test_sanitized_config_redacts_runtime_state_bot_credentials(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    runtime_state = {
        "im_bots": [
            {
                "id": "feishu-bot",
                "type": "feishu",
                "credentials": {
                    "app_id": "cli_public",
                    "app_secret": "should-not-leak",
                    "streaming_enabled": "true",
                },
            }
        ]
    }
    (data_dir / "runtime_state.json").write_text(
        json.dumps(runtime_state),
        encoding="utf-8",
    )

    monkeypatch.setattr(bug_report, "_resolve_data_dir", lambda: data_dir)
    monkeypatch.setattr(bug_report, "_collect_endpoint_summary", lambda: {})

    sanitized = bug_report._collect_sanitized_config()

    credentials = sanitized["_runtime_state"]["im_bots"][0]["credentials"]
    assert credentials["app_id"] == "cli_public"
    assert credentials["app_secret"] == REDACTION
    assert credentials["streaming_enabled"] == "true"


@pytest.mark.asyncio
async def test_bug_report_zip_includes_org_json_state(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    org_dir = data_dir / "orgs" / "org_123"
    org_dir.mkdir(parents=True)
    (org_dir / "org.json").write_text(json.dumps({"id": "org_123"}), encoding="utf-8")
    (org_dir / "state.json").write_text(json.dumps({"status": "active"}), encoding="utf-8")
    (org_dir / "events").mkdir()
    (org_dir / "events" / "20260627.jsonl").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(bug_report, "_resolve_data_dir", lambda: data_dir)
    monkeypatch.setattr(bug_report, "_get_recent_llm_debug_files", lambda limit=50: [])
    monkeypatch.setattr(bug_report, "_collect_sanitized_config", lambda: {})
    monkeypatch.setattr(bug_report, "_add_windows_crash_artifacts", lambda zf: None)

    zip_bytes = await bug_report._build_bug_zip(
        report_id="r1",
        title="bug",
        description="desc",
        steps="",
        sys_info={},
        contact_email="",
        contact_wechat="",
        images=None,
        upload_logs=False,
        upload_debug=True,
    )

    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        names = set(zf.namelist())

    assert "orgs/org_123/org.json" in names
    assert "orgs/org_123/state.json" in names
    assert "orgs/org_123/events/20260627.jsonl" in names
