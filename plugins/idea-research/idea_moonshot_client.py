"""Kimi (Moonshot) OpenAI-compatible chat client.

Uses ``POST {base_url}/chat/completions`` with ``Authorization: Bearer``.
See https://platform.kimi.com/docs/api/overview — base_url typically
``https://api.moonshot.cn/v1``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from idea_dashscope_client import ChatResult
from idea_research_inline.llm_json_parser import LlmJsonParseError, parse_llm_json
from idea_research_inline.vendor_client import (
    VendorAuthError,
    VendorClient,
    VendorError,
    VendorFormatError,
    VendorNetworkError,
    VendorQuotaError,
    VendorRateLimitError,
    VendorTimeoutError,
)

CHAT_COMPLETIONS_PATH = "/chat/completions"


def map_dashscope_model_to_moonshot(dashscope_model: str) -> str:
    """Pick a Moonshot chat model from a pipeline Qwen model name."""

    m = (dashscope_model or "").lower()
    if "max" in m or "turbo" in m:
        return "moonshot-v1-128k"
    if "plus" in m or "qwen" in m:
        return "moonshot-v1-32k"
    return "moonshot-v1-8k"


class MoonshotClient(VendorClient):
    """OpenAI-compatible chat for Kimi / Moonshot."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        api_key: str | None,
        base_url: str = "https://api.moonshot.cn/v1",
        chat_model: str | None = None,
        default_timeout_s: float = 120.0,
    ) -> None:
        super().__init__(
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            default_timeout_s=default_timeout_s,
        )
        self._http = client
        self._default_chat_model = (chat_model or "").strip() or None

    def _effective_model(self, dashscope_model: str) -> str:
        if self._default_chat_model:
            return self._default_chat_model
        return map_dashscope_model_to_moonshot(dashscope_model)

    def _headers(self) -> dict[str, str]:
        if not (self.api_key or "").strip():
            raise VendorAuthError(
                "Kimi (Moonshot) API key not configured — add it under Settings → AI Keys",
            )
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _post_chat(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        url = self.base_url + CHAT_COMPLETIONS_PATH
        try:
            r = await self._http.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=timeout or self.default_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise VendorTimeoutError(f"timeout posting {url}") from exc
        except httpx.HTTPError as exc:
            raise VendorNetworkError(f"http error posting {url}: {exc}") from exc
        return self._parse_openai_response(r)

    def _parse_openai_response(self, r: httpx.Response) -> dict[str, Any]:
        if r.status_code == 401:
            raise VendorAuthError(
                f"moonshot auth failed (401): {r.text[:200]!r}",
                status_code=r.status_code,
            )
        if r.status_code == 429:
            raise VendorRateLimitError(
                f"moonshot rate limited (429): {r.text[:200]!r}",
                status_code=r.status_code,
            )
        if r.status_code in (402, 403):
            raise VendorQuotaError(
                f"moonshot quota/forbidden ({r.status_code}): {r.text[:200]!r}",
                status_code=r.status_code,
            )
        if r.status_code >= 500:
            raise VendorNetworkError(
                f"moonshot upstream {r.status_code}: {r.text[:200]!r}",
                status_code=r.status_code,
            )
        if r.status_code != 200:
            txt = (r.text or "").strip()
            try:
                err_body = json.loads(txt) if txt.startswith("{") else {}
            except json.JSONDecodeError:
                err_body = {}
            err = err_body.get("error") if isinstance(err_body, dict) else None
            msg = str((err or {}).get("message") or txt[:200] or r.status_code)
            if r.status_code == 400 and (
                "balance" in msg.lower() or "quota" in msg.lower() or "欠费" in msg
            ):
                raise VendorQuotaError(msg, status_code=r.status_code)
            raise VendorNetworkError(
                f"moonshot unexpected {r.status_code}: {msg}",
                status_code=r.status_code,
            )
        try:
            return r.json()
        except json.JSONDecodeError as exc:
            raise VendorFormatError(f"moonshot non-json response: {r.text[:200]!r}") from exc

    @staticmethod
    def _extract_message_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        msg = (choices[0] or {}).get("message") or {}
        return str(msg.get("content") or "")

    @staticmethod
    def _usage_dict(payload: dict[str, Any]) -> dict[str, int]:
        u = payload.get("usage") or {}
        if not isinstance(u, dict):
            return {}
        out: dict[str, int] = {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if k in u and u[k] is not None:
                try:
                    out[k] = int(u[k])
                except (TypeError, ValueError):
                    pass
        return out

    async def chat_completion(
        self,
        *,
        system: str,
        user: str,
        model: str = "qwen-max",
        response_json: bool = False,
        expected_keys: list[str] | None = None,
        temperature: float = 0.5,
        max_tokens: int | None = None,
        retries: int = 1,
    ) -> ChatResult:
        msmodel = self._effective_model(model)
        payload: dict[str, Any] = {
            "model": msmodel,
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": user},
            ],
            "temperature": float(temperature),
        }
        if max_tokens:
            payload["max_tokens"] = int(max_tokens)
        if response_json:
            payload["response_format"] = {"type": "json_object"}

        last_exc: Exception | None = None
        for attempt in range(max(1, retries + 1)):
            try:
                resp = await self._post_chat(payload)
            except (VendorTimeoutError, VendorRateLimitError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            content = self._extract_message_content(resp)
            usage = self._usage_dict(resp)
            parsed: Any = None
            if response_json:
                try:
                    parsed = parse_llm_json(content, expected_keys=expected_keys)
                except LlmJsonParseError as exc:
                    if attempt < retries:
                        payload["messages"][0]["content"] = (
                            (system or "")
                            + "\n\n你的上一个输出无法被 json.loads 解析，请只输出严格 JSON。"
                        )
                        last_exc = exc
                        continue
                    raise VendorFormatError(str(exc)) from exc
            return ChatResult(
                content=content,
                model=msmodel,
                parsed_json=parsed,
                usage=usage,
                raw=resp,
            )
        if last_exc is not None:
            raise last_exc
        raise VendorError("moonshot chat_completion exhausted retries with no error captured")


__all__ = ["CHAT_COMPLETIONS_PATH", "MoonshotClient", "map_dashscope_model_to_moonshot"]
