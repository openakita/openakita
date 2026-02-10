"""
系统功能处理器

处理系统功能相关的系统技能：
- enable_thinking: 控制深度思考
- get_session_logs: 获取会话日志
- get_tool_info: 获取工具信息
"""

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)


class SystemHandler:
    """系统功能处理器"""

    TOOLS = [
        "ask_user",
        "enable_thinking",
        "get_session_logs",
        "get_tool_info",
        "generate_image",
        "set_task_timeout",
    ]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        """处理工具调用"""
        if tool_name == "ask_user":
            # ask_user 正常由 ReasoningEngine 在 ACT 阶段拦截，不会到达此处
            # 此为防御性兜底：若意外到达，返回问题文本而不是报错
            question = params.get("question", "")
            logger.warning(f"[SystemHandler] ask_user reached handler (should be intercepted): {question[:80]}")
            return question or "（等待用户回复）"
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
        else:
            return f"❌ Unknown system tool: {tool_name}"

    def _enable_thinking(self, params: dict) -> str:
        """控制深度思考模式"""
        enabled = params["enabled"]
        reason = params.get("reason", "")

        self.agent.brain.set_thinking_mode(enabled)

        if enabled:
            logger.info(f"Thinking mode enabled by LLM: {reason}")
            return f"✅ 已启用深度思考模式。原因: {reason}\n后续回复将使用更强的推理能力。"
        else:
            logger.info(f"Thinking mode disabled by LLM: {reason}")
            return f"✅ 已关闭深度思考模式。原因: {reason}\n将使用快速响应模式。"

    def _get_session_logs(self, params: dict) -> str:
        """获取会话日志"""
        from ...logging import get_session_log_buffer

        count = params.get("count", 20)
        # level 参数改为 level_filter（修复参数名不匹配问题）
        level_filter = params.get("level_filter") or params.get("level")

        log_buffer = get_session_log_buffer()
        logs = log_buffer.get_logs(count=count, level_filter=level_filter)

        if not logs:
            return "没有会话日志"

        output = f"最近 {len(logs)} 条日志:\n\n"
        for log in logs:
            output += f"[{log['level']}] {log['module']}: {log['message']}\n"

        return output

    def _get_tool_info(self, params: dict) -> str:
        """获取工具信息"""
        tool_name_to_query = params["tool_name"]
        return self.agent.tool_catalog.get_tool_info_formatted(tool_name_to_query)

    def _set_task_timeout(self, params: dict) -> str:
        """动态调整当前任务的超时策略"""
        pt = int(params.get("progress_timeout_seconds") or 0)
        ht = int(params.get("hard_timeout_seconds") or 0)
        reason = params.get("reason", "")

        if pt <= 0:
            return "❌ progress_timeout_seconds 必须为正整数（秒）"
        if ht < 0:
            return "❌ hard_timeout_seconds 不能为负数"

        monitor = getattr(self.agent, "_current_task_monitor", None)
        if not monitor:
            return "⚠️ 当前没有正在执行的任务，无法调整超时策略"

        monitor.timeout_seconds = pt
        monitor.hard_timeout_seconds = ht
        logger.info(f"[TaskTimeout] Updated by LLM: progress={pt}s hard={ht}s reason={reason}")
        return f"✅ 已更新当前任务超时策略：无进展超时={pt}s，硬超时={ht if ht else 0}s（0=禁用）。原因：{reason}"

    async def _generate_image(self, params: dict) -> str:
        """
        文生图：调用 Qwen-Image 同步接口，下载图片并落盘。

        API 参考（通义百炼）：https://help.aliyun.com/zh/model-studio/qwen-image-api
        """
        import json
        import time

        import httpx

        from ...config import settings

        prompt = (params.get("prompt") or "").strip()
        if not prompt:
            return "❌ prompt 不能为空"

        api_key = (getattr(settings, "dashscope_api_key", "") or "").strip()
        if not api_key:
            return "❌ 未配置 DASHSCOPE_API_KEY，无法生成图片"

        model = (params.get("model") or "qwen-image-max").strip()
        negative_prompt = (params.get("negative_prompt") or "").strip()
        size = (params.get("size") or "1664*928").strip()
        prompt_extend = params.get("prompt_extend", True)
        watermark = params.get("watermark", False)
        seed = params.get("seed")
        output_path = (params.get("output_path") or "").strip()

        # 允许通过配置覆盖（便于跨地域/私有网络）
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

        # 1) 生成图片（返回临时 URL）
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
                resp = await client.post(api_url, headers=headers, json=body)
                if resp.status_code >= 400:
                    return (
                        f"❌ 图片生成失败: HTTP {resp.status_code}\n"
                        f"{(resp.text or '')[:800]}"
                    )
                try:
                    data = resp.json()
                except Exception as e:
                    preview = (resp.text or "")[:800]
                    return f"❌ 图片生成返回非 JSON（{type(e).__name__}: {e}）\n{preview}"

                # 兼容响应结构：output.choices[0].message.content[0].image
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
                    # 失败结构：code/message
                    code = data.get("code")
                    msg = data.get("message")
                    return f"❌ 图片生成返回异常：未找到 image 字段（code={code}, message={msg}）"

                # 2) 下载并落盘
                if output_path:
                    out_path = Path(output_path)
                else:
                    out_dir = Path("data") / "generated_images"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    suffix = request_id or str(int(time.time()))
                    out_path = out_dir / f"{model}_{suffix}.png"

                out_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    img_resp = await client.get(image_url)
                    if img_resp.status_code >= 400:
                        return f"❌ 图片下载失败: HTTP {img_resp.status_code}"
                    out_path.write_bytes(img_resp.content)
                except httpx.HTTPError as e:
                    return f"❌ 图片下载失败（网络错误）：{type(e).__name__}: {e}"

        except httpx.HTTPError as e:
            return f"❌ 图片生成请求失败（网络错误）：{type(e).__name__}: {e}"
        except Exception as e:
            return f"❌ 图片生成失败（异常）：{type(e).__name__}: {e}"

        elapsed_ms = int((time.time() - t0) * 1000)
        return json.dumps(
            {
                "ok": True,
                "model": model,
                "image_url": image_url,
                "saved_to": str(out_path),
                "request_id": request_id,
                "elapsed_ms": elapsed_ms,
                "hint": "如需发送到 IM，请使用 deliver_artifacts(type=image, path=saved_to)。",
            },
            ensure_ascii=False,
            indent=2,
        )


def create_handler(agent: "Agent"):
    """创建系统功能处理器"""
    handler = SystemHandler(agent)
    return handler.handle
