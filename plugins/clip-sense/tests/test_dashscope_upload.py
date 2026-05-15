"""Tests for clip_dashscope_upload.py — public URL rules + temp OSS upload."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from clip_dashscope_upload import (
    DashScopeUploadError,
    paraformer_file_url_is_public,
    upload_local_file_for_paraformer,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestParaformerFileUrlIsPublic:
    def test_empty_and_relative(self):
        assert paraformer_file_url_is_public("") is False
        assert paraformer_file_url_is_public("   ") is False
        assert paraformer_file_url_is_public("/api/plugins/clip-sense/uploads/x.mp4") is False
        assert paraformer_file_url_is_public("relative/no/scheme.mp4") is False

    def test_oss_scheme_accepted(self):
        assert paraformer_file_url_is_public("oss://bucket/dir/file.mp4") is True

    def test_public_https(self):
        assert paraformer_file_url_is_public("https://example.com/a.mp4") is True

    def test_loopback_and_localhost(self):
        assert paraformer_file_url_is_public("http://127.0.0.1:18900/v.mp4") is False
        assert paraformer_file_url_is_public("http://localhost/foo.mp4") is False
        assert paraformer_file_url_is_public("http://[::1]/x") is False

    def test_private_ip(self):
        assert paraformer_file_url_is_public("http://192.168.1.10/share/v.mp4") is False
        assert paraformer_file_url_is_public("http://10.0.0.1/v.mp4") is False


class TestUploadLocalFileForParaformer:
    def test_missing_file(self, tmp_path: Path):
        client = AsyncMock()
        missing = tmp_path / "nope.mp4"
        with pytest.raises(DashScopeUploadError, match="not found"):
            run(
                upload_local_file_for_paraformer(
                    client, "k", base_url="https://dashscope.aliyuncs.com", local_path=missing,
                ),
            )

    def test_happy_path_snake_case_policy(self, tmp_path: Path):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"vid")

        policy_json = {
            "data": {
                "upload_host": "https://oss-cn-hangzhou.aliyuncs.com",
                "upload_dir": "dashscope/tmp/123",
                "oss_access_key_id": "AKID",
                "signature": "sig",
                "policy": "pol",
            }
        }
        get_resp = MagicMock()
        get_resp.status_code = 200
        get_resp.json.return_value = policy_json

        post_resp = MagicMock()
        post_resp.status_code = 200
        post_resp.text = ""

        client = AsyncMock()
        client.get = AsyncMock(return_value=get_resp)
        client.post = AsyncMock(return_value=post_resp)

        url = run(
            upload_local_file_for_paraformer(
                client, "sk-xxx", base_url="https://dashscope.aliyuncs.com", local_path=f,
            ),
        )
        assert url == "oss://dashscope/tmp/123/a.mp4"
        client.get.assert_awaited_once()
        g_kw = client.get.await_args.kwargs
        assert g_kw["params"] == {"action": "getPolicy", "model": "paraformer-v2"}
        client.post.assert_awaited_once()
        post_call = client.post.await_args
        assert post_call.args[0] == "https://oss-cn-hangzhou.aliyuncs.com"
        assert "file" in post_call.kwargs["files"]

    def test_get_policy_http_error(self, tmp_path: Path):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.RequestError("boom", request=MagicMock()),
        )
        with pytest.raises(DashScopeUploadError, match="getPolicy network"):
            run(
                upload_local_file_for_paraformer(
                    client, "k", base_url="https://dashscope.aliyuncs.com", local_path=f,
                ),
            )
