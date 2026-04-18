"""
System feature handler

Handles system skills related to core system features:
- enable_thinking: Toggle deep thinking
- get_session_logs: Retrieve session logs
- get_tool_info: Retrieve tool information
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...skills.exposure import get_skill_source_roots

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class SystemHandler:
    """System feature handler"""

    TOOLS = [
        "ask_user",
        "enable_thinking",
        "get_session_logs",
        "get_tool_info",
        "generate_image",
        "set_task_timeout",
        "get_workspace_map",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """Handle tool call"""
        if tool_name == "ask_user":
            # ask_user is normally intercepted by ReasoningEngine during the ACT phase and should not reach here.
            # This is a defensive fallback: if it does arrive, return the question text instead of raising an error.
            question = params.get("question", "")
            logger.warning(
                f"[SystemHandler] ask_user reached handler (should be intercepted): {question[:80]}"
            )
            return question or "(waiting for user reply)"
        elif tool_name == "enable_thinking":
            return self._enable_thinking(params)
        elif tool_name == "get_session_logs":
            return self._get_session_logs(params)
        elif tool_name == "get_tool_info":
            return self._get_tool_info(params)
        elif tool_name == "generate_image":
            return await self._generate_image(params)
        elif tool_name == "set_task_timeout":
            return self._set_task_timeout(params)
        elif tool_name == "get_workspace_map":
            return self._get_workspace_map()
        else:
            return f"❌ Unknown system tool: {tool_name}"

    def _enable_thinking(self, params: dict) -> str:
        """Toggle deep thinking mode"""
        enabled = params["enabled"]
        reason = params.get("reason", "")

        self.agent.brain.set_thinking_mode(enabled)

        if enabled:
            logger.info(f"Thinking mode enabled by LLM: {reason}")
            return f"✅ Deep thinking mode enabled. Reason: {reason}\nSubsequent replies will use stronger reasoning."
        else:
            logger.info(f"Thinking mode disabled by LLM: {reason}")
            return f"✅ Deep thinking mode disabled. Reason: {reason}\nFast response mode will be used."

    def _get_session_logs(self, params: dict) -> str:
        """Retrieve session logs"""
        from ...logging import get_session_log_buffer

        count = params.get("count", 20)
        # The `level` parameter was renamed to `level_filter` (fixes parameter name mismatch)
        level_filter = params.get("level_filter") or params.get("level")

        log_buffer = get_session_log_buffer()
        logs = log_buffer.get_logs(count=count, level_filter=level_filter)

        if not logs:
            return "No session logs"

        output = f"Most recent {len(logs)} log entries:\n\n"
        for log in logs:
            output += f"[{log['level']}] {log['module']}: {log['message']}\n"

        return output

    def _get_tool_info(self, params: dict) -> str:
        """Retrieve tool information"""
        tool_name_to_query = params["tool_name"]
        return self.agent.tool_catalog.get_tool_info_formatted(tool_name_to_query)

    def _set_task_timeout(self, params: dict) -> str:
        """Dynamically adjust the timeout policy for the current task"""
        pt = int(params.get("progress_timeout_seconds") or 0)
        ht = int(params.get("hard_timeout_seconds") or 0)
        reason = params.get("reason", "")

        if pt <= 0:
            return "❌ progress_timeout_seconds must be a positive integer (seconds)"
        if ht < 0:
            return "❌ hard_timeout_seconds cannot be negative"

        monitor = getattr(self.agent, "_current_task_monitor", None)
        if not monitor:
            return "⚠️ No task is currently executing; cannot adjust timeout policy"

        monitor.timeout_seconds = pt
        monitor.hard_timeout_seconds = ht
        logger.info(f"[TaskTimeout] Updated by LLM: progress={pt}s hard={ht}s reason={reason}")
        return f"✅ Updated current task timeout policy: no-progress timeout={pt}s, hard timeout={ht if ht else 0}s (0=disabled). Reason: {reason}"

    def _get_workspace_map(self) -> str:
        """Return the workspace directory structure and key path descriptions"""
        from ...config import settings

        root = settings.project_root

        try:
            identity_rel = settings.identity_path.relative_to(root)
        except ValueError:
            identity_rel = settings.identity_path
        try:
            logs_rel = settings.log_dir_path.relative_to(root)
        except ValueError:
            logs_rel = settings.log_dir_path

        lines = [
            "## Workspace path map",
            "",
            f"- **Project root**: {root}",
            f"- **User data dir**: {settings.openakita_home}",
            f"- **Identity**: {identity_rel}/ — identity documents (SOUL.md, AGENT.md, USER.md, MEMORY.md)",
            "- **Skills**: The skill system is multi-source; skills may come from builtin, user workspace, or project directories.",
            "- **Skills Rule**: Do not guess skill file paths from the workspace map; use list_skills / get_skill_info to view the actual source and path.",
            "- **Data**: data/ — runtime data root",
            "  - sessions/ — session persistence",
            "  - memory/ — memory storage",
            "  - plans/ — plan files",
            "  - media/ — IM media files",
            "  - temp/ — temporary files (safe to read/write)",
            "  - llm_debug/ — LLM debug logs",
            "  - scheduler/ — scheduled tasks",
            "  - screenshots/ — desktop/browser screenshots",
            "  - generated_images/ — AI-generated images",
            "  - tool_overflow/ — overflow files for large tool outputs",
            "  - llm_endpoints.json — LLM endpoint configuration",
            "  - agent.db — SQLite database (memory/sessions)",
            f"- **Logs**: {logs_rel}/",
            f"  - {settings.log_file_prefix}.log — main log (rolling, latest)",
            "  - error.log — error log (rolls daily)",
        ]

        skill_roots = [
            f"  - {origin}: {path}"
            for origin, path in get_skill_source_roots(
                project_root=root,
                user_skills_dir=settings.skills_path,
            )
        ]
        lines[6:6] = skill_roots

        return "\n".join(line for line in lines if line is not None)

    _GENERATE_IMAGE_FAIL_HINT = (
        "\n[Behavior guidance] The image generation endpoint is temporarily unavailable; "
        "please inform the user of the failure reason above directly. "
        "Do not attempt to substitute image generation via run_shell, pip install, or any other means."
    )

    async def _generate_image(self, params: dict) -> str:
        """
        Text-to-image: call the Qwen-Image sync endpoint, download the image, and write it to disk.

        API reference (Tongyi Bailian): https://help.aliyun.com/zh/model-studio/qwen-image-api
        """
        import json
        import time

        import httpx

        from ...config import settings

        _hint = self._GENERATE_IMAGE_FAIL_HINT

        prompt = (params.get("prompt") or "").strip()
        if not prompt:
            return "❌ prompt cannot be empty"

        api_key = (getattr(settings, "dashscope_api_key", "") or "").strip()
        if not api_key:
            return f"❌ DASHSCOPE_API_KEY is not configured; cannot generate image{_hint}"

        model = (params.get("model") or "qwen-image-max").strip()
        negative_prompt = (params.get("negative_prompt") or "").strip()
        size = (params.get("size") or "1664*928").strip()
        prompt_extend = params.get("prompt_extend", True)
        watermark = params.get("watermark", False)
        seed = params.get("seed")
        output_path = (params.get("output_path") or "").strip()

        # Allow override via config (useful for cross-region / private networks)
        api_url = (getattr(settings, "dashscope_image_api_url", "") or "").strip()
        if not api_url:
            api_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"

        body: dict[str, Any] = {
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [{"text": prompt}],
                    }
                ]
            },
            "parameters": {
                "prompt_extend": bool(prompt_extend),
                "watermark": bool(watermark),
                "size": size,
            },
        }
        if negative_prompt:
            body["parameters"]["negative_prompt"] = negative_prompt
        if seed is not None:
            body["parameters"]["seed"] = int(seed)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

        from ...channels.retry import async_with_retry
        from ...llm.providers.proxy_utils import extract_connection_error, get_httpx_client_kwargs

        _dl_headers = {"User-Agent": "OpenAkita/1.0 (image-download)"}

        async def _download_image(url: str) -> bytes:
            """Try direct download first, then proxy: domestic CDNs usually don't need a proxy, and direct is faster and more reliable."""
            # First attempt: direct download without proxy
            try:
                async with httpx.AsyncClient(
                    timeout=60, trust_env=False, follow_redirects=True
                ) as dl_client:
                    resp = await dl_client.get(url, headers=_dl_headers)
                    resp.raise_for_status()
                    return resp.content
            except Exception as direct_err:
                logger.debug("generate_image: direct download failed: %s", direct_err)
            # Second attempt: retry using global proxy configuration
            async with httpx.AsyncClient(
                **get_httpx_client_kwargs(timeout=60), follow_redirects=True
            ) as dl_client:
                resp = await dl_client.get(url, headers=_dl_headers)
                resp.raise_for_status()
                return resp.content

        # 1) Generate image (returns a temporary URL)
        t0 = time.time()
        try:
            async with httpx.AsyncClient(
                **get_httpx_client_kwargs(timeout=180), follow_redirects=True
            ) as client:
                resp = await client.post(api_url, headers=headers, json=body)
                if resp.status_code >= 400:
                    return f"❌ Image generation failed: HTTP {resp.status_code}\n{(resp.text or '')[:800]}{_hint}"
                try:
                    data = resp.json()
                except Exception as e:
                    preview = (resp.text or "")[:800]
                    return f"❌ Image generation returned non-JSON ({type(e).__name__}: {e})\n{preview}{_hint}"

                # Response shape: output.choices[0].message.content[0].image
                image_url = None
                try:
                    image_url = (
                        data.get("output", {})
                        .get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", [{}])[0]
                        .get("image")
                    )
                except Exception:
                    image_url = None

                request_id = data.get("request_id") or data.get("requestId")

                if not image_url:
                    code = data.get("code")
                    msg = data.get("message")
                    return f"❌ Image generation returned unexpected response: image field missing (code={code}, message={msg}){_hint}"

            # 2) Download and write to disk (dedicated client, fresh connection per retry)
            if output_path:
                out_path = Path(output_path)
            else:
                out_dir = Path("data") / "generated_images"
                out_dir.mkdir(parents=True, exist_ok=True)
                suffix = request_id or str(int(time.time()))
                out_path = out_dir / f"{model}_{suffix}.png"

            out_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                img_bytes = await async_with_retry(
                    _download_image, image_url,
                    max_retries=3, base_delay=2.0, operation_name="download_generated_image",
                )
                out_path.write_bytes(img_bytes)
            except Exception as e:
                detail = extract_connection_error(e)
                from urllib.parse import urlparse
                host = urlparse(image_url).hostname or image_url[:60]
                return f"❌ Image download failed (network error, target: {host}): {detail}{_hint}"

        except httpx.HTTPError as e:
            detail = extract_connection_error(e)
            return f"❌ Image generation request failed (network error): {detail}{_hint}"
        except Exception as e:
            return f"❌ Image generation failed (exception): {type(e).__name__}: {e}{_hint}"

        elapsed_ms = int((time.time() - t0) * 1000)
        return json.dumps(
            {
                "ok": True,
                "model": model,
                "image_url": image_url,
                "saved_to": str(out_path),
                "request_id": request_id,
                "elapsed_ms": elapsed_ms,
                "hint": "To actually deliver the image to the user, call deliver_artifacts(artifacts=[{type:'image', path:saved_to}]). Call only once — do not merely state in text that the image has been sent.",
            },
            ensure_ascii=False,
            indent=2,
        )


def create_handler(agent: "Agent"):
    """Create system feature handler"""
    handler = SystemHandler(agent)
    return handler.handle
