#!/usr/bin/env python3
"""百度千帆 AppBuilder 公共客户端模块

供所有百度 AppBuilder skill 共享，零外部依赖（纯 stdlib）。

认证方式：环境变量 APPBUILDER_TOKEN
API 端点：https://appbuilder.baidu.com/rpc/2.0/cloud_hub/v1/ai_engine/agi_platform/v1/instance/integrated
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Generator, Optional

API_BASE = "https://appbuilder.baidu.com/rpc/2.0/cloud_hub/v1/ai_engine"
INTEGRATED_URL = f"{API_BASE}/agi_platform/v1/instance/integrated"

CONVERSATION_URL = (
    "https://appbuilder.baidu.com/rpc/2.0/cloud_hub/v1/ai_engine/"
    "agi_platform/v1/instance/conversation"
)


class AppBuilderError(Exception):
    """AppBuilder API 调用异常"""

    def __init__(self, message: str, status_code: int = 0, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class AppBuilderClient:
    """百度千帆 AppBuilder 客户端

    Parameters
    ----------
    token : str, optional
        AppBuilder API Token，缺省从 APPBUILDER_TOKEN 环境变量读取。
    app_id : str, optional
        应用 ID，部分接口需要。
    timeout : int
        HTTP 超时秒数，默认 120。
    """

    def __init__(
        self,
        token: Optional[str] = None,
        app_id: Optional[str] = None,
        timeout: int = 120,
    ):
        self.token = token or os.environ.get("APPBUILDER_TOKEN", "")
        if not self.token:
            raise AppBuilderError(
                "缺少 APPBUILDER_TOKEN，请设置环境变量或显式传入 token"
            )
        self.app_id = app_id or os.environ.get("APPBUILDER_APP_ID", "")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Appbuilder-Authorization": f"Bearer {self.token}",
        }

    def _request(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise AppBuilderError(
                f"HTTP {e.code}: {body[:500]}", status_code=e.code, body=body
            ) from e
        except urllib.error.URLError as e:
            raise AppBuilderError(f"网络错误: {e.reason}") from e

    def _stream_request(
        self, url: str, payload: Dict[str, Any]
    ) -> Generator[Dict[str, Any], None, None]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise AppBuilderError(
                f"HTTP {e.code}: {body[:500]}", status_code=e.code, body=body
            ) from e
        except urllib.error.URLError as e:
            raise AppBuilderError(f"网络错误: {e.reason}") from e

        buffer = ""
        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        return
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
        finally:
            resp.close()

    def create_conversation(self) -> str:
        payload: Dict[str, Any] = {}
        if self.app_id:
            payload["app_id"] = self.app_id
        result = self._request(CONVERSATION_URL, payload)
        cid = result.get("conversation_id", "")
        if not cid:
            raise AppBuilderError(f"创建会话失败: {json.dumps(result, ensure_ascii=False)}")
        return cid

    def run(
        self,
        query: str,
        stream: bool = False,
        conversation_id: Optional[str] = None,
    ) -> Any:
        payload: Dict[str, Any] = {
            "query": query,
            "response_mode": "streaming" if stream else "blocking",
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if self.app_id:
            payload["app_id"] = self.app_id

        if stream:
            return self._stream_request(INTEGRATED_URL, payload)
        return self._request(INTEGRATED_URL, payload)


def get_client(**kwargs: Any) -> AppBuilderClient:
    """快捷工厂函数，从环境变量构造客户端"""
    return AppBuilderClient(**kwargs)


def parse_common_args(description: str = "") -> argparse.ArgumentParser:
    """创建包含公共参数的 ArgumentParser，供各 skill 脚本扩展"""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--token", help="AppBuilder Token（默认读 APPBUILDER_TOKEN 环境变量）")
    parser.add_argument("--app-id", help="AppBuilder 应用 ID（默认读 APPBUILDER_APP_ID 环境变量）")
    parser.add_argument("--stream", action="store_true", help="使用流式输出")
    parser.add_argument("--conversation-id", help="复用已有会话 ID")
    return parser


def client_from_args(args: argparse.Namespace) -> AppBuilderClient:
    """根据 parse_common_args 的解析结果构造客户端"""
    kwargs: Dict[str, Any] = {}
    if getattr(args, "token", None):
        kwargs["token"] = args.token
    if getattr(args, "app_id", None):
        kwargs["app_id"] = args.app_id
    return AppBuilderClient(**kwargs)


def print_response(result: Any, stream: bool = False) -> None:
    """统一输出 API 响应"""
    if stream:
        for chunk in result:
            answer = chunk.get("answer", chunk.get("result", ""))
            if answer:
                print(answer, end="", flush=True)
        print()
    else:
        answer = result.get("answer", result.get("result", ""))
        if answer:
            print(answer)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))


def run_skill_query(args: argparse.Namespace, query: str) -> None:
    """通用 skill 执行入口：构造客户端 -> 调用 -> 输出"""
    try:
        client = client_from_args(args)
    except AppBuilderError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    stream = getattr(args, "stream", False)
    cid = getattr(args, "conversation_id", None)
    try:
        result = client.run(query, stream=stream, conversation_id=cid)
    except AppBuilderError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    print_response(result, stream=stream)

