"""
Identity file management routes: list, read, write, validate, compile, reload.

Provides HTTP API for the frontend Identity Management Panel.
Supports editing SOUL.md, AGENT.md, USER.md, MEMORY.md, personas, policies,
and runtime compilation artifacts.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from openakita.config import settings
from openakita.prompt.budget import estimate_tokens

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/identity", tags=["identity"])


# ─── Constants ──────────────────────────────────────────────────────────

_BUDGET_MAP = {
    "SOUL.md": 3600,
    "runtime/agent.core.md": 1200,
    "runtime/user.summary.md": 300,
    "runtime/persona.custom.md": 150,
    "prompts/policies.md": 1200,
}

_EDITABLE_SOURCE_FILES = [
    "SOUL.md",
    "AGENT.md",
    "USER.md",
    "MEMORY.md",
    "POLICIES.yaml",
    "prompts/policies.md",
]

_RUNTIME_FILES = [
    "runtime/agent.core.md",
    "runtime/user.summary.md",
    "runtime/persona.custom.md",
]

_RESTRICTED_FILES = {
    "AGENT.md",
    "MEMORY.md",
    "POLICIES.yaml",
    "prompts/policies.md",
}

_FILE_WARNINGS: dict[str, str] = {
    "SOUL.md": "soul",
    "AGENT.md": "agent",
    "USER.md": "user",
    "MEMORY.md": "memory",
    "POLICIES.yaml": "policiesYaml",
    "prompts/policies.md": "policiesMd",
}


# ─── Helpers ────────────────────────────────────────────────────────────


def _identity_dir() -> Path:
    return settings.identity_path


def _resolve_file(name: str) -> Path:
    """Resolve a relative identity file name to an absolute path, with traversal guard."""
    identity = _identity_dir()
    target = (identity / name).resolve()
    if not str(target).startswith(str(identity.resolve())):
        raise HTTPException(400, "Path traversal not allowed")
    return target


def _get_agent(request: Request):
    agent = getattr(request.app.state, "agent", None)
    if agent is None:
        raise HTTPException(503, "Agent not initialized")
    return agent


# ─── Validation ─────────────────────────────────────────────────────────


def validate_identity_file(name: str, content: str) -> dict[str, list[str]]:
    """Validate identity file content before saving.

    Returns dict with 'errors' (block save) and 'warnings' (allow with confirmation).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if name == "POLICIES.yaml":
        try:
            import yaml
            data = yaml.safe_load(content)
            if data is None:
                pass  # empty file is ok
            elif not isinstance(data, dict):
                errors.append("根节点必须是 YAML 字典")
            else:
                allowed_keys = {"tool_policies", "scope_policy", "auto_confirm"}
                unknown = set(data.keys()) - allowed_keys
                if unknown:
                    errors.append(f"未知的顶层键: {', '.join(sorted(unknown))}")
                tp = data.get("tool_policies")
                if tp is not None:
                    if not isinstance(tp, list):
                        errors.append("tool_policies 必须是列表")
                    else:
                        for i, item in enumerate(tp):
                            if not isinstance(item, dict):
                                errors.append(f"tool_policies[{i}] 必须是字典")
                            elif "tool_name" not in item:
                                errors.append(f"tool_policies[{i}] 缺少必需的 tool_name 字段")
                sp = data.get("scope_policy")
                if sp is not None and not isinstance(sp, dict):
                    errors.append("scope_policy 必须是字典")
                ac = data.get("auto_confirm")
                if ac is not None and not isinstance(ac, bool):
                    errors.append("auto_confirm 必须是布尔值")
        except ImportError:
            warnings.append("PyYAML 未安装，无法校验 YAML 结构")
        except Exception as e:
            errors.append(f"YAML 语法错误: {e}")

    elif name == "MEMORY.md":
        from openakita.memory.types import MEMORY_MD_MAX_CHARS
        if len(content) > MEMORY_MD_MAX_CHARS:
            warnings.append(
                f"内容超出 {MEMORY_MD_MAX_CHARS} 字符限制"
                f"（当前 {len(content)}），保存后将被自动截断"
            )

    elif name == "USER.md":
        bold_fields = re.findall(r"\*\*(.+?)\*\*:", content)
        if content.strip() and not bold_fields:
            warnings.append("未检测到 **字段名**: 格式，系统自动学习功能可能失效")

    elif name.startswith("personas/") and name.endswith(".md"):
        known_sections = {"性格特征", "沟通风格", "提示词片段", "表情包配置"}
        found = re.findall(r"^## (.+)", content, re.MULTILINE)
        unknown_sections = [s.strip() for s in found if s.strip() not in known_sections]
        if unknown_sections:
            warnings.append(
                f"包含非标准段落: {', '.join(unknown_sections)}，"
                "不影响保存但可能不被系统识别"
            )

    elif name == "prompts/policies.md":
        system_titles = {"三条红线（必须遵守）", "意图声明（每次纯文本回复必须遵守）",
                         "切换模型的工具上下文隔离"}
        found = re.findall(r"^## (.+)", content, re.MULTILINE)
        overridden = [s.strip() for s in found if s.strip() in system_titles]
        if overridden:
            warnings.append(
                f"以下段落会被系统内置策略覆盖: {', '.join(overridden)}"
            )

    return {"errors": errors, "warnings": warnings}


# ─── Models ─────────────────────────────────────────────────────────────


class FileWriteRequest(BaseModel):
    name: str
    content: str
    force: bool = False  # skip warnings confirmation


class ValidateRequest(BaseModel):
    name: str
    content: str


# ─── Routes ─────────────────────────────────────────────────────────────


@router.get("/files")
async def list_identity_files():
    """List all editable identity files with metadata."""
    identity = _identity_dir()
    files: list[dict[str, Any]] = []

    all_names = list(_EDITABLE_SOURCE_FILES)

    # discover persona files
    personas_dir = identity / "personas"
    if personas_dir.exists():
        for p in sorted(personas_dir.glob("*.md")):
            rel = f"personas/{p.name}"
            if rel not in all_names:
                all_names.append(rel)

    # add runtime files
    all_names.extend(_RUNTIME_FILES)

    for name in all_names:
        path = identity / name
        entry: dict[str, Any] = {
            "name": name,
            "exists": path.exists(),
            "restricted": name in _RESTRICTED_FILES,
            "is_runtime": name.startswith("runtime/"),
            "warning_key": _FILE_WARNINGS.get(name, "runtime" if name.startswith("runtime/") else None),
            "budget_tokens": _BUDGET_MAP.get(name),
        }
        if path.exists():
            stat = path.stat()
            entry["size"] = stat.st_size
            entry["modified"] = datetime.fromtimestamp(stat.st_mtime).isoformat()
            content = path.read_text(encoding="utf-8")
            entry["tokens"] = estimate_tokens(content)
        files.append(entry)

    return {"files": files}


@router.get("/file")
async def read_identity_file(name: str):
    """Read a single identity file."""
    path = _resolve_file(name)
    if not path.exists():
        raise HTTPException(404, f"File not found: {name}")
    content = path.read_text(encoding="utf-8")
    return {
        "name": name,
        "content": content,
        "tokens": estimate_tokens(content),
        "budget_tokens": _BUDGET_MAP.get(name),
    }


@router.put("/file")
async def write_identity_file(req: FileWriteRequest, request: Request):
    """Write an identity file with validation.

    Returns 400 if validation errors exist.
    Returns 200 with warnings if there are warnings and force=false.
    Returns 200 with saved=true when saved.
    """
    name = req.name

    # Block writing to .compiled_at or other non-editable paths
    if name.startswith("runtime/.") or name.startswith("compiled/"):
        raise HTTPException(403, "Cannot write to internal files")

    path = _resolve_file(name)

    # Validate
    result = validate_identity_file(name, req.content)
    if result["errors"]:
        raise HTTPException(400, detail={
            "message": "格式校验失败",
            "errors": result["errors"],
            "warnings": result["warnings"],
        })
    if result["warnings"] and not req.force:
        return {
            "saved": False,
            "needs_confirm": True,
            "warnings": result["warnings"],
        }

    # Write
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(req.content, encoding="utf-8")

    return {
        "saved": True,
        "name": name,
        "tokens": estimate_tokens(req.content),
    }


@router.post("/validate")
async def validate_file(req: ValidateRequest):
    """Validate file content without saving."""
    result = validate_identity_file(req.name, req.content)
    return result


@router.post("/reload")
async def reload_identity(request: Request):
    """Hot-reload identity files into the running agent."""
    agent = _get_agent(request)

    identity = getattr(agent, "identity", None)
    if identity is None:
        local = getattr(agent, "_local_agent", None)
        if local:
            identity = getattr(local, "identity", None)
    if identity is None:
        raise HTTPException(500, "Identity not available on agent")

    identity.reload()

    # Force recompile runtime artifacts
    from openakita.prompt.compiler import compile_all
    identity_dir = _identity_dir()
    compile_all(identity_dir)

    # Rebuild system prompt if possible
    _try_rebuild_prompt(agent)

    return {"status": "reloaded"}


@router.post("/compile")
async def compile_identity(request: Request, mode: str = "rules"):
    """Trigger identity compilation.

    mode=llm: LLM-assisted (async, higher quality)
    mode=rules: Rule-based (sync, fast, uses static fallbacks)
    """
    identity_dir = _identity_dir()
    mode_used = mode

    if mode == "llm":
        agent = _get_agent(request)
        brain = getattr(agent, "brain", None)
        if brain is None:
            local = getattr(agent, "_local_agent", None)
            if local:
                brain = getattr(local, "brain", None)
        if brain:
            from openakita.prompt.compiler import PromptCompiler
            compiler = PromptCompiler(brain=brain)
            await compiler.compile_all(identity_dir)
            mode_used = "llm"
        else:
            from openakita.prompt.compiler import compile_all
            compile_all(identity_dir)
            mode_used = "rules (LLM not available)"
    else:
        from openakita.prompt.compiler import compile_all
        compile_all(identity_dir)
        mode_used = "rules"

    # Rebuild system prompt
    agent = getattr(request.app.state, "agent", None)
    if agent:
        _try_rebuild_prompt(agent)

    from openakita.prompt.compiler import get_compiled_content
    compiled = get_compiled_content(identity_dir)
    _key_rt = {
        "agent_core": "runtime/agent.core.md",
        "user": "runtime/user.summary.md",
        "persona_custom": "runtime/persona.custom.md",
    }
    compiled_info = {}
    for key, text in compiled.items():
        compiled_info[key] = {
            "content": text,
            "tokens": estimate_tokens(text),
            "budget_tokens": _BUDGET_MAP.get(_key_rt.get(key, "")),
        }

    return {
        "mode_used": mode_used,
        "compiled_files": compiled_info,
    }


@router.get("/compile-status")
async def compile_status():
    """Get compilation status: token counts, budget, freshness."""
    identity_dir = _identity_dir()

    from openakita.prompt.compiler import check_compiled_outdated, get_compiled_content
    compiled = get_compiled_content(identity_dir)
    outdated = check_compiled_outdated(identity_dir)

    runtime_dir = identity_dir / "runtime"
    timestamp_file = runtime_dir / ".compiled_at"
    last_compiled = None
    if timestamp_file.exists():
        try:
            last_compiled = timestamp_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    key_to_runtime = {
        "agent_core": "runtime/agent.core.md",
        "user": "runtime/user.summary.md",
        "persona_custom": "runtime/persona.custom.md",
    }
    status = {}
    for key, content in compiled.items():
        runtime_name = key_to_runtime.get(key, f"runtime/{key}.md")
        status[key] = {
            "tokens": estimate_tokens(content),
            "budget_tokens": _BUDGET_MAP.get(runtime_name),
            "has_content": bool(content.strip()),
        }

    return {
        "outdated": outdated,
        "last_compiled": last_compiled,
        "files": status,
    }


# ─── Persona import / template ───────────────────────────────────────────

_PERSONA_TEMPLATE = """\
# 自定义人格名称

> 预设角色: 用一句话描述这个角色

## 性格特征
- 特征1：描述
- 特征2：描述
- 特征3：描述

## 沟通风格
- 正式程度: neutral（可选 formal / neutral / casual）
- 幽默感: occasional（可选 none / occasional / frequent）
- 回复长度: adaptive（可选 brief / moderate / adaptive / detailed）
- 情感距离: friendly（可选 professional / friendly / intimate）
- 称呼: 默认使用用户设定的称呼

## 主动行为
- 描述角色会主动做哪些事
- 主动提醒、建议等行为模式

## 活人感配置
- 主动消息: 低频 / 中频 / 高频（每日最多 N 条）
- 消息类型: 任务提醒、关心问候等
- 闲聊问候: 低频主动发起

## 表情包配置
- 使用频率: rare / occasional / frequent
- 偏好分类: 通用
- 使用场景: 任务完成、鼓励等

## 提示词片段
你是一个[角色描述]，[核心行为准则]。[沟通风格要求]。
"""


@router.get("/persona/template")
async def download_persona_template():
    """Download a persona MD template file for users to fill in."""
    return PlainTextResponse(
        content=_PERSONA_TEMPLATE,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="persona_template.md"',
        },
    )


@router.post("/persona/import")
async def import_persona_file(file: UploadFile = File(...)):
    """Import a persona MD file. Saves to identity/personas/ with the uploaded filename.

    No strict validation — the file is saved as-is.
    """
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    fname = file.filename
    if not fname.endswith(".md"):
        fname = fname + ".md"

    safe_name = re.sub(r"[^\w\-.]", "_", fname)
    if safe_name.startswith(".") or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(400, "非法文件名")

    content_bytes = await file.read()
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "文件编码必须为 UTF-8")

    personas_dir = _identity_dir() / "personas"
    personas_dir.mkdir(parents=True, exist_ok=True)
    target = (personas_dir / safe_name).resolve()

    if not str(target).startswith(str(personas_dir.resolve())):
        raise HTTPException(400, "Path traversal not allowed")

    target.write_text(content, encoding="utf-8")

    persona_id = safe_name.removesuffix(".md")
    logger.info(f"[Identity API] Imported persona file: {safe_name}")

    return {
        "saved": True,
        "name": f"personas/{safe_name}",
        "persona_id": persona_id,
        "tokens": estimate_tokens(content),
    }


# ─── Internal helpers ───────────────────────────────────────────────────


def _try_rebuild_prompt(agent) -> None:
    """Best-effort rebuild of the agent's system prompt after identity changes."""
    try:
        local = getattr(agent, "_local_agent", agent)
        if hasattr(local, "_build_system_prompt_compiled_sync"):
            new_prompt = local._build_system_prompt_compiled_sync()
            ctx = getattr(local, "_context", None)
            if ctx:
                ctx.system = new_prompt
                logger.info("[Identity API] System prompt rebuilt after identity change")
                return
        # Fallback: try identity.get_compiled_prompt for simpler setups
        identity = getattr(local, "identity", None)
        if identity and hasattr(identity, "get_compiled_prompt"):
            base_prompt = identity.get_compiled_prompt()
            ctx = getattr(local, "_context", None)
            if ctx:
                ctx.system = base_prompt
                logger.info("[Identity API] System prompt rebuilt (identity-only fallback)")
    except Exception as e:
        logger.warning(f"[Identity API] Failed to rebuild system prompt: {e}")
