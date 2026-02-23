"""
BrowserUseRunner - browser-use AI Agent 集成

封装 browser-use 库的调用，与 Playwright 直接操作完全解耦。
通过 BrowserManager.cdp_url 复用已有浏览器，或让 browser-use 自行创建。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .manager import BrowserManager

logger = logging.getLogger(__name__)


class _BrowserUseLLMProxy:
    """
    browser-use 会直接访问 llm.provider / llm.model。
    但 langchain_openai.ChatOpenAI 往往不允许动态挂载新属性（pydantic/slots），
    因此用一个轻量代理对象显式提供这两个字段，其余属性/方法全部转发。
    """

    def __init__(self, inner: Any, *, provider: str, model: str):
        self._inner = inner
        self.provider = provider
        self.model = model

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def _ensure_browser_use_llm_contract(llm_obj: Any, *, provider: str, model: str) -> Any:
    """返回满足 browser-use 契约的 llm 对象（必要时会用代理包装）。"""
    if hasattr(llm_obj, "provider") and hasattr(llm_obj, "model"):
        return llm_obj

    try:
        if not hasattr(llm_obj, "provider"):
            llm_obj.provider = provider
        if not hasattr(llm_obj, "model"):
            llm_obj.model = model
        if hasattr(llm_obj, "provider") and hasattr(llm_obj, "model"):
            return llm_obj
    except Exception:
        pass

    return _BrowserUseLLMProxy(llm_obj, provider=provider, model=model)


class BrowserUseRunner:
    """封装 browser-use Agent 执行 browser_task。"""

    def __init__(self, manager: BrowserManager):
        self._manager = manager
        self._llm_config: dict | None = None

    def set_llm_config(self, config: dict) -> None:
        self._llm_config = config
        logger.info(f"[BrowserUseRunner] LLM config set: model={config.get('model')}")

    async def run_task(self, task: str, max_steps: int = 15) -> dict:
        """
        使用 browser-use Agent 自主完成浏览器任务。

        Args:
            task: 任务描述
            max_steps: 最大执行步骤数
        """
        if not task:
            return {"success": False, "error": "task is required"}

        try:
            from browser_use import Agent as BUAgent
            from browser_use import Browser as BUBrowser

            if not self._manager.is_ready:
                success = await self._manager.start(visible=True)
                if not success:
                    return {"success": False, "error": "浏览器启动失败"}

            logger.info(f"[BrowserTask] Starting task: {task}")

            bu_browser = None
            cdp_url = self._manager.cdp_url
            if cdp_url:
                try:
                    bu_browser = BUBrowser(cdp_url=cdp_url, is_local=True)
                    logger.info(f"[BrowserTask] Connected via CDP: {cdp_url}")
                except Exception as cdp_error:
                    logger.warning(
                        f"[BrowserTask] CDP connection failed: {cdp_error}, "
                        "falling back to new browser"
                    )

            if bu_browser is None:
                bu_browser = BUBrowser(
                    headless=not self._manager.visible, is_local=True,
                )
                logger.info("[BrowserTask] Created new browser instance")

            llm = self._resolve_llm()
            if llm is None:
                return {
                    "success": False,
                    "error": "No LLM configured. Please set LLM config or set "
                    "OPENAI_API_KEY environment variable.",
                }

            agent = BUAgent(
                task=task, llm=llm, browser=bu_browser, max_steps=max_steps,
            )

            _task_timeout = max_steps * 60
            try:
                history = await asyncio.wait_for(agent.run(), timeout=_task_timeout)
            except asyncio.TimeoutError:
                logger.error(
                    f"[BrowserTask] Task timed out after {_task_timeout}s "
                    f"(max_steps={max_steps}): {task}"
                )
                if not cdp_url:
                    try:
                        await bu_browser.close()
                    except Exception:
                        pass
                return {
                    "success": False,
                    "error": f"浏览器任务执行超时 ({_task_timeout}秒)。任务: {task}",
                }

            final_result = (
                history.final_result() if hasattr(history, "final_result") else str(history)
            )

            if not cdp_url:
                await bu_browser.close()

            logger.info(f"[BrowserTask] Task completed: {task}")

            return {
                "success": True,
                "result": {
                    "task": task,
                    "steps_taken": len(history.history) if hasattr(history, "history") else 0,
                    "final_result": final_result,
                    "message": f"任务完成: {task}",
                },
            }

        except ImportError as e:
            from openakita.tools._import_helper import import_or_hint
            hint = import_or_hint("browser_use") or import_or_hint("langchain_openai") or str(e)
            logger.error(f"[BrowserTask] Import error: {hint}")
            return {"success": False, "error": hint}
        except Exception as e:
            logger.error(f"[BrowserTask] Error: {e}")
            return {"success": False, "error": f"任务执行失败: {str(e)}"}

    def _resolve_llm(self) -> Any | None:
        """三级回退获取 LLM 实例：注入配置 → 环境变量 → ChatBrowserUse。"""

        def _try_langchain(model: str, api_key: str, base_url: str | None) -> Any | None:
            try:
                from langchain_openai import ChatOpenAI
            except ImportError as ie:
                logger.error(
                    f"[BrowserTask] langchain_openai 模块加载失败: {ie}. "
                    "请确认已打包 langchain-openai 依赖。"
                )
                return None
            try:
                llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url)
                return _ensure_browser_use_llm_contract(
                    llm, provider="openai", model=model,
                )
            except Exception as e:
                logger.error(f"[BrowserTask] ChatOpenAI 初始化失败: {e}")
                return None

        # 1. 注入的配置
        if self._llm_config:
            model = self._llm_config.get("model", "")
            api_key = self._llm_config.get("api_key")
            base_url = self._llm_config.get("base_url")

            if api_key:
                llm = _try_langchain(model, api_key, base_url)
                if llm:
                    logger.info(f"[BrowserTask] Using inherited LLM config: {model}")
                    return llm

        # 2. 环境变量
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            base_url = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL")
            model = os.getenv("OPENAI_MODEL", "")
            llm = _try_langchain(model, api_key, base_url)
            if llm:
                logger.info(f"[BrowserTask] Using env LLM: {model}")
                return llm

        # 3. ChatBrowserUse
        try:
            from browser_use import ChatBrowserUse
            llm = ChatBrowserUse()
            logger.info("[BrowserTask] Using ChatBrowserUse")
            return llm
        except Exception:
            pass

        return None
