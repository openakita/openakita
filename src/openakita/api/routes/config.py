"""
Config routes: workspace info, env read/write, endpoints read/write, skills config.

These endpoints mirror the Tauri commands (workspace_read_file, workspace_update_env,
workspace_write_file) but exposed via HTTP so the desktop app can operate in "remote mode"
when connected to an already-running serve instance.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ───────────────────────────────────────────────────────────


def _project_root() -> Path:
    """Return the project root (settings.project_root or cwd)."""
    try:
        from openakita.config import settings

        return Path(settings.project_root)
    except Exception:
        return Path.cwd()


def _enabled_chat_endpoints_count(endpoints: list[Any]) -> int:
    """Treat ``enabled: false`` as excluded; unset ``enabled`` means on."""
    n = 0
    for raw in endpoints:
        if not isinstance(raw, dict):
            continue
        if raw.get("enabled") is False:
            continue
        n += 1
    return n


def _two_chat_endpoints_denial_reason(candidate_list: list[Any], endpoint_type: str) -> str | None:
    """Require ≥2 usable chat endpoints when ``llm_view_min_endpoints_v1`` on."""
    if endpoint_type != "endpoints":
        return None
    try:
        from openakita.core.feature_flags import is_enabled as _ff_enabled
    except Exception:
        return None
    if not _ff_enabled("llm_view_min_endpoints_v1"):
        return None
    if _enabled_chat_endpoints_count(candidate_list) >= 2:
        return None
    return (
        "至少需要 2 个处于启用状态的聊天模型端点（主 + 备用，便于故障切换）。"
        "请再添加或启用一条端点；若在开发环境只需单端点，可在 FEATURE_FLAGS "
        "中将 llm_view_min_endpoints_v1 设为 false。"
    )


def _read_endpoints_safe(ep_path: Path) -> dict | None:
    """Read llm_endpoints.json with .bak fallback."""
    from openakita.utils.atomic_io import read_json_safe

    return read_json_safe(ep_path)


def _endpoints_config_path() -> Path:
    """Return the canonical llm_endpoints.json path.

    Delegates to ``get_default_config_path()`` — the single source of truth
    for config path resolution across LLMClient, EndpointManager, and this API.
    """
    try:
        from openakita.llm.config import get_default_config_path

        return get_default_config_path()
    except Exception:
        return _project_root() / "data" / "llm_endpoints.json"


def _parse_env(content: str) -> dict[str, str]:
    """Parse .env file content into a dict (same logic as Tauri bridge)."""
    # Strip UTF-8 BOM if present (e.g. files saved by Windows Notepad)
    if content.startswith("\ufeff"):
        content = content[1:]
    env: dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes; unescape only \" and \\ (produced by _quote_env_value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            inner = value[1:-1]
            if "\\" in inner:
                # Only unescape sequences produced by our own writer
                inner = inner.replace("\\\\", "\x00").replace('\\"', '"').replace("\x00", "\\")
            value = inner
        else:
            # Unquoted: strip inline comment (# preceded by whitespace)
            for sep in (" #", "\t#"):
                idx = value.find(sep)
                if idx != -1:
                    value = value[:idx].rstrip()
                    break
        env[key] = value
    return env


def _needs_quoting(value: str) -> bool:
    """Check whether a .env value must be quoted to survive round-trip parsing."""
    if not value:
        return False
    if value[0] in (" ", "\t") or value[-1] in (" ", "\t"):
        return True  # leading/trailing whitespace
    if value[0] in ('"', "'"):
        return True  # starts with a quote char
    return any(ch in value for ch in (" ", "#", '"', "'", "\\"))


def _quote_env_value(value: str) -> str:
    """Quote a .env value only when it contains characters that would be
    mangled by typical .env parsers.  Plain values (the vast majority of
    API keys, URLs, flags) are written unquoted for maximum compatibility
    with older OpenAkita versions and third-party .env tooling."""
    if not _needs_quoting(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _update_env_content(
    existing: str,
    entries: dict[str, str],
    delete_keys: set[str] | None = None,
) -> str:
    """Merge entries into existing .env content (preserves comments, order).

    - Non-empty values are written (quoted for round-trip safety).
    - Empty string values are **ignored** (original line preserved).
    - Keys in *delete_keys* are explicitly removed.
    """
    delete_keys = delete_keys or set()
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
        if key in delete_keys:
            updated_keys.add(key)
            continue  # explicit delete — skip line
        if key in entries:
            value = entries[key]
            if value == "":
                # Empty value → preserve the existing line (do NOT delete)
                new_lines.append(line)
            else:
                new_lines.append(f"{key}={_quote_env_value(value)}")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append new keys that weren't in the existing content
    for key, value in entries.items():
        if key not in updated_keys and value != "":
            new_lines.append(f"{key}={_quote_env_value(value)}")

    return "\n".join(new_lines) + "\n"


# ─── Pydantic models ──────────────────────────────────────────────────


class EnvUpdateRequest(BaseModel):
    entries: dict[str, str]
    delete_keys: list[str] = []


class SkillsWriteRequest(BaseModel):
    content: dict  # Full JSON content of skills.json


class DisabledViewsRequest(BaseModel):
    views: list[str]  # e.g. ["skills", "im", "token_stats"]


class AgentModeRequest(BaseModel):
    enabled: bool


class ListModelsRequest(BaseModel):
    api_type: str  # "openai" | "anthropic"
    base_url: str
    provider_slug: str | None = None
    api_key: str


class SecurityConfigUpdate(BaseModel):
    security: dict[str, Any]


class SecurityZonesUpdate(BaseModel):
    workspace: list[str] = []
    controlled: list[str] = []
    protected: list[str] = []
    forbidden: list[str] = []
    default_zone: str = "workspace"


class SecurityPathPolicyUpdate(BaseModel):
    workspace_paths: list[str] = []
    safety_immune_paths: list[str] = []


class SecurityProfileUpdate(BaseModel):
    profile: str
    ack_phrase: str | None = None


class SecurityCommandsUpdate(BaseModel):
    custom_critical: list[str] = []
    custom_high: list[str] = []
    excluded_patterns: list[str] = []
    blocked_commands: list[str] = []


class SecuritySandboxUpdate(BaseModel):
    enabled: bool | None = None
    backend: str | None = None
    sandbox_risk_levels: list[str] | None = None
    exempt_commands: list[str] | None = None


class SecurityConfirmRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    confirm_id: str = Field(validation_alias=AliasChoices("confirm_id", "confirmation_id"))
    decision: str = Field(
        validation_alias=AliasChoices("decision", "choice"),
        description="allow_once | allow_session | allow_always | deny | sandbox (legacy: allow/cancel/confirm_continue)",
    )

    def normalized_decision(self) -> str:
        value = (self.decision or "").strip()
        return {
            "cancel": "deny",
            "confirm_cancel": "deny",
            "confirm_continue": "allow_once",
            "continue": "allow_once",
            "inspect_only": "deny",
        }.get(value, value)


class SecurityConfirmBatchRequest(BaseModel):
    """C18 Phase B：一次性 resolve 一个 session 内时间窗内的所有待 confirm。"""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(validation_alias=AliasChoices("session_id", "conversation_id"))
    decision: str = Field(
        validation_alias=AliasChoices("decision", "choice"),
        description="allow_once | allow_session | allow_always | deny | sandbox",
    )
    within_seconds: float | None = Field(
        default=None,
        ge=0.0,
        le=600.0,
        description=(
            "Restrict batch to confirms whose created_at is within "
            "``within_seconds`` of the most recent pending. None/0 = "
            "no time filter (resolve every pending in the session). "
            "Server clamps to POLICIES.yaml confirmation.aggregation_"
            "window_seconds when that field is set and stricter."
        ),
    )

    def normalized_decision(self) -> str:
        value = (self.decision or "").strip()
        return {
            "cancel": "deny",
            "confirm_cancel": "deny",
            "confirm_continue": "allow_once",
            "continue": "allow_once",
            "inspect_only": "deny",
        }.get(value, value)


def _normalize_permission_mode(mode: str) -> str:
    """Normalize product/user-facing mode names to the existing backend modes."""
    normalized = (mode or "yolo").strip().lower()
    if normalized == "trust":
        normalized = "yolo"
    if normalized not in ("cautious", "smart", "yolo"):
        normalized = "yolo"
    return normalized


def _permission_label(mode: str) -> str:
    return "trust" if _normalize_permission_mode(mode) == "yolo" else mode


_SECURITY_PROFILE_OFF_ACK = "确认风险同意关闭"


def _normalize_security_profile(profile: str) -> str:
    value = (profile or "protect").strip().lower()
    aliases = {
        "yolo": "trust",
        "smart": "protect",
        "cautious": "strict",
    }
    value = aliases.get(value, value)
    if value not in ("trust", "protect", "strict", "off", "custom"):
        value = "protect"
    return value


def _write_profile_event(profile: str, *, previous: str | None = None) -> None:
    """Write a profile switch event using the audit floor.

    This intentionally uses a default AuditLogger, not get_audit_logger(), so a
    disabled normal audit config cannot hide off-mode transitions.
    """
    try:
        from openakita.core.audit_logger import AuditLogger

        AuditLogger(enabled=True).log_event(
            "security_profile_change",
            {"profile": profile, "previous": previous or ""},
        )
    except Exception:
        logger.debug("[Config API] failed to write security profile audit", exc_info=True)


def _apply_security_profile_defaults(sec: dict[str, Any], profile: str) -> None:
    profile = _normalize_security_profile(profile)
    prev = (sec.get("profile") or {}).get("current")
    sec["profile"] = {"current": profile, "base": None if profile != "custom" else prev}
    conf = sec.setdefault("confirmation", {})
    sandbox = sec.setdefault("sandbox", {})
    shell_risk = sec.setdefault("shell_risk", {})
    death_switch = sec.setdefault("death_switch", {})
    checkpoint = sec.setdefault("checkpoint", {})
    if profile == "trust":
        sec["enabled"] = True
        conf["mode"] = "trust"
        sandbox["enabled"] = False
        shell_risk["enabled"] = True
        death_switch["enabled"] = True
        checkpoint["enabled"] = True
    elif profile == "protect":
        sec["enabled"] = True
        conf["mode"] = "default"
        sandbox["enabled"] = True
        shell_risk["enabled"] = True
        death_switch["enabled"] = True
        checkpoint["enabled"] = True
    elif profile == "strict":
        sec["enabled"] = True
        conf["mode"] = "strict"
        sandbox["enabled"] = True
        shell_risk["enabled"] = True
        death_switch["enabled"] = True
        checkpoint["enabled"] = True
    elif profile == "off":
        # off 同时把 security.enabled 关掉，使二者保持单一语义：
        # "整套策略停摆"。engine.preflight 任一为关都会短路 ALLOW，
        # 但只有这里二者同时被写下，未来导出/迁移/审计才不会出现
        # "enabled=True 但 profile=off" 这种荒谬组合。
        sec["enabled"] = False
        conf["mode"] = "trust"
        sandbox["enabled"] = False
        shell_risk["enabled"] = False
        death_switch["enabled"] = False
        checkpoint["enabled"] = False


def _mark_security_profile_custom(sec: dict[str, Any]) -> None:
    """任何细粒度写入都把方案标成 custom；off→custom 会写审计事件。

    设计取舍：UI 在 off 状态下应隐藏细粒度页，但 API 层不能假设调用方
    一定来自 UI。这里采取"细粒度写入 = 用户显式希望开启细粒度策略"，
    把方案从 off 平滑过渡到 custom 并重新启用安全总开关；同时通过审计
    floor 把这次"逃出 off"事件留痕，便于运维事后排查。
    """
    profile = sec.setdefault("profile", {})
    prev = profile.get("current")
    leaving_off = prev == "off"
    if prev != "custom":
        profile["base"] = prev or "protect"
        profile["current"] = "custom"
    # custom 模式下 security.enabled 永远应该是 True（否则不存在意义）。
    sec["enabled"] = True
    if leaving_off:
        _write_profile_event("custom", previous="off")


def _mode_from_security(sec: dict[str, Any] | None) -> str:
    conf = (sec or {}).get("confirmation", {})
    mode = conf.get("mode")
    if mode:
        return _normalize_permission_mode(str(mode))
    if conf.get("auto_confirm") is True:
        return "yolo"
    return "smart" if sec else "yolo"


def _apply_permission_mode_defaults(sec: dict[str, Any], mode: str) -> None:
    """Synchronize high-level permission mode with granular security defaults.

    chat 视图的快速模式切换器只认 cautious/smart/yolo，但其底层就是
    profile 预设。该路径如果碰到当前是 off/custom 的用户，会把状态
    强行带回 trust/protect/strict——这是正向的（重新启用安全），但
    审计上要留痕，便于事后排查"用户突然从 off 跳出了"。
    """
    mode = _normalize_permission_mode(mode)
    target_profile = {
        "yolo": "trust",
        "smart": "protect",
        "cautious": "strict",
    }.get(mode, "strict")
    prev = (sec.get("profile") or {}).get("current")
    _apply_security_profile_defaults(sec, target_profile)
    if prev and prev != target_profile and prev in ("off", "custom"):
        _write_profile_event(target_profile, previous=prev)


# ─── Routes ────────────────────────────────────────────────────────────


@router.get("/api/config/workspace-info")
async def workspace_info():
    """Return current workspace path and basic info.

    路径脱敏：对外只返回相对路径，不暴露用户名和完整目录结构。
    """
    root = _project_root()
    ep_path = _endpoints_config_path()
    return {
        "workspace_path": f"~/{root.name}",
        "workspace_name": root.name,
        "env_exists": (root / ".env").exists(),
        "endpoints_exists": ep_path.exists(),
        "endpoints_path": _sanitize_path(ep_path, root),
    }


def _sanitize_path(full_path: Path, workspace_root: Path) -> str:
    """将绝对路径转换为相对于工作区的路径，避免泄露系统目录结构。"""
    try:
        return str(full_path.relative_to(workspace_root))
    except ValueError:
        return full_path.name


_SENSITIVE_KEY_PATTERNS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

# NOTE: 修改本块（_mask_value / _mask_raw_env / read_env 的 mask 行为）时，
# 务必同步检查以下三处防御，避免再次回归 v1.26.6 已修复的
# "编辑端点时遮蔽 API Key 被回写覆盖真实密钥" 缺陷：
#   1. write_env: 对敏感键过滤含 *** 的值（safe_entries）
#   2. save_endpoint: body.api_key 含 *** 时降级为 None
#   3. apps/setup-center/src/views/LLMView.tsx: editDraft.apiKeyDirty 标记
# 参考 v1.26.x 提交：8ab550fa（前后端 dirty + save_endpoint 防御）
# 与 d3ea9814（write_env *** 防御）。曾被 main 6439b342 误回退。


def _mask_value(key: str, value: str) -> str:
    """Redact values whose key name suggests a secret (API keys, tokens, etc.)."""
    if any(p in key.upper() for p in _SENSITIVE_KEY_PATTERNS):
        if len(value) <= 8:
            return "****"
        return value[:4] + "****" + value[-4:]
    return value


def _mask_raw_env(raw: str) -> str:
    """Mask sensitive values inside the raw .env text."""
    import re

    def _replace(m: re.Match) -> str:
        key, sep, val = m.group("key"), m.group("sep"), m.group("val")
        return f"{key}{sep}{_mask_value(key, val)}"

    return re.sub(
        r"^(?P<key>[A-Za-z_]\w*)(?P<sep>\s*=\s*)(?P<val>.+)$",
        _replace,
        raw,
        flags=re.MULTILINE,
    )


def _runtime_env_key_map() -> dict[str, str]:
    """Map env-style keys to RuntimeState-managed settings fields."""
    from openakita.config import _PERSISTABLE_KEYS

    return {key.upper(): key for key in _PERSISTABLE_KEYS}


def _runtime_env_value(field_name: str) -> str:
    """Return a frontend-friendly string for a RuntimeState-backed setting."""
    from openakita.config import settings

    value = getattr(settings, field_name)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def _runtime_default_value(field_name: str) -> Any:
    """Return the Settings default for a RuntimeState-backed field."""
    from openakita.config import Settings

    field = Settings.model_fields[field_name]
    return field.get_default(call_default_factory=True)


def _coerce_runtime_value(field_name: str, raw_value: str) -> Any:
    """Coerce a frontend env string into the typed Settings field value."""
    from pydantic import TypeAdapter

    from openakita.config import Settings

    field = Settings.model_fields[field_name]
    value: Any = raw_value.strip()
    origin = getattr(field.annotation, "__origin__", None)
    if origin in (list, dict) or str(field.annotation).startswith(("list[", "dict[")):
        try:
            value = json.loads(value) if value else _runtime_default_value(field_name)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be valid JSON") from exc
    return TypeAdapter(field.annotation).validate_python(value)


def _sync_runtime_agent_settings(request: Request, changed_fields: set[str]) -> None:
    """Apply runtime settings that live inside already-created Agent objects."""
    if "persona_name" not in changed_fields:
        return

    try:
        from openakita.config import settings

        agent = getattr(request.app.state, "agent", None)
        actual_agent = getattr(agent, "_local_agent", agent)
        persona_manager = getattr(actual_agent, "persona_manager", None)
        if persona_manager is not None:
            persona_manager.switch_preset(settings.persona_name)
        if hasattr(actual_agent, "_invalidate_system_prompt_cache"):
            actual_agent._invalidate_system_prompt_cache("persona config changed")
        ctx = getattr(actual_agent, "_context", None)
        if (
            ctx is not None
            and getattr(ctx, "system", None)
            and hasattr(actual_agent, "_build_system_prompt")
        ):
            ctx.system = actual_agent._build_system_prompt()
    except Exception as exc:
        logger.warning("[Config API] persona runtime sync failed: %s", exc)


@router.get("/api/config/env")
async def read_env():
    """Read .env file content as key-value pairs.

    Sensitive values (keys containing TOKEN/SECRET/PASSWORD/KEY/CREDENTIAL)
    are masked before being returned. The ``has_value`` map tells the
    frontend which keys actually have a non-empty value without leaking
    the real secret — this lets the editor distinguish "已配置 / 未配置"
    while still requiring the user to explicitly retype to update a key
    (see apiKeyDirty in LLMView.tsx).
    """
    env_path = _project_root() / ".env"
    content = ""
    if env_path.exists():
        content = env_path.read_bytes().decode("utf-8", errors="replace")
    env = _parse_env(content)
    for env_key, field_name in _runtime_env_key_map().items():
        env[env_key] = _runtime_env_value(field_name)
    masked_env = {k: _mask_value(k, v) for k, v in env.items()}
    has_value = {k: bool(v and v.strip()) for k, v in env.items()}
    masked_raw = _mask_raw_env(content)
    return {"env": masked_env, "has_value": has_value, "raw": masked_raw}


@router.post("/api/config/env")
async def write_env(body: EnvUpdateRequest, request: Request):
    """Update .env file with key-value entries (merge, preserving comments).

    - Non-empty values are upserted.
    - Empty string values are ignored (original value preserved).
    - Keys listed in ``delete_keys`` are explicitly removed.
    - Uses atomic write with .bak backup to prevent corruption on crash.
    """
    from openakita.utils.atomic_io import safe_write

    env_path = _project_root() / ".env"
    existing = ""
    if env_path.exists():
        existing = env_path.read_bytes().decode("utf-8", errors="replace")

    # Defense: drop masked sentinel values for sensitive keys to prevent the
    # frontend's saveEnvKeys path from accidentally overwriting real secrets
    # with the *** display values returned by GET /api/config/env.
    # See v1.26.x commit d3ea9814.
    import re as _re

    _sensitive_key_re = _re.compile(
        r"(TOKEN|SECRET|PASSWORD|KEY|APIKEY|CREDENTIAL)", _re.IGNORECASE
    )
    runtime_key_map = _runtime_env_key_map()
    safe_entries: dict[str, str] = {}
    runtime_entries: dict[str, str] = {}
    for key, value in body.entries.items():
        if value and "***" in value and _sensitive_key_re.search(key):
            logger.warning("[Config API] write_env: dropping masked value for %s", key)
            continue
        field_name = runtime_key_map.get(key.upper())
        if field_name:
            runtime_entries[key.upper()] = value
        else:
            safe_entries[key] = value

    runtime_delete_fields: dict[str, str] = {}
    env_delete_keys: set[str] = set()
    for key in body.delete_keys:
        field_name = runtime_key_map.get(key.upper())
        if field_name:
            runtime_delete_fields[key.upper()] = field_name
            env_delete_keys.add(key)
        else:
            env_delete_keys.add(key)

    runtime_changed_fields: set[str] = set()
    if runtime_entries or runtime_delete_fields:
        from openakita.config import runtime_state, settings

        errors: list[str] = []
        runtime_updates: dict[str, Any] = {}
        for env_key, raw_value in runtime_entries.items():
            field_name = runtime_key_map[env_key]
            try:
                new_value = _coerce_runtime_value(field_name, raw_value)
            except (TypeError, ValueError) as exc:
                errors.append(f"{env_key}: {exc}")
                continue
            runtime_updates[field_name] = new_value
            env_delete_keys.add(env_key)

        for env_key, field_name in runtime_delete_fields.items():
            runtime_updates[field_name] = _runtime_default_value(field_name)
            env_delete_keys.add(env_key)

        if errors:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_runtime_config",
                    "messages": errors,
                },
            )

        for field_name, new_value in runtime_updates.items():
            if getattr(settings, field_name) != new_value:
                setattr(settings, field_name, new_value)
                runtime_changed_fields.add(field_name)

        runtime_state.save()

    if safe_entries or (env_delete_keys and env_path.exists()):
        new_content = _update_env_content(existing, safe_entries, delete_keys=env_delete_keys)
        safe_write(env_path, new_content)
    for key, value in safe_entries.items():
        if value:
            os.environ[key] = value
    for key in env_delete_keys:
        os.environ.pop(key, None)
    count = (
        len([v for v in safe_entries.values() if v]) + len(runtime_entries) + len(env_delete_keys)
    )
    logger.info(f"[Config API] Updated .env with {count} entries")

    # Push changes into the in-process Settings singleton so consumers that
    # read ``getattr(settings, ...)`` on each task (e.g. LoopBudgetGuard,
    # ContextManager.calculate_context_pressure, ReasoningEngine ratio
    # injection) see the new values without a process restart.
    try:
        from openakita.config import settings as _settings

        _settings_changed = _settings.reload()
        if _settings_changed:
            logger.info("[Config API] Settings hot-reloaded fields: %s", _settings_changed)
    except Exception as exc:
        logger.warning("[Config API] Settings.reload() failed: %s", exc)

    # Determine if any changed keys require a service restart
    _RESTART_REQUIRED_PREFIXES = (
        "TELEGRAM_",
        "FEISHU_",
        "DINGTALK_",
        "WEWORK_",
        "ONEBOT_",
        "QQ_",
        "WECHAT_",
        "IM_",
        "REDIS_",
        "DATABASE_",
        "SANDBOX_",
    )
    _HOT_RELOAD_PREFIXES = (
        "OPENAI_",
        "ANTHROPIC_",
        "LLM_",
        "DEFAULT_MODEL",
        "TEMPERATURE",
        "MAX_TOKENS",
        "OPENAKITA_THEME",
        "LANGUAGE",
        # Context / long-task / task-budget knobs — read fresh on each task
        # so they hot-reload as soon as Settings.reload() above runs.
        "CONTEXT_",
        "TASK_BUDGET_",
        "API_TOOLS_",
        "SAME_TOOL_",
        "READONLY_STAGNATION_",
        "MAX_ITERATIONS",
        "THINKING_MODE",
        "PROGRESS_TIMEOUT_",
        "HARD_TIMEOUT_",
        "TOOL_MAX_PARALLEL",
        "FORCE_TOOL_CALL_",
        "CONFIRMATION_TEXT_",
        "ALLOW_PARALLEL_TOOLS",
        "MEMORY_",
        "PERSONA_",
        "AGENT_NAME",
        "PROACTIVE_",
        "STICKER_",
        "SCHEDULER_",
        "SELFCHECK_",
        "DESKTOP_NOTIFY_",
        "SESSION_TIMEOUT_",
        "SESSION_MAX_HISTORY",
        "BACKUP_",
    )
    if runtime_changed_fields:
        _sync_runtime_agent_settings(request, runtime_changed_fields)
        _notify_runtime_config_changed(
            request,
            "runtime_config:" + ",".join(sorted(runtime_changed_fields)),
        )

    changed_keys = (
        {k for k, v in safe_entries.items() if v}
        | set(runtime_entries.keys())
        | set(body.delete_keys)
    )
    restart_required = any(
        any(k.upper().startswith(p) for p in _RESTART_REQUIRED_PREFIXES) for k in changed_keys
    )
    hot_reloadable = (
        all(
            any(k.upper().startswith(p) for p in _HOT_RELOAD_PREFIXES)
            or k.upper().startswith("OPENAKITA_")
            for k in changed_keys
        )
        if changed_keys
        else True
    )

    return {
        "status": "ok",
        "updated_keys": list(safe_entries.keys()) + list(runtime_entries.keys()),
        "restart_required": restart_required,
        "hot_reloadable": hot_reloadable,
    }


@router.get("/api/config/endpoints")
async def read_endpoints():
    """Read data/llm_endpoints.json, falling back to .bak if primary is corrupt."""
    ep_path = _endpoints_config_path()
    data = _read_endpoints_safe(ep_path)
    if data is None:
        return {"endpoints": [], "raw": {}}
    return {"endpoints": data.get("endpoints", []), "raw": data}


def _get_endpoint_manager():
    """Get or create the EndpointManager singleton for the current workspace."""
    from openakita.llm.endpoint_manager import EndpointManager

    root = _project_root()
    _mgr = getattr(_get_endpoint_manager, "_instance", None)
    if _mgr is None or _mgr._ws_dir != root:
        _mgr = EndpointManager(root, config_path=_endpoints_config_path())
        _get_endpoint_manager._instance = _mgr
    return _mgr


class SaveEndpointRequest(BaseModel):
    endpoint: dict
    api_key: str | None = None
    endpoint_type: str = "endpoints"
    expected_version: str | None = None
    original_name: str | None = None


class SaveEndpointsRequest(BaseModel):
    endpoints: list[dict]
    api_key: str | None = None
    endpoint_type: str = "endpoints"
    expected_version: str | None = None


class DeleteEndpointRequest(BaseModel):
    endpoint_type: str = "endpoints"
    clean_env: bool = True


@router.post("/api/config/save-endpoint")
async def save_endpoint(body: SaveEndpointRequest, request: Request):
    """Save or update an LLM endpoint atomically.

    Writes the API key to .env and the endpoint config to llm_endpoints.json
    in a single coordinated operation. Then triggers hot-reload.
    """
    from openakita.llm.endpoint_manager import ConflictError

    # Defense: if the frontend echoed back the masked sentinel from
    # GET /api/config/env (e.g. "sk-d****ab53"), treat it as "unchanged"
    # rather than overwriting the real key on disk. The frontend should
    # already gate this via apiKeyDirty (LLMView.tsx) but we keep this
    # belt-and-suspenders to prevent regressions like main 6439b342.
    # See v1.26.x commit 8ab550fa.
    api_key = body.api_key
    if api_key and "***" in api_key:
        logger.warning(
            "[Config API] save-endpoint: ignoring masked API key (len=%d), treating as unchanged",
            len(api_key),
        )
        api_key = None

    mgr = _get_endpoint_manager()
    existing_endpoint = None
    lookup_name = (body.original_name or body.endpoint.get("name") or "").strip()
    if lookup_name:
        try:
            existing_endpoint = next(
                (
                    ep
                    for ep in mgr.list_endpoints(body.endpoint_type)
                    if str(ep.get("name") or "").strip() == lookup_name
                ),
                None,
            )
        except Exception:
            existing_endpoint = None

    env_cache: dict[str, str] = {}
    env_path = _project_root() / ".env"
    if env_path.exists():
        env_cache = _parse_env(env_path.read_bytes().decode("utf-8", errors="replace"))

    def _lookup_key(name: str) -> str | None:
        return os.environ.get(name) or env_cache.get(name)

    try:
        from openakita.llm.endpoint_validation import (
            validate_endpoint_api_key,
            validate_endpoint_model_usage,
        )

        validation_error = validate_endpoint_model_usage(
            body.endpoint,
            endpoint_type=body.endpoint_type,
        ) or validate_endpoint_api_key(
            body.endpoint,
            api_key=api_key,
            existing_endpoint=existing_endpoint,
            env_lookup=_lookup_key,
        )
    except Exception as e:
        logger.debug("[Config API] endpoint API key validation skipped: %s", e)
        validation_error = None
    if validation_error:
        return {"status": "error", "error": validation_error}

    try:
        result = mgr.save_endpoint(
            endpoint=body.endpoint,
            api_key=api_key,
            endpoint_type=body.endpoint_type,
            expected_version=body.expected_version,
            original_name=body.original_name,
        )
    except ConflictError as e:
        return {"status": "conflict", "error": str(e), "current_version": e.current_version}
    except (ValueError, Exception) as e:
        logger.error("[Config API] save-endpoint failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}

    # Auto-reload running clients. Saving is authoritative; reload is a
    # runtime follow-up and should be reported separately instead of turning a
    # successful write into a generic "model config failed" error.
    reload_result = _trigger_reload(request)

    response = {
        "status": "ok",
        "saved": True,
        "endpoint": result,
        "version": mgr.get_version(),
        "reload": reload_result,
    }
    if reload_result.get("status") == "failed":
        response["warning"] = (
            "配置已保存，但当前运行中的服务暂未加载新配置。"
            "可以继续配置；如果马上要使用新模型，请重启服务或稍后再试。"
        )
    return response


@router.post("/api/config/save-endpoints")
async def save_endpoints(body: SaveEndpointsRequest, request: Request):
    """Save multiple LLM endpoints in one import operation."""
    from openakita.llm.endpoint_manager import ConflictError

    api_key = body.api_key
    if api_key and "***" in api_key:
        logger.warning(
            "[Config API] save-endpoints: ignoring masked API key (len=%d)",
            len(api_key),
        )
        api_key = None

    deny_two = _two_chat_endpoints_denial_reason(body.endpoints, body.endpoint_type)
    if deny_two:
        return {"status": "error", "error": deny_two}

    mgr = _get_endpoint_manager()
    existing_by_name: dict[str, dict] = {}
    try:
        existing_by_name = {
            str(ep.get("name") or "").strip(): ep for ep in mgr.list_endpoints(body.endpoint_type)
        }
    except Exception:
        existing_by_name = {}

    env_cache: dict[str, str] = {}
    env_path = _project_root() / ".env"
    if env_path.exists():
        env_cache = _parse_env(env_path.read_bytes().decode("utf-8", errors="replace"))

    def _lookup_key(name: str) -> str | None:
        return os.environ.get(name) or env_cache.get(name)

    try:
        from openakita.llm.endpoint_validation import (
            validate_endpoint_api_key,
            validate_endpoint_model_usage,
        )

        for endpoint in body.endpoints:
            endpoint_name = str(endpoint.get("name") or "").strip()
            validation_error = validate_endpoint_model_usage(
                endpoint,
                endpoint_type=body.endpoint_type,
            ) or validate_endpoint_api_key(
                endpoint,
                api_key=api_key,
                existing_endpoint=existing_by_name.get(endpoint_name),
                env_lookup=_lookup_key,
            )
            if validation_error:
                return {"status": "error", "error": validation_error, "endpoint": endpoint_name}
    except Exception as e:
        logger.debug("[Config API] endpoint batch API key validation skipped: %s", e)

    try:
        result = mgr.save_endpoints(
            endpoints=body.endpoints,
            api_key=api_key,
            endpoint_type=body.endpoint_type,
            expected_version=body.expected_version,
        )
    except ConflictError as e:
        return {"status": "conflict", "error": str(e), "current_version": e.current_version}
    except (ValueError, Exception) as e:
        logger.error("[Config API] save-endpoints failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}

    reload_result = _trigger_reload(request)
    response = {
        "status": "ok",
        "saved": True,
        "count": len(result),
        "endpoints": result,
        "version": mgr.get_version(),
        "reload": reload_result,
    }
    if reload_result.get("status") == "failed":
        response["warning"] = (
            "配置已保存，但当前运行中的服务暂未加载新配置。"
            "可以继续配置；如果马上要使用新模型，请重启服务或稍后再试。"
        )
    return response


@router.delete("/api/config/endpoint/{name:path}")
async def delete_endpoint_by_name(
    name: str, request: Request, endpoint_type: str = "endpoints", clean_env: bool = True
):
    """Delete an LLM endpoint by name. Cleans up the .env key if no longer used."""
    mgr = _get_endpoint_manager()
    removed = mgr.delete_endpoint(name, endpoint_type=endpoint_type, clean_env=clean_env)
    if removed is None:
        return {"status": "not_found", "name": name}

    reload_result = _trigger_reload(request)
    return {
        "status": "ok",
        "removed": removed,
        "version": mgr.get_version(),
        "reload": reload_result,
    }


@router.get("/api/config/endpoint-status")
async def endpoint_status():
    """Return key presence status for all configured endpoints."""
    mgr = _get_endpoint_manager()
    return {"endpoints": mgr.get_endpoint_status()}


class ToggleEndpointRequest(BaseModel):
    name: str
    endpoint_type: str = "endpoints"


class ReorderEndpointsRequest(BaseModel):
    ordered_names: list[str]
    endpoint_type: str = "endpoints"


class UpdateSettingsRequest(BaseModel):
    settings: dict


@router.post("/api/config/toggle-endpoint")
async def toggle_endpoint(body: ToggleEndpointRequest, request: Request):
    """Toggle an endpoint's enabled/disabled state via EndpointManager."""
    mgr = _get_endpoint_manager()
    try:
        updated = mgr.toggle_endpoint(body.name, endpoint_type=body.endpoint_type)
    except (ValueError, Exception) as e:
        logger.error("[Config API] toggle-endpoint failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
    deny_tgl = None
    if body.endpoint_type == "endpoints" and mgr.list_endpoints(body.endpoint_type):
        deny_tgl = _two_chat_endpoints_denial_reason(
            mgr.list_endpoints(body.endpoint_type),
            body.endpoint_type,
        )
    if deny_tgl:
        try:
            mgr.toggle_endpoint(body.name, endpoint_type=body.endpoint_type)
        except Exception:
            pass
        return {"status": "error", "error": deny_tgl}

    reload_result = _trigger_reload(request)
    return {
        "status": "ok",
        "endpoint": updated,
        "version": mgr.get_version(),
        "reload": reload_result,
    }


@router.post("/api/config/reorder-endpoints")
async def reorder_endpoints(body: ReorderEndpointsRequest, request: Request):
    """Reorder endpoints by name list via EndpointManager."""
    mgr = _get_endpoint_manager()
    try:
        result = mgr.reorder_endpoints(
            body.ordered_names,
            endpoint_type=body.endpoint_type,
        )
    except (ValueError, Exception) as e:
        logger.error("[Config API] reorder-endpoints failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
    reload_result = _trigger_reload(request)
    return {
        "status": "ok",
        "endpoints": result,
        "version": mgr.get_version(),
        "reload": reload_result,
    }


@router.post("/api/config/update-settings")
async def update_endpoint_settings(body: UpdateSettingsRequest, request: Request):
    """Merge settings into llm_endpoints.json via EndpointManager."""
    mgr = _get_endpoint_manager()
    try:
        updated = mgr.update_settings(body.settings)
    except Exception as e:
        logger.error("[Config API] update-settings failed: %s", e, exc_info=True)
        return {"status": "error", "error": str(e)}
    reload_result = _trigger_reload(request)
    return {
        "status": "ok",
        "settings": updated,
        "version": mgr.get_version(),
        "reload": reload_result,
    }


def _trigger_reload(request: Request) -> dict[str, Any]:
    """Apply persisted LLM config to live runtime components."""
    from openakita.llm.runtime_config import apply_llm_runtime_config

    return apply_llm_runtime_config(
        agent=getattr(request.app.state, "agent", None),
        gateway=getattr(request.app.state, "gateway", None),
        pool=getattr(request.app.state, "agent_pool", None),
        config_path=_endpoints_config_path(),
        reason="llm_config",
    )


def _notify_runtime_config_changed(request: Request, reason: str) -> None:
    """Invalidate pooled desktop agents after runtime-affecting config changes."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is None:
        return
    try:
        if hasattr(pool, "notify_runtime_config_changed"):
            pool.notify_runtime_config_changed(reason)
        elif hasattr(pool, "notify_skills_changed"):
            pool.notify_skills_changed()
    except Exception as e:
        logger.warning("[Config API] pool runtime invalidation failed: %s", e)


@router.post("/api/config/reload")
async def reload_config(request: Request):
    """Hot-reload LLM endpoints config from disk into the running agent.

    This should be called after writing llm_endpoints.json so the running
    service picks up changes without a full restart.
    """
    return _trigger_reload(request)


@router.post("/api/config/restart")
async def restart_service(request: Request):
    """触发服务优雅重启。

    流程：设置重启标志 → 触发 shutdown_event → serve() 主循环检测标志后重新初始化。
    前端应在调用后轮询 /api/health 直到服务恢复。
    """
    from openakita import config as cfg

    cfg._restart_requested = True
    shutdown_event = getattr(request.app.state, "shutdown_event", None)
    if shutdown_event is not None:
        logger.info("[Config API] Restart requested, triggering graceful shutdown for restart")
        shutdown_event.set()
        return {"status": "restarting"}
    else:
        logger.warning("[Config API] Restart requested but no shutdown_event available")
        cfg._restart_requested = False
        return {"status": "error", "message": "restart not available in this mode"}


@router.get("/api/config/skills")
async def read_skills_config():
    """Read data/skills.json (external skill selection only)."""
    sk_path = _project_root() / "data" / "skills.json"
    if not sk_path.exists():
        return {"kind": "skill_external_allowlist", "skills": {}}
    try:
        data = json.loads(sk_path.read_text(encoding="utf-8"))
        return {"kind": "skill_external_allowlist", "skills": data}
    except Exception as e:
        return {"kind": "skill_external_allowlist", "error": str(e), "skills": {}}


@router.get("/api/config/skills/external-allowlist")
async def read_skill_external_allowlist():
    """Read the external skill enablement allowlist.

    This is intentionally separate from the security user_allowlist in
    identity/POLICIES.yaml and from IM channel group allowlists.
    """
    from openakita.core.security_actions import list_skill_external_allowlist

    return list_skill_external_allowlist()


@router.post("/api/config/skills")
async def write_skills_config(body: SkillsWriteRequest, request: Request):
    """Write data/skills.json.

    写入后统一走 ``Agent.propagate_skill_change``：
      - Parser/Loader 缓存失效，外部技能 allowlist 按新文件重算
      - SkillCatalog / ``_skill_catalog_text`` 重建，CLI 系统提示重刷
      - Handler 映射同步 + AgentInstancePool 版本号自增（下一条桌面端请求拿到新 Agent）
      - HTTP ``_skills_cache`` 通过事件回调失效，WebSocket 广播 SkillEvent.ENABLE

    若 request 中尚无 agent（启动前调用），则仅写盘，刷新将在 Agent 初始化时自然发生。
    """
    from openakita.core.security_actions import maybe_refresh_skills, set_skill_external_allowlist

    content = body.content if isinstance(body.content, dict) else {}
    al = content.get("external_allowlist") if isinstance(content, dict) else None

    # 优先走唯一写入点；若 payload 不是标准 allowlist 结构，回退到旧的整文件覆盖，
    # 以保持前端「原样写入」的兼容（例如把非 allowlist 字段写进去）。
    if isinstance(al, list):
        set_skill_external_allowlist([str(x).strip() for x in al if str(x).strip()])
    else:
        sk_path = _project_root() / "data" / "skills.json"
        sk_path.parent.mkdir(parents=True, exist_ok=True)
        sk_path.write_text(
            json.dumps(content, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # 触发统一刷新（rescan=False：仅重算 allowlist+catalog+pool，无需再扫盘）
    try:
        await maybe_refresh_skills(
            {"status": "ok", "kind": "skill_external_allowlist"},
            lambda: getattr(request.app.state, "agent", None),
        )
    except Exception as e:
        logger.warning("[Config API] post-write skill propagate failed: %s", e)

    logger.info("[Config API] Updated skills.json")
    return {"status": "ok", "kind": "skill_external_allowlist"}


@router.post("/api/config/skills/external-allowlist")
async def write_skill_external_allowlist(body: dict, request: Request):
    """Replace the external skill allowlist via the explicit skill-only endpoint."""
    content = {
        "external_allowlist": body.get("external_allowlist", body.get("skill_ids", [])),
    }
    return await write_skills_config(SkillsWriteRequest(content=content), request)


@router.get("/api/config/disabled-views")
async def read_disabled_views():
    """Read the list of disabled module views."""
    dv_path = _project_root() / "data" / "disabled_views.json"
    if not dv_path.exists():
        return {"disabled_views": []}
    try:
        data = json.loads(dv_path.read_text(encoding="utf-8"))
        return {"disabled_views": data.get("disabled_views", [])}
    except Exception as e:
        return {"error": str(e), "disabled_views": []}


@router.post("/api/config/disabled-views")
async def write_disabled_views(body: DisabledViewsRequest):
    """Update the list of disabled module views."""
    dv_path = _project_root() / "data" / "disabled_views.json"
    dv_path.parent.mkdir(parents=True, exist_ok=True)
    dv_path.write_text(
        json.dumps({"disabled_views": body.views}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info(f"[Config API] Updated disabled_views: {body.views}")
    return {"status": "ok", "disabled_views": body.views}


@router.get("/api/config/agent-mode")
async def read_agent_mode():
    """返回多Agent模式状态（已默认常开）"""
    return {"multi_agent_enabled": True}


def _hot_patch_agent_tools(request: Request, *, enable: bool) -> None:
    """Dynamically register / unregister multi-agent tools on the live global Agent."""
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        return
    try:
        from openakita.tools.definitions.agent import AGENT_TOOLS
        from openakita.tools.definitions.org_setup import ORG_SETUP_TOOLS
        from openakita.tools.handlers.agent import create_handler as create_agent_handler
        from openakita.tools.handlers.org_setup import create_handler as create_org_setup_handler

        all_tools = AGENT_TOOLS + ORG_SETUP_TOOLS
        tool_names = [t["name"] for t in all_tools]

        if enable:
            existing = {t["name"] for t in agent._tools}
            for t in all_tools:
                if t["name"] not in existing:
                    agent._tools.append(t)
                agent.tool_catalog.add_tool(t)
            agent.handler_registry.register("agent", create_agent_handler(agent))
            agent.handler_registry.register("org_setup", create_org_setup_handler(agent))
            logger.info("[Config API] Agent + org_setup tools hot-patched onto global agent")
        else:
            agent._tools = [t for t in agent._tools if t["name"] not in set(tool_names)]
            for name in tool_names:
                agent.tool_catalog.remove_tool(name)
            agent.handler_registry.unregister("agent")
            try:
                agent.handler_registry.unregister("org_setup")
            except Exception:
                pass
            logger.info("[Config API] Agent + org_setup tools removed from global agent")
    except Exception as e:
        logger.warning(f"[Config API] Failed to hot-patch agent tools: {e}")


@router.post("/api/config/agent-mode")
async def write_agent_mode(body: AgentModeRequest, request: Request):
    """多Agent模式已默认常开，此端点保留以兼容旧客户端。"""
    from openakita.config import settings

    old = settings.multi_agent_enabled
    settings.multi_agent_enabled = True
    logger.info(f"[Config API] multi_agent_enabled forced True (was {old})")

    if not old:
        try:
            from openakita.main import _init_orchestrator

            await _init_orchestrator()
            from openakita.main import _orchestrator

            if _orchestrator is not None:
                request.app.state.orchestrator = _orchestrator
                logger.info("[Config API] Orchestrator initialized and bound to app.state")
        except Exception as e:
            logger.warning(f"[Config API] Failed to init orchestrator on mode switch: {e}")
        try:
            from openakita.agents.presets import ensure_presets_on_mode_enable

            ensure_presets_on_mode_enable(settings.data_dir / "agents")
        except Exception as e:
            logger.warning(f"[Config API] Failed to deploy presets: {e}")

        _hot_patch_agent_tools(request, enable=True)

    # 通知 pool 刷新版本号，旧会话的 Agent 下次请求时自动重建
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None:
        pool.notify_skills_changed()

    return {"status": "ok", "multi_agent_enabled": True}


# ---------------------------------------------------------------------------
# Tool Loading Configuration
# ---------------------------------------------------------------------------


class ToolLoadingRequest(BaseModel):
    always_load_tools: list[str] = []
    always_load_categories: list[str] = []


@router.get("/api/config/tool-loading")
async def read_tool_loading(request: Request):
    """读取工具常驻加载配置。"""
    from openakita.config import settings

    available_categories: list[str] = []
    agent = getattr(request.app.state, "agent", None)
    if agent and hasattr(agent, "tool_catalog"):
        try:
            available_categories = sorted(agent.tool_catalog.get_tool_groups().keys())
        except Exception:
            pass

    return {
        "always_load_tools": settings.always_load_tools,
        "always_load_categories": settings.always_load_categories,
        "available_categories": available_categories,
    }


@router.post("/api/config/tool-loading")
async def write_tool_loading(body: ToolLoadingRequest, request: Request):
    """更新工具常驻加载配置。立即生效并持久化。"""
    from openakita.config import runtime_state, settings

    settings.always_load_tools = body.always_load_tools
    settings.always_load_categories = body.always_load_categories
    runtime_state.save()
    logger.info(
        "[Config API] tool-loading updated: tools=%s, categories=%s",
        body.always_load_tools,
        body.always_load_categories,
    )
    return {
        "status": "ok",
        "always_load_tools": body.always_load_tools,
        "always_load_categories": body.always_load_categories,
    }


@router.get("/api/config/providers")
async def list_providers_api():
    """返回后端已注册的 LLM 服务商列表。

    前端可在后端运行时通过此 API 获取最新的 provider 列表，
    确保前后端数据一致。
    """
    try:
        from openakita.llm.registries import list_providers

        providers = list_providers()
        return {
            "providers": [
                {
                    "name": p.name,
                    "slug": p.slug,
                    "api_type": p.api_type,
                    "default_base_url": p.default_base_url,
                    "api_key_env_suggestion": getattr(p, "api_key_env_suggestion", ""),
                    "supports_model_list": getattr(p, "supports_model_list", True),
                    "supports_capability_api": getattr(p, "supports_capability_api", False),
                    "requires_api_key": getattr(p, "requires_api_key", True),
                    "is_local": getattr(p, "is_local", False),
                    "coding_plan_base_url": getattr(p, "coding_plan_base_url", None),
                    "coding_plan_api_type": getattr(p, "coding_plan_api_type", None),
                    "note": getattr(p, "note", None),
                }
                for p in providers
            ]
        }
    except Exception as e:
        logger.error(f"[Config API] list-providers failed: {e}")
        return {"providers": [], "error": str(e)}


@router.post("/api/config/list-models")
async def list_models_api(body: ListModelsRequest):
    """拉取 LLM 端点的模型列表（远程模式替代 Tauri openakita_list_models 命令）。

    直接复用 bridge.list_models 的逻辑，在后端进程内异步调用，无需 subprocess。
    """
    try:
        from openakita.setup_center.bridge import (
            _list_models_anthropic,
            _list_models_openai,
        )

        api_type = (body.api_type or "").strip().lower()
        base_url = (body.base_url or "").strip()
        api_key = (body.api_key or "").strip()
        provider_slug = (body.provider_slug or "").strip() or None

        if not api_type:
            return {"error": "api_type 不能为空", "models": []}
        if not base_url:
            return {"error": "base_url 不能为空", "models": []}
        # 本地服务商（Ollama/LM Studio 等）不需要 API Key，允许空值
        if not api_key:
            api_key = "local"  # placeholder for local providers

        if api_type in ("openai", "openai_responses"):
            models = await _list_models_openai(api_key, base_url, provider_slug)
        elif api_type == "anthropic":
            models = await _list_models_anthropic(api_key, base_url, provider_slug)
        else:
            return {"error": f"不支持的 api_type: {api_type}", "models": []}

        return {"models": models}
    except Exception as e:
        logger.error(f"[Config API] list-models failed: {e}", exc_info=True)
        # 将原始 Python 异常转为用户友好的提示
        raw = str(e).lower()
        friendly = str(e)
        if "errno 2" in raw or "no such file" in raw:
            friendly = "SSL 证书文件缺失，请重新安装或更新应用"
        elif (
            "connect" in raw
            or "connection refused" in raw
            or "no route" in raw
            or "unreachable" in raw
        ):
            friendly = "无法连接到服务商，请检查 API 地址和网络连接"
            try:
                from openakita.llm.providers.proxy_utils import format_proxy_hint

                hint = format_proxy_hint()
                if hint:
                    friendly += hint
            except Exception:
                pass
        elif (
            "401" in raw
            or "unauthorized" in raw
            or "invalid api key" in raw
            or "authentication" in raw
        ):
            friendly = "API Key 无效或已过期，请检查后重试"
        elif "403" in raw or "forbidden" in raw or "permission" in raw:
            friendly = "API Key 权限不足，请确认已开通模型访问权限"
        elif "404" in raw or "not found" in raw:
            friendly = "该服务商不支持模型列表查询，您可以手动输入模型名称"
        elif "timeout" in raw or "timed out" in raw:
            friendly = "请求超时，请检查网络或稍后重试"
        elif len(friendly) > 150:
            friendly = friendly[:150] + "…"
        return {"error": friendly, "models": []}


# ─── Security Policy Routes ───────────────────────────────────────────


def _read_policies_yaml() -> dict | None:
    """Read identity/POLICIES.yaml as dict.

    Returns None on parse error to distinguish from empty file ({}).
    Callers must check for None before writing to prevent data loss (P1-9).
    """
    import yaml

    policies_path = _project_root() / "identity" / "POLICIES.yaml"
    if not policies_path.exists():
        return {}
    try:
        return yaml.safe_load(policies_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.error(f"[Config] 无法读取 POLICIES.yaml: {e}")
        return None


def _write_policies_yaml(data: dict) -> bool:
    """Write dict to identity/POLICIES.yaml.

    Returns False if the write was refused (P1-9: 防止配置文件覆盖丢失).
    """
    import yaml

    existing = _read_policies_yaml()
    if existing is None:
        logger.error("[Config] 拒绝写入 POLICIES.yaml: 当前文件无法正确读取，写入可能导致数据丢失")
        return False
    policies_path = _project_root() / "identity" / "POLICIES.yaml"
    policies_path.parent.mkdir(parents=True, exist_ok=True)
    policies_path.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return True


@router.get("/api/config/security")
async def read_security_config():
    """Read the full security policy configuration."""
    data = _read_policies_yaml()
    if data is None:
        return {"security": {}, "_warning": "配置文件读取失败"}
    return {"security": data.get("security", {})}


def _deep_merge_security(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    """Recursive in-place merge of ``source`` into ``target``.

    Semantics (C21 P0-2, derived from plan §7.2):

    - dict + dict → recurse
    - everything else (list / primitive / None) → source wins (replace)
    - keys present in ``target`` but absent in ``source`` are **preserved**

    Lists are replaced wholesale (not element-wise merged). This matches
    the typical UI flow: when the user edits ``user_allowlist.commands``
    they POST the new full list, not a diff.

    Deletion is NOT supported through deep-merge. To clear a setting, the
    caller must POST an explicit empty value (``[]`` / ``{}`` / default
    primitive) or use the ``?replace=true`` escape hatch on the endpoint.

    Why merge instead of full-replace?
    ----------------------------------

    Pre-C21 ``write_security_config`` did ``data["security"] = body.security``.
    The UI POSTs only the fields it renders; everything it doesn't render
    (e.g. ``user_allowlist`` custom commands, ``hot_reload``, ``rotation``,
    ``aggregation_window_seconds``, ``audit.log_path``) silently disappeared
    from YAML and got filled back in by loader defaults — **user-customized
    values were lost**. The plan (§7.2) explicitly required deep-merge; this
    function is the long-promised implementation.
    """
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_merge_security(target[k], v)
        else:
            target[k] = v
    return target


@router.post("/api/config/security")
async def write_security_config(body: SecurityConfigUpdate, replace: bool = False):
    """Update the security policy configuration.

    Default behaviour (C21 P0-2): **deep-merge** the request body into the
    existing ``security`` block, preserving any field the caller did not
    mention. Set ``?replace=true`` to opt into the legacy full-replace
    semantics — operators who genuinely want to wipe and rewrite the entire
    block (e.g. a reset-to-defaults flow) can do so explicitly.

    See ``_deep_merge_security`` for merge semantics, especially how lists
    are handled (wholesale replace) and how deletion works (POST empty
    container or use ``?replace=true``).
    """
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取当前配置文件，写入已取消以防止数据丢失"}
    if replace:
        data["security"] = body.security
        merge_mode = "replace"
    else:
        existing_security = data.get("security")
        if not isinstance(existing_security, dict):
            existing_security = {}
        _deep_merge_security(existing_security, body.security)
        data["security"] = existing_security
        merge_mode = "merge"
    if not _write_policies_yaml(data):
        return {"status": "error", "message": "配置写入失败"}
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="security")
    except Exception:
        pass
    logger.info("[Config API] Updated security policy (mode=%s)", merge_mode)
    return {"status": "ok", "mode": merge_mode}


# C9a §4: Dry-run preview — let user inspect how their saved policy_v2 config
# behaves against representative tool calls **without** executing anything.
# Body=None/{} → use currently persisted config (fast path; no plumbing).
# Body={"security": {...}} → build PolicyConfigV2 from a proposed security
# block (in-memory; never written to disk; ad-hoc engine instance).
# Returns: {"decisions": [{tool, params_preview, decision, reason,
#                          approval_class, risk_level, safety_immune_match}]}
_DRY_RUN_SAMPLES: list[tuple[str, dict[str, object]]] = [
    ("read_file", {"path": "README.md"}),
    ("write_file", {"path": "data/scratch/note.txt", "content": "x"}),
    ("write_file", {"path": "/etc/passwd", "content": "x"}),
    ("write_file", {"path": "identity/SOUL.md", "content": "x"}),
    ("delete_file", {"path": "data/checkpoints/old.json"}),
    ("run_shell", {"command": "ls"}),
    ("run_shell", {"command": "rm -rf /"}),
    ("delegate_to_agent", {"agent": "researcher"}),
    ("switch_mode", {"target_mode": "plan"}),
]


@router.post("/api/config/security/preview")
async def preview_security_config(body: dict | None = None):
    """Run sample tool decisions against a proposed (or current) policy config.

    C8b-1: ``make_preview_engine()`` ensures dry-run never pollutes the global
    death_switch tracker (DENY samples like ``rm -rf /`` no longer drive a
    real user into readonly mode after 3 saves). Both the proposed-config and
    current-config branches now produce a fresh ad-hoc engine; the global
    singleton is never mutated.
    """
    from pathlib import Path as _P

    from openakita.core.policy_v2 import (
        PolicyContext,
        SessionRole,
        ToolCallEvent,
    )
    from openakita.core.policy_v2.enums import ConfirmationMode
    from openakita.core.policy_v2.global_engine import make_preview_engine

    proposed_security = (body or {}).get("security") if isinstance(body, dict) else None

    try:
        if proposed_security is not None:
            from openakita.core.policy_v2.loader import load_policies_from_dict

            cfg, _report = load_policies_from_dict({"security": proposed_security}, strict=False)
            engine = make_preview_engine(cfg)
        else:
            engine = make_preview_engine()
            from openakita.core.policy_v2.global_engine import get_config_v2

            cfg = get_config_v2()
    except Exception as exc:
        return {"status": "error", "message": f"构建预览引擎失败: {exc}"}

    ctx = PolicyContext(
        session_id="dry_run_preview",
        workspace_roots=tuple(_P(p) for p in cfg.workspace.paths),
        session_role=SessionRole.AGENT,
        confirmation_mode=ConfirmationMode(cfg.confirmation.mode),
    )

    decisions: list[dict[str, object]] = []
    for tool, params in _DRY_RUN_SAMPLES:
        try:
            d = engine.evaluate_tool_call(ToolCallEvent(tool=tool, params=params), ctx)
            params_preview = ", ".join(f"{k}={v!s}"[:80] for k, v in params.items())
            decisions.append({
                "tool": tool,
                "tool_label_key": f"security.tool.{tool}",
                "params_preview": params_preview,
                "decision": d.action.value,
                "decision_label_key": f"security.decision.{d.action.value}",
                "reason": d.reason,
                "reason_code": (d.chain[-1].name if d.chain else "unknown"),
                "approval_class": d.approval_class.value if d.approval_class else None,
                "approval_class_label_key": (
                    f"security.approvalClass.{d.approval_class.value}"
                    if d.approval_class
                    else None
                ),
                "risk_level": d.shell_risk_level or "",
                "safety_immune_match": d.safety_immune_match,
                "flags": {
                    "safety_immune": bool(d.safety_immune_match),
                    "needs_sandbox": bool(d.needs_sandbox),
                    "needs_checkpoint": bool(d.needs_checkpoint),
                },
                "effective_confirmation_mode": ctx.confirmation_mode.value,
                "security_profile": cfg.profile.current,
            })
        except Exception as exc:
            decisions.append({
                "tool": tool,
                "params_preview": "",
                "decision": "error",
                "reason": f"engine error: {exc}",
                "approval_class": None,
                "risk_level": "",
                "safety_immune_match": None,
            })
    return {"decisions": decisions, "preview_uses_proposed": proposed_security is not None}


@router.get("/api/config/security/approval-matrix")
async def read_security_approval_matrix():
    from openakita.core.policy_v2 import ApprovalClass, ConfirmationMode, SessionRole
    from openakita.core.policy_v2.matrix import lookup as lookup_matrix

    rows: list[dict[str, object]] = []
    for role in SessionRole:
        for klass in ApprovalClass:
            cells = {
                mode.value: lookup_matrix(role, mode, klass).value
                for mode in ConfirmationMode
            }
            rows.append({"role": role.value, "approval_class": klass.value, "decisions": cells})
    return {
        "roles": [r.value for r in SessionRole],
        "modes": [m.value for m in ConfirmationMode],
        "classes": [c.value for c in ApprovalClass],
        "rows": rows,
        "baseline_only": True,
    }


@router.get("/api/config/security/zones")
async def read_security_zones():
    """Deprecated compatibility view over security.workspace/safety_immune."""
    data = _read_policies_yaml() or {}
    sec = data.get("security", {})
    workspace = sec.get("workspace", {}) if isinstance(sec.get("workspace"), dict) else {}
    immune = (
        sec.get("safety_immune", {})
        if isinstance(sec.get("safety_immune"), dict)
        else {}
    )
    return {
        "workspace": workspace.get("paths", ["${CWD}"]),
        "controlled": [],
        "protected": immune.get("paths", []),
        "forbidden": [],
        "default_zone": "workspace",
        "deprecated": True,
    }


@router.post("/api/config/security/zones")
async def write_security_zones(body: SecurityZonesUpdate):
    """Deprecated compatibility write into V2 path policy."""
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取当前配置文件，写入已取消以防止数据丢失"}
    sec = data.setdefault("security", {})
    sec["workspace"] = {"paths": body.workspace or ["${CWD}"]}
    sec["safety_immune"] = {"paths": [*body.protected, *body.forbidden]}
    sec.pop("zones", None)
    _mark_security_profile_custom(sec)
    _write_policies_yaml(data)
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="path_policy")
    except Exception:
        pass
    logger.info("[Config API] Updated security zones")
    return {"status": "ok"}


@router.get("/api/config/security/path-policy")
async def read_security_path_policy():
    data = _read_policies_yaml() or {}
    sec = data.get("security", {})
    workspace = sec.get("workspace", {}) if isinstance(sec.get("workspace"), dict) else {}
    immune = (
        sec.get("safety_immune", {})
        if isinstance(sec.get("safety_immune"), dict)
        else {}
    )
    internal_roots: list[str] = []
    try:
        from openakita.config import settings

        internal_roots.append(str(settings.data_dir))
    except Exception:
        pass
    return {
        "workspace_paths": workspace.get("paths", ["${CWD}"]),
        "safety_immune_paths": immune.get("paths", []),
        "internal_allowed_roots": internal_roots,
    }


@router.post("/api/config/security/path-policy")
async def write_security_path_policy(body: SecurityPathPolicyUpdate):
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取当前配置文件，写入已取消以防止数据丢失"}
    sec = data.setdefault("security", {})
    sec["workspace"] = {"paths": body.workspace_paths or ["${CWD}"]}
    sec["safety_immune"] = {"paths": body.safety_immune_paths}
    sec.pop("zones", None)
    profile = sec.setdefault("profile", {})
    if profile.get("current") != "custom":
        profile["base"] = profile.get("current") or "protect"
        profile["current"] = "custom"
    _write_policies_yaml(data)
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="path_policy")
    except Exception:
        pass
    logger.info("[Config API] Updated security path policy")
    return {"status": "ok"}


@router.get("/api/config/security/commands")
async def read_security_commands():
    """Read shell_risk command-pattern configuration.

    本接口是 V2 ``security.shell_risk`` 的 UI 视图。早期版本同时把数据
    写到 legacy ``security.command_patterns``——这一版彻底切换到 V2
    字段；如果 YAML 里只剩 legacy 数据，loader.migrate 在加载时已自动
    迁移过来，本接口直接读 ``security.shell_risk`` 即可。
    """
    from openakita.core.policy_v2.defaults import default_blocked_commands

    data = _read_policies_yaml() or {}
    sec = data.get("security", {}) if isinstance(data.get("security"), dict) else {}
    sr = sec.get("shell_risk", {}) if isinstance(sec.get("shell_risk"), dict) else {}
    # legacy fallback —— 老 YAML 还没被 migrate 过的情况下回退到 command_patterns，
    # 但仅作 read fallback，不会把它当作 source-of-truth 回写。
    cp = sec.get("command_patterns", {}) if isinstance(sec.get("command_patterns"), dict) else {}
    return {
        "custom_critical": sr.get("custom_critical", cp.get("custom_critical", [])),
        "custom_high": sr.get("custom_high", cp.get("custom_high", [])),
        "excluded_patterns": sr.get("excluded_patterns", cp.get("excluded_patterns", [])),
        "blocked_commands": (
            sr.get("blocked_commands")
            if sr.get("blocked_commands") is not None
            else cp.get("blocked_commands")
            if cp.get("blocked_commands") is not None
            else default_blocked_commands()
        ),
        "enabled": sr.get("enabled", True),
    }


@router.post("/api/config/security/commands")
async def write_security_commands(body: SecurityCommandsUpdate):
    """Update shell_risk configuration (canonical V2 location)."""
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取当前配置文件，写入已取消以防止数据丢失"}
    sec = data.setdefault("security", {})
    sr = sec.setdefault("shell_risk", {})
    sr["custom_critical"] = body.custom_critical
    sr["custom_high"] = body.custom_high
    sr["excluded_patterns"] = body.excluded_patterns
    sr["blocked_commands"] = body.blocked_commands
    # 彻底丢弃 legacy 槽位，避免双源
    sec.pop("command_patterns", None)
    _mark_security_profile_custom(sec)
    _write_policies_yaml(data)
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="commands")
    except Exception:
        pass
    logger.info("[Config API] Updated security commands (shell_risk)")
    return {"status": "ok"}


@router.get("/api/config/security/sandbox")
async def read_security_sandbox():
    """Read sandbox configuration."""
    data = _read_policies_yaml() or {}
    sec = data.get("security", {})
    mode = _mode_from_security(sec)
    sb = sec.get("sandbox", {})
    return {
        "enabled": sb.get("enabled", mode != "yolo"),
        "backend": sb.get("backend", "auto"),
        "sandbox_risk_levels": sb.get("sandbox_risk_levels", ["HIGH"]),
        "exempt_commands": sb.get("exempt_commands", []),
        "network": sb.get("network", {}),
    }


@router.post("/api/config/security/sandbox")
async def write_security_sandbox(body: SecuritySandboxUpdate):
    """Update sandbox configuration."""
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取当前配置文件，写入已取消以防止数据丢失"}
    if "security" not in data:
        data["security"] = {}
    if "sandbox" not in data["security"]:
        data["security"]["sandbox"] = {}
    sb = data["security"]["sandbox"]
    if body.enabled is not None:
        sb["enabled"] = body.enabled
    if body.backend is not None:
        sb["backend"] = body.backend
    if body.sandbox_risk_levels is not None:
        sb["sandbox_risk_levels"] = body.sandbox_risk_levels
    if body.exempt_commands is not None:
        sb["exempt_commands"] = body.exempt_commands
    _mark_security_profile_custom(data["security"])
    _write_policies_yaml(data)
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="sandbox")
    except Exception:
        pass
    logger.info("[Config API] Updated security sandbox")
    return {"status": "ok"}


@router.get("/api/config/permission-mode")
async def read_permission_mode():
    """读取当前安全模式（前端 cautious/smart/trust 与后端同步）。

    C8b-4：从 v2 ``PolicyConfigV2.confirmation.mode`` 直读，并用
    ``read_permission_mode_label`` 反向映射到 v1 product label——v1
    ``_frontend_mode`` shim 已删除。
    """
    try:
        from openakita.core.policy_v2 import read_permission_mode_label

        mode = read_permission_mode_label()
        return {"mode": mode, "label": _permission_label(mode)}
    except Exception as e:
        logger.debug(f"[Config API] permission-mode read fallback: {e}")
        return {"mode": "yolo", "label": "trust"}


class _PermissionModeBody(BaseModel):
    mode: str = "smart"


@router.post("/api/config/permission-mode")
async def write_permission_mode(body: _PermissionModeBody):
    """设置安全模式并持久化到 YAML。

    C8b-4：v1 ``pe._frontend_mode = mode`` 二次写已删除。POST 流程完全靠
    "YAML 持久化 → reset_policy_v2_layer() 触发 lazy re-load"链路：v2 lazy
    load 会重新读 YAML 并构造新的 ``PolicyConfigV2.confirmation.mode``，
    后续 ``read_permission_mode_label()`` 自然看到新值。
    """
    mode = _normalize_permission_mode(body.mode)
    if mode not in ("cautious", "smart", "yolo"):
        return {"status": "error", "message": f"无效的安全模式: {mode}"}
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        data = _read_policies_yaml()
        if data is None:
            return {"status": "error", "message": "无法读取当前配置文件，安全模式未切换"}
        sec = data.setdefault("security", {})
        _apply_permission_mode_defaults(sec, mode)
        if not _write_policies_yaml(data):
            return {"status": "error", "message": "配置写入失败，安全模式未切换"}
        reset_policy_v2_layer(scope="permission_mode")
        logger.info(f"[Config API] Permission mode set to: {mode}")
        return {"status": "ok", "mode": mode, "label": _permission_label(mode)}
    except Exception as e:
        logger.warning(f"[Config API] permission-mode write error: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/api/config/security-profile")
async def read_security_profile():
    data = _read_policies_yaml() or {}
    sec = data.get("security", {})
    profile = sec.get("profile") or {}
    current = _normalize_security_profile(str(profile.get("current") or _permission_label(_mode_from_security(sec))))
    return {
        "current": current,
        "base": profile.get("base"),
        "off_acknowledged_at": profile.get("off_acknowledged_at"),
    }


@router.post("/api/config/security-profile")
async def write_security_profile(body: SecurityProfileUpdate):
    profile = _normalize_security_profile(body.profile)
    if profile == "off" and (body.ack_phrase or "") != _SECURITY_PROFILE_OFF_ACK:
        return {
            "status": "error",
            "message": "必须手动输入【确认风险同意关闭】后才能关闭安全机制",
        }
    try:
        from datetime import UTC, datetime

        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        data = _read_policies_yaml()
        if data is None:
            return {"status": "error", "message": "无法读取当前配置文件，安全方案未切换"}
        sec = data.setdefault("security", {})
        previous = (sec.get("profile") or {}).get("current")
        _apply_security_profile_defaults(sec, profile)
        if profile == "off":
            sec.setdefault("profile", {})["off_acknowledged_at"] = datetime.now(UTC).isoformat()
            sec.setdefault("profile", {})["off_acknowledged_by"] = "local_user"
        if not _write_policies_yaml(data):
            return {"status": "error", "message": "配置写入失败，安全方案未切换"}
        _write_profile_event(profile, previous=previous)
        reset_policy_v2_layer(scope="security_profile")
        return {"status": "ok", "profile": profile}
    except Exception as e:
        logger.warning("[Config API] security profile write error: %s", e)
        return {"status": "error", "message": str(e)}


@router.get("/api/config/security/audit")
async def read_security_audit():
    """Read recent audit log entries + chain verification status (C16).

    The ``chain_verification`` field surfaces whether the audit JSONL is
    tamper-free. ``ok=True`` means every chained line verifies; ``ok=False``
    points at the first bad line. ``legacy_prefix_lines`` counts pre-C16
    rows (no ``row_hash``) which are reported but not flagged as tamper.
    SecurityView can render this as a badge.
    """
    try:
        from openakita.core.audit_logger import get_audit_logger
        from openakita.core.policy_v2.audit_chain import (
            verify_chain_with_rotation,
        )

        logger_instance = get_audit_logger()
        entries = logger_instance.tail(50)
        try:
            # C20: verify across rotation archives + active file. On
            # deployments where rotation is disabled (default) this
            # behaves identically to verify_chain(active_path) — so
            # backward compatible with the C16 contract.
            result = verify_chain_with_rotation(logger_instance._path)
            chain_verification = {
                "ok": result.ok,
                "total": result.total,
                "legacy_prefix_lines": result.legacy_prefix_lines,
                "truncated_tail_recovered": result.truncated_tail_recovered,
                "first_bad_line": result.first_bad_line,
                "reason": result.reason,
            }
        except Exception as verify_exc:  # noqa: BLE001 — UI-only field
            chain_verification = {
                "ok": None,
                "error": f"verify failed: {verify_exc}",
            }
        return {"entries": entries, "chain_verification": chain_verification}
    except Exception as e:
        return {"entries": [], "error": str(e)}


@router.get("/api/config/security/checkpoints")
async def list_checkpoints():
    """List recent file checkpoints."""
    try:
        from openakita.core.checkpoint import get_checkpoint_manager

        checkpoints = get_checkpoint_manager().list_checkpoints(20)
        return {"checkpoints": checkpoints}
    except Exception as e:
        return {"checkpoints": [], "error": str(e)}


@router.post("/api/config/security/checkpoint/rewind")
async def rewind_checkpoint(body: dict):
    """Rewind to a specific checkpoint."""
    checkpoint_id = body.get("checkpoint_id", "")
    if not checkpoint_id:
        return {"status": "error", "message": "checkpoint_id required"}
    try:
        from openakita.core.checkpoint import get_checkpoint_manager

        success = get_checkpoint_manager().rewind_to_checkpoint(checkpoint_id)
        return {"status": "ok" if success else "error"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/chat/security-confirm")
async def security_confirm(body: SecurityConfirmRequest):
    """Handle security confirmation from UI.

    C8b-3：调 ``policy_v2.apply_resolution``——它统一负责（1）唤醒
    reasoning_engine 上的 ``wait_for_resolution`` waiter；（2）按 decision
    类型写 SessionAllowlistManager / UserAllowlistManager（替代 v1
    ``mark_confirmed``）。
    """
    decision = body.normalized_decision()
    logger.info(f"[Security] Confirmation received: {body.confirm_id} -> {decision}")
    try:
        from openakita.core.policy_v2 import apply_resolution

        found = apply_resolution(body.confirm_id, decision)
        if not found:
            logger.warning(f"[Security] No pending confirm found for id={body.confirm_id}")
    except Exception as e:
        logger.warning(f"[Security] Failed to resolve confirmation: {e}")
    return {"status": "ok", "confirm_id": body.confirm_id, "decision": decision}


@router.post("/api/chat/security-confirm/batch")
async def security_confirm_batch(body: SecurityConfirmBatchRequest):
    """C18 Phase B：批量 resolve 同一 session 内 ≥2 个待 confirm。

    服务端先用 ``UIConfirmBus.list_batch_candidates`` 算出窗内 confirm_id
    列表，再对每个 id 走 ``apply_resolution``——所有 allowlist 副作用与
    单条 ``/api/chat/security-confirm`` 完全一致（决不绕过 SessionAllowlist/
    UserAllowlist 写入）。
    """
    decision = body.normalized_decision()
    try:
        from openakita.core.policy_v2 import apply_resolution
        from openakita.core.policy_v2.global_engine import get_config_v2
        from openakita.core.ui_confirm_bus import get_ui_confirm_bus

        bus = get_ui_confirm_bus()

        # Server-side clamp：window 由 POLICIES.yaml 控制。当 POLICIES.yaml
        # 设了 ``aggregation_window_seconds > 0`` 时，请求传入的更大窗会
        # 被收紧到配置值——避免恶意客户端用超大窗"清空整个 session 的所
        # 有等待 confirm"。
        try:
            cfg_window = float(
                get_config_v2().confirmation.aggregation_window_seconds
            )
        except Exception:
            cfg_window = 0.0

        if cfg_window > 0:
            if body.within_seconds is None or body.within_seconds > cfg_window:
                effective_window: float | None = cfg_window
            else:
                effective_window = body.within_seconds
        else:
            # 配置为 0（默认关）时，仍允许显式 within_seconds 传 None
            # —— 用于内部紧急脚本一次性 resolve 全部 session pending。
            # UI 路径在 enabled=false 时根本不会调这个端点。
            effective_window = body.within_seconds

        candidates = bus.list_batch_candidates(
            body.session_id, within_seconds=effective_window
        )
        logger.info(
            "[Security] Batch confirm session=%s decision=%s window=%s "
            "candidates=%d",
            body.session_id[:12] if body.session_id else "",
            decision,
            effective_window,
            len(candidates),
        )

        resolved_ids: list[str] = []
        missing_ids: list[str] = []
        for cid in candidates:
            try:
                found = apply_resolution(cid, decision)
                if found:
                    resolved_ids.append(cid)
                else:
                    missing_ids.append(cid)
            except Exception as e:
                logger.warning(
                    "[Security] Batch confirm failed for id=%s: %s", cid, e
                )
                missing_ids.append(cid)

        return {
            "status": "ok",
            "session_id": body.session_id,
            "decision": decision,
            "resolved_count": len(resolved_ids),
            "resolved_ids": resolved_ids,
            "missing_ids": missing_ids,
            "window_seconds": effective_window,
        }
    except Exception as e:
        logger.exception("[Security] Batch confirm endpoint failed")
        return {
            "status": "error",
            "session_id": body.session_id,
            "message": str(e),
        }


@router.post("/api/config/security/death-switch/reset")
async def reset_death_switch():
    """Reset the death switch (exit read-only mode)."""
    try:
        from openakita.core.security_actions import (
            maybe_broadcast_death_switch_reset,
        )
        from openakita.core.security_actions import (
            reset_death_switch as reset_death_switch_action,
        )

        result = reset_death_switch_action()
    except Exception as e:
        return {"status": "error", "message": str(e)}
    await maybe_broadcast_death_switch_reset(result)
    return {"status": "ok", "readonly_mode": False}


# ── Confirmation config CRUD ─────────────────────────────────────────


@router.get("/api/config/security/confirmation")
async def read_security_confirmation():
    """Read confirmation config."""
    data = _read_policies_yaml()
    if data is None:
        return {
            "mode": "yolo",
            "timeout_seconds": 60,
            "default_on_timeout": "deny",
            "confirm_ttl": 120,
            "aggregation_window_seconds": 0.0,
        }
    c = data.get("security", {}).get("confirmation", {})
    return {
        "mode": c.get("mode", _mode_from_security(data.get("security", {}))),
        "timeout_seconds": c.get("timeout_seconds", 60),
        "default_on_timeout": c.get("default_on_timeout", "deny"),
        "confirm_ttl": c.get("confirm_ttl", 120),
        # C18 Phase B：0 = 关；>0 = 允许 UI 批量 resolve。
        "aggregation_window_seconds": c.get("aggregation_window_seconds", 0.0),
    }


class _ConfirmationUpdate(BaseModel):
    mode: str | None = None
    timeout_seconds: int | None = None
    default_on_timeout: str | None = None
    confirm_ttl: float | None = None
    aggregation_window_seconds: float | None = None


@router.post("/api/config/security/confirmation")
async def write_security_confirmation(body: _ConfirmationUpdate):
    """Update confirmation config (PATCH semantics)."""
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取配置"}
    sec = data.setdefault("security", {})
    conf = sec.setdefault("confirmation", {})
    if body.mode is not None:
        aliases = {"yolo": "trust", "smart": "default", "cautious": "strict"}
        m = aliases.get(str(body.mode), str(body.mode))
        if m not in ("trust", "default", "accept_edits", "strict", "dont_ask"):
            return {"status": "error", "message": f"无效 mode: {body.mode}"}
        conf["mode"] = m
        _mark_security_profile_custom(sec)
    if body.timeout_seconds is not None:
        conf["timeout_seconds"] = body.timeout_seconds
    if body.default_on_timeout is not None:
        conf["default_on_timeout"] = body.default_on_timeout
    if body.confirm_ttl is not None:
        conf["confirm_ttl"] = body.confirm_ttl
    if body.aggregation_window_seconds is not None:
        v = float(body.aggregation_window_seconds)
        # Mirror the schema range (0..600) so writes via API match
        # POLICIES.yaml validation. Out-of-range = error rather than
        # silent clamp; otherwise user'd save 700 and read back 600
        # with no explanation.
        if v < 0 or v > 600:
            return {
                "status": "error",
                "message": "aggregation_window_seconds must be in [0, 600]",
            }
        conf["aggregation_window_seconds"] = v
    _mark_security_profile_custom(sec)
    _write_policies_yaml(data)
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="confirmation")
    except Exception:
        pass
    return {"status": "ok"}


# ── Self-protection config CRUD ──────────────────────────────────────
#
# UI 的"自我保护"页其实是三件不同的事：
#   - protected_dirs  → V2 ``safety_immune.paths``
#   - death_switch_*  → V2 ``death_switch.*``
#   - audit_*         → V2 ``audit.*``
# 早期版本仍把它写入旧的 ``self_protection`` 子树，导致引擎完全不读、UI 又看似生效。
# 本接口现在直接读写 V2 真源，旧字段只作 read fallback，不再回写。


_DEFAULT_PROTECTED_DIRS = ["data/", "identity/", "logs/", "src/"]


@router.get("/api/config/security/self-protection")
async def read_self_protection():
    data = _read_policies_yaml()
    if data is None:
        return {
            "enabled": True,
            "protected_dirs": _DEFAULT_PROTECTED_DIRS,
            "death_switch_threshold": 3,
            "death_switch_total_multiplier": 3,
            "audit_to_file": True,
            "audit_path": "",
            "readonly_mode": False,
        }
    sec = data.get("security", {}) if isinstance(data.get("security"), dict) else {}
    ds = sec.get("death_switch", {}) if isinstance(sec.get("death_switch"), dict) else {}
    audit_cfg = sec.get("audit", {}) if isinstance(sec.get("audit"), dict) else {}
    immune = sec.get("safety_immune", {}) if isinstance(sec.get("safety_immune"), dict) else {}
    # legacy fallback —— 仅读，写路径不再触碰
    legacy_sp = sec.get("self_protection", {}) if isinstance(sec.get("self_protection"), dict) else {}
    try:
        from openakita.core.policy_v2 import get_death_switch_tracker

        readonly = get_death_switch_tracker().is_readonly_mode()
    except Exception:
        readonly = False
    return {
        "enabled": ds.get("enabled", legacy_sp.get("enabled", True)),
        "protected_dirs": immune.get("paths", legacy_sp.get("protected_dirs", _DEFAULT_PROTECTED_DIRS)),
        "death_switch_threshold": ds.get(
            "threshold", legacy_sp.get("death_switch_threshold", 3)
        ),
        "death_switch_total_multiplier": ds.get(
            "total_multiplier", legacy_sp.get("death_switch_total_multiplier", 3)
        ),
        "audit_to_file": audit_cfg.get("enabled", legacy_sp.get("audit_to_file", True)),
        "audit_path": audit_cfg.get("log_path", legacy_sp.get("audit_path", "")),
        "readonly_mode": readonly,
    }


class _SelfProtectionUpdate(BaseModel):
    enabled: bool | None = None
    protected_dirs: list[str] | None = None
    death_switch_threshold: int | None = None
    death_switch_total_multiplier: int | None = None
    audit_to_file: bool | None = None
    audit_path: str | None = None


@router.post("/api/config/security/self-protection")
async def write_self_protection(body: _SelfProtectionUpdate):
    """Update self-protection config (PATCH semantics) — writes V2 fields only."""
    data = _read_policies_yaml()
    if data is None:
        return {"status": "error", "message": "无法读取配置"}
    sec = data.setdefault("security", {})
    ds = sec.setdefault("death_switch", {})
    audit_cfg = sec.setdefault("audit", {})
    immune = sec.setdefault("safety_immune", {})
    if body.enabled is not None:
        ds["enabled"] = body.enabled
    if body.protected_dirs is not None:
        immune["paths"] = body.protected_dirs
    if body.death_switch_threshold is not None:
        ds["threshold"] = body.death_switch_threshold
    if body.death_switch_total_multiplier is not None:
        ds["total_multiplier"] = body.death_switch_total_multiplier
    if body.audit_to_file is not None:
        audit_cfg["enabled"] = body.audit_to_file
    if body.audit_path is not None:
        audit_cfg["log_path"] = body.audit_path
    # 彻底丢弃 legacy 子树
    sec.pop("self_protection", None)
    _mark_security_profile_custom(sec)
    _write_policies_yaml(data)
    try:
        from openakita.core.policy_v2.global_engine import reset_policy_v2_layer

        reset_policy_v2_layer(scope="self_protection")
    except Exception:
        pass
    return {"status": "ok"}


# ── Security user allowlist CRUD ─────────────────────────────────────


@router.get("/api/config/security/allowlist")
async def read_user_allowlist():
    """Read the persistent security user_allowlist."""
    try:
        from openakita.core.security_actions import list_security_allowlist

        return list_security_allowlist()
    except Exception:
        return {"kind": "security_user_allowlist", "commands": [], "tools": []}


@router.get("/api/config/security/user-allowlist")
async def read_security_user_allowlist():
    """Explicit alias for security tool/command allow rules."""
    return await read_user_allowlist()


@router.post("/api/config/security/allowlist")
async def add_allowlist_entry(body: dict):
    """Add an entry to the persistent security user_allowlist."""
    entry_type = body.get("type", "command")
    entry = {k: v for k, v in body.items() if k != "type"}
    try:
        from openakita.core.security_actions import add_security_allowlist_entry

        return add_security_allowlist_entry(entry_type, entry)
    except Exception as e:
        return {"status": "error", "kind": "security_user_allowlist", "message": str(e)}


@router.post("/api/config/security/user-allowlist")
async def add_security_user_allowlist_entry(body: dict):
    """Explicit alias for adding security tool/command allow rules."""
    return await add_allowlist_entry(body)


@router.delete("/api/config/security/allowlist/{entry_type}/{index}")
async def delete_allowlist_entry(entry_type: str, index: int):
    """Remove an entry from the persistent security user_allowlist."""
    try:
        from openakita.core.security_actions import remove_security_allowlist_entry

        return remove_security_allowlist_entry(entry_type, index)
    except Exception as e:
        return {"status": "error", "kind": "security_user_allowlist", "message": str(e)}


@router.delete("/api/config/security/user-allowlist/{entry_type}/{index}")
async def delete_security_user_allowlist_entry(entry_type: str, index: int):
    """Explicit alias for removing security tool/command allow rules."""
    return await delete_allowlist_entry(entry_type, index)


@router.get("/api/config/extensions")
async def list_extensions():
    """Return status of optional external CLI tool extensions."""
    import os
    import shutil

    def _find_cli_anything() -> str | None:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            try:
                if not os.path.isdir(d):
                    continue
                for entry in os.listdir(d):
                    if entry.lower().startswith("cli-anything-"):
                        return os.path.join(d, entry)
            except OSError:
                continue
        return None

    oc_path = shutil.which("opencli")
    ca_path = _find_cli_anything()

    return {
        "extensions": [
            {
                "id": "opencli",
                "name": "OpenCLI",
                "description": "Operate websites via CLI, reusing Chrome login sessions",
                "description_zh": "将网站转化为 CLI 命令，复用 Chrome 登录态",
                "category": "Web",
                "installed": oc_path is not None,
                "path": oc_path,
                "install_cmd": "npm install -g opencli",
                "upgrade_cmd": "npm update -g opencli",
                "setup_cmd": "opencli setup",
                "homepage": "https://github.com/anthropics/opencli",
                "license": "MIT",
                "author": "Anthropic / Jack Wener",
            },
            {
                "id": "cli-anything",
                "name": "CLI-Anything",
                "description": "Control desktop software via auto-generated CLI interfaces",
                "description_zh": "为桌面软件自动生成 CLI 接口（GIMP、Blender 等）",
                "category": "Desktop",
                "installed": ca_path is not None,
                "path": ca_path,
                "install_cmd": "pip install cli-anything-gimp",
                "upgrade_cmd": "pip install --upgrade cli-anything-<app>",
                "setup_cmd": None,
                "homepage": "https://github.com/HKUDS/CLI-Anything",
                "license": "MIT",
                "author": "HKU Data Science Lab (HKUDS)",
            },
        ],
    }
