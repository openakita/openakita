"""
系统配置处理器

统一处理 system_config 工具的所有 action:
- discover: 内省 Settings.model_fields 动态发现可配置项
- get: 查看当前配置
- set: 修改配置 (.env + 热重载)
- add_endpoint / remove_endpoint / test_endpoint: LLM 端点管理
- set_ui: UI 偏好 (主题/语言)
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...core.agent import Agent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 黑名单: 不允许通过聊天修改的字段
# ---------------------------------------------------------------------------
_READONLY_FIELDS = frozenset({
    "project_root",
    "database_path",
    "session_storage_path",
    "log_dir",
    "log_file_prefix",
})

# ---------------------------------------------------------------------------
# 需重启才能生效的字段
# ---------------------------------------------------------------------------
_RESTART_REQUIRED_FIELDS = frozenset({
    "telegram_enabled", "telegram_bot_token", "telegram_webhook_url",
    "telegram_pairing_code", "telegram_require_pairing", "telegram_proxy",
    "feishu_enabled", "feishu_app_id", "feishu_app_secret",
    "wework_enabled", "wework_corp_id", "wework_token", "wework_encoding_aes_key",
    "wework_callback_port", "wework_callback_host",
    "dingtalk_enabled", "dingtalk_client_id", "dingtalk_client_secret",
    "onebot_enabled", "onebot_ws_url", "onebot_access_token",
    "qqbot_enabled", "qqbot_app_id", "qqbot_app_secret", "qqbot_sandbox",
    "qqbot_mode", "qqbot_webhook_port", "qqbot_webhook_path",
    "wechat_enabled", "wechat_token",
    "orchestration_enabled", "orchestration_mode",
    "orchestration_bus_address", "orchestration_pub_address",
    "embedding_model", "embedding_device",
})

# ---------------------------------------------------------------------------
# 敏感字段模式
# ---------------------------------------------------------------------------
_SENSITIVE_PATTERN = re.compile(r"(api_key|secret|token|password)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# 分类推断规则: (前缀/字段名元组, 分类名)
# ---------------------------------------------------------------------------
_CATEGORY_RULES: list[tuple[tuple[str, ...], str]] = [
    (("anthropic_", "default_model", "max_tokens"), "LLM"),
    (("dashscope_",), "LLM/DashScope"),
    (("agent_name", "max_iterations", "force_tool_call",
      "tool_max_parallel", "allow_parallel", "selfcheck_"), "Agent"),
    (("thinking_",), "Agent/思考模式"),
    (("im_chain_push",), "IM/思维链推送"),
    (("progress_timeout", "hard_timeout"), "Agent/超时"),
    (("log_",), "日志"),
    (("http_proxy", "https_proxy", "all_proxy", "force_ipv4"), "代理"),
    (("model_download_",), "模型下载"),
    (("embedding_", "search_backend"), "Embedding/记忆搜索"),
    (("memory_",), "记忆"),
    (("github_",), "GitHub"),
    (("telegram_",), "IM/Telegram"),
    (("feishu_",), "IM/飞书"),
    (("wework_",), "IM/企业微信"),
    (("dingtalk_",), "IM/钉钉"),
    (("onebot_",), "IM/OneBot"),
    (("qqbot_",), "IM/QQ"),
    (("wechat_",), "IM/微信"),
    (("session_",), "会话"),
    (("scheduler_",), "定时任务"),
    (("orchestration_",), "多Agent协同"),
    (("persona_",), "人格"),
    (("proactive_",), "活人感"),
    (("sticker_",), "表情包"),
    (("desktop_notify_",), "桌面通知"),
    (("tracing_",), "追踪"),
    (("evaluation_",), "评估"),
    (("ui_",), "UI偏好"),
]


def _infer_category(field_name: str) -> str:
    """根据字段名推断配置分类"""
    for patterns, category in _CATEGORY_RULES:
        for p in patterns:
            if field_name == p or field_name.startswith(p):
                return category
    return "其他"


def _get_field_category(field_name: str, field_info: Any) -> str:
    """获取字段分类，优先读 json_schema_extra 声明"""
    extra = getattr(field_info, "json_schema_extra", None) or {}
    if isinstance(extra, dict) and "category" in extra:
        return extra["category"]
    return _infer_category(field_name)


def _is_sensitive(field_name: str) -> bool:
    return bool(_SENSITIVE_PATTERN.search(field_name))


def _needs_restart(field_name: str, field_info: Any) -> bool:
    extra = getattr(field_info, "json_schema_extra", None) or {}
    if isinstance(extra, dict) and extra.get("needs_restart"):
        return True
    return field_name in _RESTART_REQUIRED_FIELDS


def _mask_value(value: Any) -> str:
    """脱敏处理"""
    s = str(value)
    if len(s) > 6:
        return s[:4] + "***" + s[-2:]
    return "***"


def _unique_env_key(base: str, used: set[str]) -> str:
    """Return *base* if unused, otherwise append _2, _3, … until unique."""
    if not base or base not in used:
        return base
    for i in range(2, 100):
        candidate = f"{base}_{i}"
        if candidate not in used:
            return candidate
    return f"{base}_{int(__import__('time').time())}"


def _update_env_content(existing: str, entries: dict[str, str]) -> str:
    """合并 entries 到现有 .env 内容（保留注释和顺序）"""
    lines = existing.splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in entries:
            value = entries[key]
            if value == "":
                updated_keys.add(key)
                continue
            new_lines.append(f"{key}={value}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    for key, value in entries.items():
        if key not in updated_keys and value != "":
            new_lines.append(f"{key}={value}")

    return "\n".join(new_lines) + "\n"


class ConfigHandler:
    """系统配置处理器"""

    TOOLS = ["system_config"]

    def __init__(self, agent: "Agent"):
        self.agent = agent

    async def handle(self, tool_name: str, params: dict[str, Any]) -> str:
        action = params.get("action", "")
        try:
            if action == "discover":
                return self._discover(params)
            elif action == "get":
                return self._get_config(params)
            elif action == "set":
                return self._set_config(params)
            elif action == "add_endpoint":
                return self._add_endpoint(params)
            elif action == "remove_endpoint":
                return self._remove_endpoint(params)
            elif action == "test_endpoint":
                return await self._test_endpoint(params)
            elif action == "set_ui":
                return self._set_ui(params)
            elif action == "manage_provider":
                return self._manage_provider(params)
            else:
                return f"未知的 action: {action}。支持: discover, get, set, add_endpoint, remove_endpoint, test_endpoint, set_ui, manage_provider"
        except Exception as e:
            logger.error(f"[ConfigHandler] action={action} failed: {e}", exc_info=True)
            return f"配置操作失败: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # discover: 内省 Settings 动态发现可配置项
    # ------------------------------------------------------------------
    def _discover(self, params: dict) -> str:
        from ...config import Settings, settings

        category_filter = (params.get("category") or "").strip()

        grouped: dict[str, list[dict]] = {}
        for field_name, field_info in Settings.model_fields.items():
            if field_name in _READONLY_FIELDS:
                continue

            cat = _get_field_category(field_name, field_info)
            if category_filter and cat != category_filter:
                # 模糊匹配: 用户输入 "Agent" 也能匹配 "Agent/思考模式"
                if category_filter not in cat:
                    continue

            current_val = getattr(settings, field_name, None)
            default_val = field_info.default
            if hasattr(field_info, "default_factory") and field_info.default_factory:
                try:
                    default_val = field_info.default_factory()
                except Exception:
                    default_val = "(dynamic)"

            sensitive = _is_sensitive(field_name)
            display_current = _mask_value(current_val) if sensitive and current_val else str(current_val)
            display_default = str(default_val)

            annotation = field_info.annotation
            type_name = getattr(annotation, "__name__", str(annotation))

            entry = {
                "field": field_name,
                "env_name": field_name.upper(),
                "description": field_info.description or "",
                "type": type_name,
                "current": display_current,
                "default": display_default,
                "is_modified": current_val != default_val,
                "is_sensitive": sensitive,
                "needs_restart": _needs_restart(field_name, field_info),
            }

            grouped.setdefault(cat, []).append(entry)

        if not grouped:
            if category_filter:
                return f"未找到分类 \"{category_filter}\" 的配置项。调用 action=discover 不带 category 可查看所有分类。"
            return "未发现可配置项。"

        lines = [f"## 可配置项（共 {sum(len(v) for v in grouped.values())} 项，{len(grouped)} 个分类）\n"]
        for cat in sorted(grouped.keys()):
            items = grouped[cat]
            modified_count = sum(1 for it in items if it["is_modified"])
            lines.append(f"### {cat} ({len(items)} 项, {modified_count} 项已修改)")
            for it in items:
                mark = "**[已修改]** " if it["is_modified"] else ""
                restart_mark = " ⚠️需重启" if it["needs_restart"] else ""
                sensitive_mark = " 🔒" if it["is_sensitive"] else ""
                lines.append(
                    f"- `{it['env_name']}` ({it['type']}): {it['description']}"
                    f"{sensitive_mark}{restart_mark}"
                )
                lines.append(f"  当前: {mark}{it['current']}  |  默认: {it['default']}")
            lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # get: 查看当前配置
    # ------------------------------------------------------------------
    def _get_config(self, params: dict) -> str:
        from ...config import Settings, settings

        category_filter = (params.get("category") or "").strip()
        keys_filter = params.get("keys") or []

        parts: list[str] = []

        # 如果指定了 keys，直接查询
        if keys_filter:
            parts.append("## 指定配置项\n")
            for key in keys_filter:
                field_name = key.lower()
                if field_name not in Settings.model_fields:
                    parts.append(f"- `{key}`: ❌ 不存在")
                    continue
                val = getattr(settings, field_name, None)
                if _is_sensitive(field_name) and val:
                    val = _mask_value(val)
                field_info = Settings.model_fields[field_name]
                parts.append(f"- `{field_name.upper()}`: {val}  ({field_info.description or ''})")
            return "\n".join(parts)

        # 按分类返回配置概览
        grouped: dict[str, list[str]] = {}
        for field_name, field_info in Settings.model_fields.items():
            if field_name in _READONLY_FIELDS:
                continue
            cat = _get_field_category(field_name, field_info)
            if category_filter and category_filter not in cat:
                continue
            val = getattr(settings, field_name, None)
            if _is_sensitive(field_name) and val:
                val = _mask_value(val)
            grouped.setdefault(cat, []).append(
                f"- `{field_name.upper()}` = {val}"
            )

        # 追加 LLM 端点概览（当查看 LLM 分类或无过滤时）
        if not category_filter or "LLM" in category_filter:
            ep_lines = self._format_endpoints_summary()
            if ep_lines:
                grouped.setdefault("LLM/端点", []).extend(ep_lines)

        if not grouped:
            return "未找到匹配的配置项。"

        parts.append("## 当前配置" + (f" (分类: {category_filter})" if category_filter else "") + "\n")
        for cat in sorted(grouped.keys()):
            parts.append(f"### {cat}")
            parts.extend(grouped[cat])
            parts.append("")

        return "\n".join(parts)

    def _format_endpoints_summary(self) -> list[str]:
        """格式化 LLM 端点摘要"""
        try:
            from ...llm.config import load_endpoints_config
            endpoints, compiler_eps, stt_eps, _ = load_endpoints_config()
        except Exception:
            return ["- ⚠️ 无法读取端点配置"]

        lines = []
        for _i, ep in enumerate(endpoints, 1):
            key_info = ""
            if ep.api_key_env:
                has_key = bool(os.environ.get(ep.api_key_env))
                key_info = f" | Key: {'✅' if has_key else '❌'}{ep.api_key_env}"
            lines.append(
                f"- **{ep.name}** (P{ep.priority}): {ep.provider}/{ep.model}"
                f" | {ep.api_type}{key_info}"
            )

        if compiler_eps:
            lines.append(f"- Compiler 端点: {len(compiler_eps)} 个")
        if stt_eps:
            lines.append(f"- STT 端点: {len(stt_eps)} 个")
        if not endpoints:
            lines.append("- (无端点)")
        return lines

    # ------------------------------------------------------------------
    # set: 修改配置
    # ------------------------------------------------------------------
    def _set_config(self, params: dict) -> str:
        from ...config import Settings, runtime_state, settings

        updates = params.get("updates")
        if not updates or not isinstance(updates, dict):
            return "❌ updates 参数缺失或格式错误，应为 {\"KEY\": \"value\"} 字典"

        # 项目根目录
        project_root = Path(settings.project_root)
        env_path = project_root / ".env"

        changes: list[str] = []
        env_entries: dict[str, str] = {}
        restart_needed: list[str] = []
        errors: list[str] = []

        for env_key, new_value in updates.items():
            field_name = env_key.lower()

            # 黑名单检查
            if field_name in _READONLY_FIELDS:
                errors.append(f"`{env_key}`: 只读字段，不允许修改")
                continue

            # 检查字段是否存在
            if field_name not in Settings.model_fields:
                errors.append(f"`{env_key}`: 未知配置项。可用 action=discover 查看可配置项")
                continue

            field_info = Settings.model_fields[field_name]

            # 类型校验和转换
            _, err = self._validate_value(field_name, field_info, new_value)
            if err:
                errors.append(f"`{env_key}`: {err}")
                continue

            old_value = getattr(settings, field_name, None)
            if _is_sensitive(field_name) and old_value:
                old_display = _mask_value(old_value)
            else:
                old_display = str(old_value)

            new_display = _mask_value(new_value) if _is_sensitive(field_name) else str(new_value)

            env_entries[env_key.upper()] = str(new_value)
            changes.append(f"- `{env_key.upper()}`: {old_display} → {new_display}")

            if _needs_restart(field_name, field_info):
                restart_needed.append(env_key.upper())

        if errors:
            error_lines = "\n".join(f"  {e}" for e in errors)
            if not changes:
                return f"❌ 所有修改都被拒绝:\n{error_lines}"

        # 写入 .env（原子写入 + 备份）
        if env_entries:
            from openakita.utils.atomic_io import safe_write
            existing = ""
            if env_path.exists():
                existing = env_path.read_text(encoding="utf-8", errors="replace")
            new_content = _update_env_content(existing, env_entries)
            safe_write(env_path, new_content)

            # 同步到 os.environ
            for key, value in env_entries.items():
                if value:
                    os.environ[key] = value
                elif key in os.environ:
                    del os.environ[key]

            # 热重载 settings（reload 已跳过 _PERSISTABLE_KEYS 字段）
            changed_fields = settings.reload()
            logger.info(f"[ConfigHandler] set: updated {len(env_entries)} entries, reloaded fields: {changed_fields}")

            # 双重保险：恢复运行时持久化字段（防止旧版 reload 或异常路径覆盖）
            try:
                runtime_state.load()
            except Exception as e:
                logger.warning(f"[ConfigHandler] runtime_state.load failed: {e}")

            # 持久化 runtime_state（如果修改了可持久化的字段）
            try:
                from ...config import _PERSISTABLE_KEYS
                if any(k.lower() in _PERSISTABLE_KEYS for k in env_entries):
                    runtime_state.save()
            except Exception as e:
                logger.warning(f"[ConfigHandler] runtime_state save failed: {e}")

        # 构建响应
        result_lines = ["✅ 配置已更新:\n"] + changes

        if errors:
            result_lines.append("\n⚠️ 部分字段被拒绝:")
            result_lines.extend(f"  {e}" for e in errors)

        if restart_needed:
            result_lines.append(f"\n⚠️ 以下字段需要重启服务才能生效: {', '.join(restart_needed)}")

        return "\n".join(result_lines)

    _INT_CONSTRAINTS: dict[str, tuple[int | None, int | None, str]] = {
        "max_iterations": (15, 10000, "最大迭代次数范围 15~10000，推荐 100~300"),
        "progress_timeout_seconds": (60, None, "无进展超时最小 60 秒"),
        "tool_max_parallel": (1, 32, "并行工具数范围 1~32"),
    }

    def _check_int_constraints(self, field_name: str, value: int) -> str | None:
        spec = self._INT_CONSTRAINTS.get(field_name)
        if not spec:
            return None
        lo, hi, msg = spec
        if lo is not None and value < lo:
            return f"值 {value} 过小。{msg}"
        if hi is not None and value > hi:
            return f"值 {value} 过大。{msg}"
        return None

    def _validate_value(self, field_name: str, field_info: Any, value: Any) -> tuple[Any, str | None]:
        """校验配置值的类型和合法性。返回 (validated_value, error_or_None)"""
        annotation = field_info.annotation

        # 处理 str
        if annotation is str:
            return str(value), None

        # 处理 int
        if annotation is int:
            try:
                v = int(value)
            except (ValueError, TypeError):
                return None, f"需要整数，但收到: {value}"
            constraint_err = self._check_int_constraints(field_name, v)
            if constraint_err:
                return None, constraint_err
            return v, None

        # 处理 bool
        if annotation is bool:
            if isinstance(value, bool):
                return value, None
            s = str(value).lower()
            if s in ("true", "1", "yes", "on"):
                return True, None
            elif s in ("false", "0", "no", "off"):
                return False, None
            return None, f"需要布尔值 (true/false)，但收到: {value}"

        # 处理 list (如 thinking_keywords)
        if hasattr(annotation, "__origin__") and annotation.__origin__ is list:
            if isinstance(value, list):
                return value, None
            return None, f"需要列表类型，但收到: {type(value).__name__}"

        # 处理 Path
        if annotation is Path:
            return None, "路径类型不允许通过聊天修改"

        return str(value), None

    # ------------------------------------------------------------------
    # add_endpoint: 添加 LLM 端点
    # ------------------------------------------------------------------
    def _add_endpoint(self, params: dict) -> str:
        endpoint_data = params.get("endpoint")
        if not endpoint_data or not isinstance(endpoint_data, dict):
            return "❌ 缺少 endpoint 参数"

        name = endpoint_data.get("name", "").strip()
        provider = endpoint_data.get("provider", "").strip()
        model = endpoint_data.get("model", "").strip()
        if not name or not provider or not model:
            return "❌ endpoint 必须包含 name, provider, model"

        target = (params.get("target") or "main").strip()

        api_type = endpoint_data.get("api_type", "")
        base_url = endpoint_data.get("base_url", "")

        if not api_type or not base_url:
            defaults = self._get_provider_defaults(provider)
            if defaults:
                if not api_type:
                    api_type = defaults.get("api_type", "openai")
                if not base_url:
                    base_url = defaults.get("base_url", "")

        if not api_type:
            api_type = "openai"
        if not base_url:
            return f"❌ 无法推断 {provider} 的 API 地址，请手动提供 base_url"

        api_key = endpoint_data.get("api_key", "").strip()

        endpoint_type_map = {"compiler": "compiler_endpoints", "stt": "stt_endpoints"}
        endpoint_type = endpoint_type_map.get(target, "endpoints")

        ep_dict = {
            "name": name,
            "provider": provider,
            "api_type": api_type,
            "base_url": base_url,
            "model": model,
            "priority": int(endpoint_data.get("priority", 10)),
            "max_tokens": int(endpoint_data.get("max_tokens", 0)),
            "context_window": int(endpoint_data.get("context_window", 200000)),
            "timeout": int(endpoint_data.get("timeout", 180)),
        }
        if endpoint_data.get("capabilities"):
            ep_dict["capabilities"] = endpoint_data["capabilities"]
        if endpoint_data.get("api_key_env"):
            ep_dict["api_key_env"] = endpoint_data["api_key_env"]

        from ...llm.endpoint_manager import EndpointManager
        from ...config import settings

        mgr = EndpointManager(Path(settings.project_root))
        try:
            result = mgr.save_endpoint(
                endpoint=ep_dict,
                api_key=api_key or None,
                endpoint_type=endpoint_type,
            )
        except ValueError as e:
            return f"❌ {e}"

        reload_info = self._reload_llm_client()

        api_key_env = result.get("api_key_env", "")
        key_info = f"API Key 已存入 .env ({api_key_env})" if api_key_env else "未配置 API Key"
        return (
            f"✅ 已添加 LLM 端点:\n"
            f"- 名称: {name}\n"
            f"- 服务商: {provider} | 协议: {api_type}\n"
            f"- API 地址: {base_url}\n"
            f"- 模型: {model} | 优先级: {ep_dict['priority']}\n"
            f"- {key_info}\n"
            f"- 目标: {target}\n"
            f"- {reload_info}"
        )

    # ------------------------------------------------------------------
    # remove_endpoint: 删除端点
    # ------------------------------------------------------------------
    def _remove_endpoint(self, params: dict) -> str:
        endpoint_name = (params.get("endpoint_name") or "").strip()
        if not endpoint_name:
            return "❌ 缺少 endpoint_name 参数"

        target = (params.get("target") or "main").strip()

        endpoint_type_map = {"compiler": "compiler_endpoints", "stt": "stt_endpoints"}
        endpoint_type = endpoint_type_map.get(target, "endpoints")

        from ...llm.endpoint_manager import EndpointManager
        from ...config import settings

        mgr = EndpointManager(Path(settings.project_root))
        removed = mgr.delete_endpoint(endpoint_name, endpoint_type=endpoint_type)

        if removed is None:
            all_eps = mgr.list_endpoints(endpoint_type)
            available = ", ".join(e.get("name", "") for e in all_eps) or "(无)"
            return f"❌ 未找到端点 \"{endpoint_name}\"。当前 {target} 端点: {available}"

        reload_info = self._reload_llm_client()
        return f"✅ 已删除端点 \"{endpoint_name}\" ({target})。{reload_info}"

    # ------------------------------------------------------------------
    # test_endpoint: 测试连通性
    # ------------------------------------------------------------------
    async def _test_endpoint(self, params: dict) -> str:
        endpoint_name = (params.get("endpoint_name") or "").strip()
        if not endpoint_name:
            return "❌ 缺少 endpoint_name 参数"

        from ...llm.config import load_endpoints_config

        endpoints, compiler_eps, stt_eps, _ = load_endpoints_config()
        all_eps = endpoints + compiler_eps + stt_eps

        target_ep = None
        for ep in all_eps:
            if ep.name == endpoint_name:
                target_ep = ep
                break

        if not target_ep:
            available = ", ".join(ep.name for ep in all_eps) or "(无)"
            return f"❌ 未找到端点 \"{endpoint_name}\"。可用端点: {available}"

        api_key = target_ep.get_api_key()
        if not api_key:
            return (
                f"❌ 端点 \"{endpoint_name}\" 未配置 API Key。\n"
                f"请设置环境变量 {target_ep.api_key_env or '(未指定)'} 或在端点配置中提供 api_key。"
            )

        import httpx

        # 尝试 list models 请求
        from openakita.llm.types import normalize_base_url

        headers = {"Authorization": f"Bearer {api_key}"}
        _base = normalize_base_url(target_ep.base_url)
        if target_ep.api_type == "anthropic":
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            }
            test_url = _base + "/v1/models"
        else:
            test_url = _base + "/models"

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(test_url, headers=headers)
                elapsed_ms = int((time.time() - t0) * 1000)

                if resp.status_code < 400:
                    return (
                        f"✅ 端点 \"{endpoint_name}\" 连通正常\n"
                        f"- 状态码: {resp.status_code}\n"
                        f"- 延迟: {elapsed_ms}ms\n"
                        f"- 服务商: {target_ep.provider} | 模型: {target_ep.model}"
                    )
                else:
                    body_preview = (resp.text or "")[:300]
                    return (
                        f"⚠️ 端点 \"{endpoint_name}\" 返回错误\n"
                        f"- 状态码: {resp.status_code}\n"
                        f"- 延迟: {elapsed_ms}ms\n"
                        f"- 响应: {body_preview}"
                    )
        except httpx.ConnectError as e:
            return f"❌ 端点 \"{endpoint_name}\" 连接失败: 无法连接到 {target_ep.base_url}\n{e}"
        except httpx.TimeoutException:
            return f"❌ 端点 \"{endpoint_name}\" 请求超时 (15s)"
        except Exception as e:
            return f"❌ 端点 \"{endpoint_name}\" 测试失败: {type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # set_ui: 设置 UI 偏好
    # ------------------------------------------------------------------
    def _set_ui(self, params: dict) -> str:
        from ...config import runtime_state, settings

        theme = (params.get("theme") or "").strip()
        language = (params.get("language") or "").strip()

        if not theme and not language:
            return "❌ 请指定 theme 或 language 参数"

        changes: list[str] = []
        ui_pref: dict[str, str] = {}

        if theme:
            if theme not in ("light", "dark", "system"):
                return f"❌ theme 只支持 light/dark/system，收到: {theme}"
            settings.ui_theme = theme
            ui_pref["theme"] = theme
            changes.append(f"- 主题: {theme}")

        if language:
            if language not in ("zh", "en"):
                return f"❌ language 只支持 zh/en，收到: {language}"
            settings.ui_language = language
            ui_pref["language"] = language
            changes.append(f"- 语言: {language}")

        runtime_state.save()

        result = {
            "ok": True,
            "message": "✅ UI 偏好已更新:\n" + "\n".join(changes),
            "ui_preference": ui_pref,
        }

        # 检查当前通道
        session = getattr(self.agent, "_current_session", None)
        channel = getattr(session, "channel", None) if session else None
        if channel and channel != "desktop":
            result["message"] += "\n\n注意: 此设置仅影响桌面客户端 (Desktop)，当前通道为 " + channel

        return json.dumps(result, ensure_ascii=False)

    # ------------------------------------------------------------------
    # manage_provider: 管理 LLM 服务商
    # ------------------------------------------------------------------

    _PROVIDER_REQUIRED_FIELDS = ("slug", "name", "api_type", "default_base_url")
    _PROVIDER_VALID_API_TYPES = ("openai", "anthropic")
    _PROVIDER_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

    def _manage_provider(self, params: dict) -> str:
        operation = (params.get("operation") or "").strip()

        if operation == "list":
            return self._list_providers_info()
        elif operation == "add":
            return self._add_custom_provider(params.get("provider") or {})
        elif operation == "update":
            return self._update_custom_provider(params.get("provider") or {})
        elif operation == "remove":
            slug = (params.get("slug") or "").strip()
            return self._remove_custom_provider(slug)
        else:
            return (
                "❌ manage_provider 需要 operation 参数。\n"
                "支持: list (列出所有服务商), add (添加自定义服务商), "
                "update (修改自定义服务商), remove (删除自定义服务商)"
            )

    def _list_providers_info(self) -> str:
        from ...llm.registries import list_providers, load_custom_providers

        all_providers = list_providers()
        custom_slugs = {e.get("slug") for e in load_custom_providers()}

        lines = [f"## LLM 服务商列表 (共 {len(all_providers)} 个)\n"]
        for p in all_providers:
            tag = " [自定义]" if p.slug in custom_slugs else ""
            local_tag = " [本地]" if p.is_local else ""
            lines.append(
                f"- **{p.name}**{tag}{local_tag}\n"
                f"  slug: `{p.slug}` | 协议: {p.api_type} | URL: {p.default_base_url}"
            )
        lines.append(
            "\n自定义服务商文件: data/custom_providers.json\n"
            "使用 operation=add 添加新服务商，operation=update 修改已有服务商。"
        )
        return "\n".join(lines)

    def _validate_provider_entry(self, entry: dict) -> str | None:
        """校验服务商条目，返回错误信息或 None"""
        for field in self._PROVIDER_REQUIRED_FIELDS:
            if not (entry.get(field) or "").strip():
                return f"缺少必填字段: {field}"

        slug = entry["slug"].strip()
        if not self._PROVIDER_SLUG_PATTERN.match(slug):
            return f"slug 格式无效: '{slug}'（只允许小写字母、数字、连字符、下划线，不能以符号开头）"

        api_type = entry["api_type"].strip()
        if api_type not in self._PROVIDER_VALID_API_TYPES:
            return f"api_type 无效: '{api_type}'（只允许 openai 或 anthropic）"

        base_url = entry["default_base_url"].strip()
        if not base_url.startswith(("http://", "https://")):
            return "default_base_url 必须以 http:// 或 https:// 开头"

        return None

    def _add_custom_provider(self, provider_data: dict) -> str:
        if not provider_data or not isinstance(provider_data, dict):
            return "❌ 缺少 provider 参数（需包含 slug, name, api_type, default_base_url）"

        err = self._validate_provider_entry(provider_data)
        if err:
            return f"❌ {err}"

        from ...llm.registries import (
            list_providers,
            load_custom_providers,
            reload_registries,
            save_custom_providers,
        )

        slug = provider_data["slug"].strip()

        existing_slugs = {p.slug for p in list_providers()}
        if slug in existing_slugs:
            return (
                f"❌ slug '{slug}' 已存在。如需修改，请使用 operation=update；"
                f"如需覆盖内置服务商的默认配置，也使用 operation=update。"
            )

        entry = {
            "slug": slug,
            "name": provider_data["name"].strip(),
            "api_type": provider_data["api_type"].strip(),
            "default_base_url": provider_data["default_base_url"].strip(),
            "api_key_env_suggestion": (provider_data.get("api_key_env_suggestion") or "").strip(),
            "supports_model_list": provider_data.get("supports_model_list", True),
            "supports_capability_api": provider_data.get("supports_capability_api", False),
            "registry_class": provider_data.get("registry_class") or (
                "AnthropicRegistry" if provider_data["api_type"].strip() == "anthropic" else "OpenAIRegistry"
            ),
            "requires_api_key": provider_data.get("requires_api_key", True),
            "is_local": provider_data.get("is_local", False),
        }
        if provider_data.get("coding_plan_base_url"):
            entry["coding_plan_base_url"] = provider_data["coding_plan_base_url"].strip()
        if provider_data.get("coding_plan_api_type"):
            entry["coding_plan_api_type"] = provider_data["coding_plan_api_type"].strip()

        custom = load_custom_providers()
        custom.append(entry)
        save_custom_providers(custom)
        count = reload_registries()

        return (
            f"✅ 已添加自定义服务商:\n"
            f"- 名称: {entry['name']}\n"
            f"- slug: {slug}\n"
            f"- 协议: {entry['api_type']} | URL: {entry['default_base_url']}\n"
            f"- 服务商总数: {count}\n"
            f"- 保存位置: data/custom_providers.json"
        )

    def _update_custom_provider(self, provider_data: dict) -> str:
        if not provider_data or not isinstance(provider_data, dict):
            return "❌ 缺少 provider 参数"

        slug = (provider_data.get("slug") or "").strip()
        if not slug:
            return "❌ 缺少 slug 字段，用于定位要修改的服务商"

        from ...llm.registries import (
            load_custom_providers,
            reload_registries,
            save_custom_providers,
        )

        if "api_type" in provider_data:
            api_type = provider_data["api_type"].strip()
            if api_type not in self._PROVIDER_VALID_API_TYPES:
                return f"❌ api_type 无效: '{api_type}'"

        if "default_base_url" in provider_data:
            url = provider_data["default_base_url"].strip()
            if not url.startswith(("http://", "https://")):
                return "❌ default_base_url 必须以 http:// 或 https:// 开头"

        custom = load_custom_providers()
        found = False
        for i, entry in enumerate(custom):
            if entry.get("slug") == slug:
                for k, v in provider_data.items():
                    if k == "slug":
                        continue
                    custom[i][k] = v.strip() if isinstance(v, str) else v
                found = True
                break

        if not found:
            new_entry = {"slug": slug}
            for k, v in provider_data.items():
                if k == "slug":
                    continue
                new_entry[k] = v.strip() if isinstance(v, str) else v
            if not new_entry.get("registry_class"):
                api_type = new_entry.get("api_type", "openai")
                new_entry["registry_class"] = (
                    "AnthropicRegistry" if api_type == "anthropic" else "OpenAIRegistry"
                )
            custom.append(new_entry)

        save_custom_providers(custom)
        count = reload_registries()

        action = "修改" if found else "添加（覆盖内置配置）"
        return (
            f"✅ 已{action}服务商 '{slug}':\n"
            f"- 更新字段: {', '.join(k for k in provider_data if k != 'slug')}\n"
            f"- 服务商总数: {count}"
        )

    def _remove_custom_provider(self, slug: str) -> str:
        if not slug:
            return "❌ 缺少 slug 参数"

        from ...llm.registries import (
            _BUILTIN_ENTRIES,
            load_custom_providers,
            reload_registries,
            save_custom_providers,
        )

        builtin_slugs = {e["slug"] for e in _BUILTIN_ENTRIES}
        if slug in builtin_slugs:
            custom = load_custom_providers()
            had_override = any(e.get("slug") == slug for e in custom)
            if had_override:
                custom = [e for e in custom if e.get("slug") != slug]
                save_custom_providers(custom)
                reload_registries()
                return f"✅ 已移除对内置服务商 '{slug}' 的自定义覆盖，恢复为内置默认配置"
            return f"❌ '{slug}' 是内置服务商，不能删除。如需修改其配置，使用 operation=update"

        custom = load_custom_providers()
        original_len = len(custom)
        custom = [e for e in custom if e.get("slug") != slug]

        if len(custom) == original_len:
            return f"❌ 未找到自定义服务商 '{slug}'"

        save_custom_providers(custom)
        count = reload_registries()
        return f"✅ 已删除自定义服务商 '{slug}'。服务商总数: {count}"

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _get_provider_defaults(self, provider_slug: str) -> dict | None:
        """从 provider registry 获取默认配置"""
        try:
            from ...llm.registries import list_providers
            for p in list_providers():
                if p.slug == provider_slug:
                    return {
                        "api_type": p.api_type,
                        "base_url": p.default_base_url,
                        "api_key_env": p.api_key_env_suggestion,
                        "requires_api_key": p.requires_api_key,
                    }
        except Exception as e:
            logger.warning(f"[ConfigHandler] Failed to load provider registry: {e}")
        return None

    def _reload_llm_client(self) -> str:
        """热重载 LLM client，返回结果描述"""
        brain = getattr(self.agent, "brain", None)
        llm_client = getattr(brain, "_llm_client", None) if brain else None
        if llm_client is None:
            return "⚠️ LLM client 未找到，请手动重启服务"

        try:
            success = llm_client.reload()
            if success:
                count = len(llm_client.endpoints)
                return f"已热重载 ({count} 个端点生效)"
            return "⚠️ 热重载返回 false"
        except Exception as e:
            return f"⚠️ 热重载失败: {e}"


def create_handler(agent: "Agent"):
    """创建配置处理器"""
    handler = ConfigHandler(agent)
    return handler.handle
