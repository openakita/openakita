"""Structured risk intent classification for user requests.

The classifier is intentionally deterministic and conservative.  It is the
single pre-ReAct gate for deciding whether a user message needs an explicit
confirmation before any free-form tools can run.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OperationKind(str, Enum):
    NONE = "none"
    READ = "read"
    EXPLAIN = "explain"
    INSPECT = "inspect"
    SUGGEST = "suggest"
    WRITE = "write"
    DELETE = "delete"
    RESET = "reset"
    DISABLE = "disable"
    OVERWRITE = "overwrite"
    EXECUTE = "execute"


class TargetKind(str, Enum):
    UNKNOWN = "unknown"
    SECURITY_USER_ALLOWLIST = "security_user_allowlist"
    SKILL_EXTERNAL_ALLOWLIST = "skill_external_allowlist"
    IM_ALLOWLIST = "im_allowlist"
    DEATH_SWITCH = "death_switch"
    SECURITY_POLICY = "security_policy"
    PROTECTED_FILE = "protected_file"
    SHELL_COMMAND = "shell_command"
    # 用户给出技能的 URL / 路径，希望通过 `install_skill` 工具装配。
    # 命中此 kind 时跳过 EXECUTE 通用路径，避免被误判为高危 shell。
    SKILL_INSTALL = "skill_install"


class AccessMode(str, Enum):
    READ_ONLY = "read_only"
    WRITE = "write"
    EXECUTE = "execute"


_READ_ONLY_RE = re.compile(
    r"(解释|说明|介绍|区别|查看|只查看|列出|查询|展示|分析|建议|如何|怎么|"
    r"explain|describe|show|list|view|inspect|read|query|suggest|compare)",
    re.IGNORECASE,
)
_WRITE_RE = re.compile(
    r"(删除|删掉|移除|清空|重置|覆盖|写入|修改|添加|禁用|关闭|卸载|销毁|"
    r"delete|remove|clear|reset|overwrite|write|modify|add|disable|destroy|drop|truncate)",
    re.IGNORECASE,
)
# 高敏感的 shell/系统执行动词（无条件升级 EXECUTE）。
# 中文「执行/运行」**不再**直接进入这个集合 — 它们是日常用语，
# 命中过宽会把"请你执行 edit_file""方案 OK，确认开始执行"等
# 普通推进语句误判为 high-risk shell。
_EXECUTE_RE = re.compile(
    r"(kill\s|rm\s+-rf|remove-item|del\s+/s|rmdir|force\s+push|push\s+--force|"
    r"sudo\s|chmod\s+777|format\s+[a-z]:)",
    re.IGNORECASE,
)

# 通用"执行/运行"动词；仅当与 _SHELL_CONTEXT_RE 同时出现时才升级 EXECUTE。
_GENERIC_DO_RE = re.compile(r"(执行|运行|跑一下|跑下|run\b|execute\b)", re.IGNORECASE)

# Shell/命令上下文词；用于判定通用执行动词是否真的指向 shell 命令。
_SHELL_CONTEXT_RE = re.compile(
    r"(shell|bash|powershell|pwsh|cmd|命令行|脚本|script|"
    r"命令\s|这条命令|这段命令|这个命令|run_shell|run_powershell)",
    re.IGNORECASE,
)

# ──────────────────────────────────────────────────────────────────────────
# 「装技能」专属意图识别
#
# 用户在前端"技能广场/装技能"页面发起的请求，文案常见为：
#   - "帮我装这个技能 https://github.com/owner/repo"
#   - "安装 gitee.com/foo/bar"
#   - "装一下 path/to/SKILL.md"
#   - "把这个技能装上：xxx"
#
# 老版本由于带 "装/安装" 等动词 + URL 中带 ".com/" 路径，被
# `_GENERIC_DO_RE` + `_SHELL_CONTEXT_RE` 间接判定为 EXECUTE/shell_command
# 而触发高危确认弹窗；用户确认后又因为 classification.action=None 报
# "该操作尚无受控执行入口"。
#
# 这里在 classify() 入口之前优先识别该类意图，命中即返回低风险 + 明确的
# action="install_skill"，绕开 EXECUTE 通用路径。
# ──────────────────────────────────────────────────────────────────────────

# 装技能动词关键词集合。中文与字母词混排，且常见在中文里没有空白边界
# （"帮我装" / "把xxx装上"），因此用 substring 而非 \b 匹配。
_SKILL_INSTALL_VERB_KEYWORDS: tuple[str, ...] = (
    # 中文动词
    "装",
    "安装",
    "启用",
    "加载",
    "部署",
    "试一下",
    "试试",
    # 英文动词
    "install",
    "setup",
    "enable",
)

# 技能名词关键词。
_SKILL_NOUN_KEYWORDS: tuple[str, ...] = (
    "技能",
    "skill",
)

# URL：http(s)://...
_SKILL_URL_RE = re.compile(r"https?://[^\s\u4e00-\u9fff，。；！？]+", re.IGNORECASE)

# 本地路径：以 / 或 \ 紧邻的 SKILL.md / skill.yaml / skill.yml；
# 排除 https?:// 前缀，让 URL 优先识别。
_SKILL_LOCAL_PATH_RE = re.compile(
    r"(?<!://)(?:[A-Za-z]:)?[\w./\\\-]*[/\\](?:SKILL\.md|skill\.yaml|skill\.yml)",
    re.IGNORECASE,
)


def _has_skill_install_verb(lowered: str) -> bool:
    return any(kw in lowered for kw in _SKILL_INSTALL_VERB_KEYWORDS)


def _has_skill_noun(lowered: str) -> bool:
    return any(kw in lowered for kw in _SKILL_NOUN_KEYWORDS)


def _detect_skill_install(text: str) -> dict[str, Any] | None:
    """识别「装技能」专属意图。

    返回 ``None`` 表示不是装技能；返回字典包含 ``url`` / ``path`` 之一即视为命中。

    判定规则（任一命中即返回）：
      A) 含 http(s):// URL 且（含装动词 + 含技能名词，或 URL 路径里包含
         SKILL.md / skill.yaml / skills? 等技能仓库特征）
      B) 含本地 SKILL.md / skill.yaml 路径（且含装动词或技能名词，避免误抓
         代码片段引用）

    URL 优先于本地路径，避免 "https://.../SKILL.md" 被路径正则抢走 URL 信息。
    """
    if not text:
        return None

    lowered = text.lower()
    has_verb = _has_skill_install_verb(lowered)
    has_noun = _has_skill_noun(lowered)

    # A) URL 优先
    url_match = _SKILL_URL_RE.search(text)
    if url_match:
        url = url_match.group(0).rstrip(".,;:!?'\"`)）】")
        url_lower = url.lower()
        url_has_skill_marker = bool(
            re.search(
                r"(skill\.md|skill\.yaml|skill\.yml|/skills?[/\-]|skill-pack|skill_pack)",
                url_lower,
            )
        )
        # URL 自带技能标记 → 强信号，无需动词
        if url_has_skill_marker:
            return {"url": url, "path": None}
        # 普通仓库 URL + 装动词 + 技能名词 → 装技能
        if has_verb and has_noun:
            return {"url": url, "path": None}

    # B) 本地路径，需要装动词或技能名词背书
    local_match = _SKILL_LOCAL_PATH_RE.search(text)
    if local_match and (has_verb or has_noun):
        return {"url": None, "path": local_match.group(0)}

    return None


# 对上一轮 ask_user/risk-confirm 的肯定回复。短路豁免，避免被
# 当成新的高风险请求重新走 risk gate（导致 confirm → confirm 死循环）。
_AFFIRMATIVE_REPLY_RE = re.compile(
    r"^\s*(确认继续|继续吧?|开始执行|方案\s*ok|方案\s*可以|"
    r"已确认|同意|可以|ok|yes|继续|go|approved)\s*[。.！!\s]*$",
    re.IGNORECASE,
)
# 只在显式上下文里抓 index：必须出现「第N条/项/个」或「index N」。
# 旧实现末尾 `?` 让上下文全可选，结果连日期年份 `2026` 都被当成 index=2026。
# 数字部分限制 1-3 位，进一步避免误抓四位年份/版本号。
_INDEX_RE = re.compile(
    r"(?:第\s*(\d{1,3})\s*(?:条|项|个))|(?:\bindex\s*[:=]?\s*(\d{1,3})\b)",
    re.IGNORECASE,
)
_ARITHMETIC_OR_COUNT_RE = re.compile(
    r"(\d+\s*[+\-*/×÷]\s*\d+|calculate|calculation|count|revised count|sum|times|"
    r"算一下|计算|合计|数量|总数|等于多少)",
    re.IGNORECASE,
)
_NON_ACTION_DISCUSSION_RE = re.compile(
    r"(suppose|hypothetical|what should you do|what would you do|if i say|"
    r"假设|如果我说|只是讨论|不需要执行|不要执行|如何处理|应该怎么)",
    re.IGNORECASE,
)

# 系统/组织合成消息前缀集合：用于在 classify() 入口短路豁免，避免把
# 「下属交付物正文里出现的「执行/运行」等普通中文动词」或「日期年份 2026」
# 误判为高风险 shell execute。
#
# 这些消息不是用户主动发起的指令，而是 OrgRuntime / reasoning_engine 内部
# 合成后塞进 root 节点 mailbox 的——如果继续走 risk gate 分类，root 节点会
# 在收到「[收到任务交付] 来自 xxx」时秒退（duration=0s）并回复「请确认风险」，
# 把组织协作链路打断（详见 2026-04-28 12:57:57 / 12:58:01 拦截日志）。
#
# 来源：
#   - openakita/orgs/runtime.py::_format_incoming_message  (13 种 [收到xxx])
#   - openakita/orgs/runtime.py::_push_summary_command_to_root ([用户指令最终汇总])
#   - openakita/core/reasoning_engine.py 多处自注入  ([系统] / [系统提示])
#   - openakita/core/agent.py::_prepare_session_context  ([以上是之前的对话历史)
ORG_SYNTH_PREFIXES: tuple[str, ...] = (
    # reasoning_engine / agent 自注入
    "[系统]",
    "[系统提示]",
    "[组织]",
    "[用户指令最终汇总]",
    "[以上是之前的对话历史",
    # OrgRuntime._format_incoming_message 13 种 type_label
    "[收到任务]",
    "[收到任务结果]",
    "[收到任务交付]",
    "[任务已通过验收]",
    "[任务被打回]",
    "[收到汇报]",
    "[收到提问]",
    "[收到回答]",
    "[收到上报]",
    "[收到组织公告]",
    "[收到部门公告]",
    "[收到反馈]",
    "[收到握手请求]",
    "[收到消息]",
)


def _is_org_synthesized_message(text: str) -> bool:
    """判断消息是否为系统/组织内部合成（命中即跳过 risk gate）。

    使用 ``startswith(tuple)`` 做前缀匹配，仅看正文开头，避免误伤
    「正文中恰好提到 [收到xxx]」等真实用户输入。
    """
    if not text:
        return False
    return text.lstrip().startswith(ORG_SYNTH_PREFIXES)


@dataclass(frozen=True)
class RiskIntentResult:
    risk_level: RiskLevel = RiskLevel.NONE
    operation_kind: OperationKind = OperationKind.NONE
    target_kind: TargetKind = TargetKind.UNKNOWN
    access_mode: AccessMode = AccessMode.READ_ONLY
    requires_confirmation: bool = False
    reason: str = ""
    action: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        data["operation_kind"] = self.operation_kind.value
        data["target_kind"] = self.target_kind.value
        data["access_mode"] = self.access_mode.value
        return data


class RiskIntentClassifier:
    """Classify whether a request is read-only or a dangerous write."""

    def classify(self, message: str, intent: Any | None = None) -> RiskIntentResult:
        text = (message or "").strip()

        # 短路：系统/组织合成消息一律视为非危险输入，不再做关键词分类。
        # 否则下属交付物正文里的「执行/运行/重置」等动词会被 _EXECUTE_RE /
        # _WRITE_RE 误判为高风险，阻断 root 节点的验收与汇总流程。
        if _is_org_synthesized_message(text):
            return RiskIntentResult(
                risk_level=RiskLevel.NONE,
                operation_kind=OperationKind.NONE,
                target_kind=TargetKind.UNKNOWN,
                access_mode=AccessMode.READ_ONLY,
                requires_confirmation=False,
                reason="org_synthesized_message",
                action=None,
                parameters={},
            )

        # 短路：用户对上一轮 ask_user / risk-confirm 的简短肯定回复
        # （"确认继续 / 开始执行 / 方案 OK / 同意"等）一律视为非新动作，
        # 让上层 _handle_pending_risk_answer 处理；避免被本分类器再次升级。
        if _AFFIRMATIVE_REPLY_RE.match(text):
            return RiskIntentResult(
                risk_level=RiskLevel.NONE,
                operation_kind=OperationKind.NONE,
                target_kind=TargetKind.UNKNOWN,
                access_mode=AccessMode.READ_ONLY,
                requires_confirmation=False,
                reason="affirmative_reply_to_prior_turn",
                action=None,
                parameters={},
            )

        # 短路：识别"装技能"专属意图。命中即直接返回低风险 + install_skill
        # 引导，避免被通用 EXECUTE 路径误判为高危 shell。
        skill_install = _detect_skill_install(text)
        if skill_install is not None:
            params: dict[str, Any] = {}
            if skill_install.get("url"):
                params["skill_url"] = skill_install["url"]
            if skill_install.get("path"):
                params["skill_path"] = skill_install["path"]
            return RiskIntentResult(
                risk_level=RiskLevel.LOW,
                operation_kind=OperationKind.WRITE,
                target_kind=TargetKind.SKILL_INSTALL,
                access_mode=AccessMode.WRITE,
                requires_confirmation=False,
                reason="skill_install_intent",
                action="install_skill",
                parameters=params,
            )

        lowered = text.lower()
        target = self._target_kind(lowered)
        operation = self._operation_kind(text)

        # Read-only access wins over topic keywords.  "解释 allowlist" should
        # never be blocked merely because it mentions a sensitive object.
        if operation in {
            OperationKind.READ,
            OperationKind.EXPLAIN,
            OperationKind.INSPECT,
            OperationKind.SUGGEST,
        }:
            return RiskIntentResult(
                risk_level=RiskLevel.LOW if target != TargetKind.UNKNOWN else RiskLevel.NONE,
                operation_kind=operation,
                target_kind=target,
                access_mode=AccessMode.READ_ONLY,
                requires_confirmation=False,
                reason="read_only_request",
                action=self._read_action(target),
                parameters=self._extract_parameters(text, target),
            )

        if self._is_non_action_discussion(text, intent, target, operation):
            return RiskIntentResult(
                risk_level=RiskLevel.NONE,
                operation_kind=OperationKind.NONE,
                target_kind=target,
                access_mode=AccessMode.READ_ONLY,
                requires_confirmation=False,
                reason="non_action_discussion",
                action=self._read_action(target),
                parameters=self._extract_parameters(text, target),
            )

        destructive_signal = self._intent_destructive_signal(intent)
        if operation == OperationKind.NONE and destructive_signal:
            operation = OperationKind.WRITE

        if operation == OperationKind.EXECUTE:
            return RiskIntentResult(
                risk_level=RiskLevel.HIGH,
                operation_kind=operation,
                target_kind=target if target != TargetKind.UNKNOWN else TargetKind.SHELL_COMMAND,
                access_mode=AccessMode.EXECUTE,
                requires_confirmation=True,
                reason="execute_or_shell_risk",
                action=None,
                parameters=self._extract_parameters(text, target),
            )

        if operation in {
            OperationKind.WRITE,
            OperationKind.DELETE,
            OperationKind.RESET,
            OperationKind.DISABLE,
            OperationKind.OVERWRITE,
        }:
            risk = RiskLevel.HIGH if self._is_sensitive_target(target) else RiskLevel.MEDIUM
            if target == TargetKind.UNKNOWN and not self._intent_high_risk_signal(intent):
                risk = RiskLevel.LOW
            return RiskIntentResult(
                risk_level=risk,
                operation_kind=operation,
                target_kind=target,
                access_mode=AccessMode.WRITE,
                requires_confirmation=risk in {RiskLevel.MEDIUM, RiskLevel.HIGH},
                reason="dangerous_write_request",
                action=self._write_action(operation, target),
                parameters=self._extract_parameters(text, target),
            )

        return RiskIntentResult(
            risk_level=RiskLevel.LOW if target != TargetKind.UNKNOWN else RiskLevel.NONE,
            operation_kind=OperationKind.NONE,
            target_kind=target,
            access_mode=AccessMode.READ_ONLY,
            requires_confirmation=False,
            reason="no_write_intent",
            action=self._read_action(target),
            parameters=self._extract_parameters(text, target),
        )

    @staticmethod
    def _intent_destructive_signal(intent: Any | None) -> bool:
        complexity = getattr(intent, "complexity", None)
        return bool(getattr(complexity, "destructive_potential", False))

    @staticmethod
    def _intent_high_risk_signal(intent: Any | None) -> bool:
        hint = str(getattr(intent, "risk_level_hint", "") or "").lower()
        if hint in {"risklevelhint.high", "high", "medium", "risklevelhint.medium"}:
            return True
        complexity = getattr(intent, "complexity", None)
        return bool(getattr(complexity, "destructive_potential", False))

    @classmethod
    def _is_non_action_discussion(
        cls,
        text: str,
        intent: Any | None,
        target: TargetKind,
        operation: OperationKind,
    ) -> bool:
        if operation == OperationKind.EXECUTE or cls._is_sensitive_target(target):
            return False

        requires_tools = getattr(intent, "requires_tools", None)
        risk_hint = str(getattr(intent, "risk_level_hint", "") or "").lower()
        if requires_tools is False and risk_hint in {"", "none", "low", "risklevelhint.none", "risklevelhint.low"}:
            return True

        if _ARITHMETIC_OR_COUNT_RE.search(text):
            return True

        if _NON_ACTION_DISCUSSION_RE.search(text):
            return True

        return False

    @staticmethod
    def _operation_kind(text: str) -> OperationKind:
        lowered = text.lower()
        # READ-only 路径：READ 关键词 + 没有任何写/执行词
        if (
            _READ_ONLY_RE.search(text)
            and not _WRITE_RE.search(text)
            and not _EXECUTE_RE.search(text)
            and not _GENERIC_DO_RE.search(text)
        ):
            if re.search(r"(解释|说明|介绍|区别|explain|describe|compare)", text, re.IGNORECASE):
                return OperationKind.EXPLAIN
            if re.search(r"(建议|如何|怎么|suggest)", text, re.IGNORECASE):
                return OperationKind.SUGGEST
            return OperationKind.INSPECT
        # 高敏感 shell 动词无条件 EXECUTE
        if _EXECUTE_RE.search(text):
            return OperationKind.EXECUTE
        # 通用「执行/运行」**仅当**伴随明确 shell 上下文时才升 EXECUTE
        if _GENERIC_DO_RE.search(text) and _SHELL_CONTEXT_RE.search(text):
            return OperationKind.EXECUTE
        if re.search(r"(删除|删掉|移除|delete|remove|drop|truncate)", lowered, re.IGNORECASE):
            return OperationKind.DELETE
        if re.search(r"(重置|reset)", lowered, re.IGNORECASE):
            return OperationKind.RESET
        if re.search(r"(禁用|关闭|disable)", lowered, re.IGNORECASE):
            return OperationKind.DISABLE
        if re.search(r"(覆盖|overwrite)", lowered, re.IGNORECASE):
            return OperationKind.OVERWRITE
        if _WRITE_RE.search(text):
            return OperationKind.WRITE
        return OperationKind.NONE

    @staticmethod
    def _target_kind(lowered: str) -> TargetKind:
        if "security user_allowlist" in lowered or "安全白名单" in lowered:
            return TargetKind.SECURITY_USER_ALLOWLIST
        if "user_allowlist" in lowered and "skill" not in lowered:
            return TargetKind.SECURITY_USER_ALLOWLIST
        if "external_allowlist" in lowered or "技能" in lowered and "allowlist" in lowered:
            return TargetKind.SKILL_EXTERNAL_ALLOWLIST
        if "im" in lowered and ("allowlist" in lowered or "白名单" in lowered):
            return TargetKind.IM_ALLOWLIST
        if "death-switch" in lowered or "death_switch" in lowered or "死亡开关" in lowered:
            return TargetKind.DEATH_SWITCH
        if "安全策略" in lowered or "policies" in lowered or "policy" in lowered:
            return TargetKind.SECURITY_POLICY
        if any(s in lowered for s in ("identity/", "data/", ".ssh", "hosts")):
            return TargetKind.PROTECTED_FILE
        if "allowlist" in lowered or "白名单" in lowered:
            return TargetKind.SECURITY_USER_ALLOWLIST
        return TargetKind.UNKNOWN

    @staticmethod
    def _is_sensitive_target(target: TargetKind) -> bool:
        return target in {
            TargetKind.SECURITY_USER_ALLOWLIST,
            TargetKind.DEATH_SWITCH,
            TargetKind.SECURITY_POLICY,
            TargetKind.PROTECTED_FILE,
            TargetKind.SHELL_COMMAND,
        }

    @staticmethod
    def _read_action(target: TargetKind) -> str | None:
        if target == TargetKind.SECURITY_USER_ALLOWLIST:
            return "list_security_allowlist"
        if target == TargetKind.SKILL_EXTERNAL_ALLOWLIST:
            return "list_skill_external_allowlist"
        return None

    @staticmethod
    def _write_action(operation: OperationKind, target: TargetKind) -> str | None:
        if target == TargetKind.SECURITY_USER_ALLOWLIST and operation == OperationKind.DELETE:
            return "remove_security_allowlist_entry"
        if target == TargetKind.DEATH_SWITCH and operation == OperationKind.RESET:
            return "reset_death_switch"
        if target == TargetKind.SKILL_EXTERNAL_ALLOWLIST:
            return "set_skill_external_allowlist"
        return None

    @staticmethod
    def _extract_parameters(text: str, target: TargetKind) -> dict[str, Any]:
        params: dict[str, Any] = {}
        match = _INDEX_RE.search(text)
        if match:
            # _INDEX_RE 现有两个捕获组：第N条/项/个 vs index N，
            # 任何一个非空都视为 index 命中（数字已限定 1-3 位）。
            raw = match.group(1) or match.group(2)
            if raw:
                params["index"] = int(raw)
        if target == TargetKind.SECURITY_USER_ALLOWLIST:
            if re.search(r"(tool|工具)", text, re.IGNORECASE):
                params["entry_type"] = "tool"
            else:
                params["entry_type"] = "command"
        return params


def classify_risk_intent(message: str, intent: Any | None = None) -> RiskIntentResult:
    return RiskIntentClassifier().classify(message, intent)
