"""Plugin error codes with localized user-facing messages."""

from __future__ import annotations

from enum import Enum
from typing import Any


class PluginErrorCode(str, Enum):
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    MANIFEST_NOT_FOUND = "MANIFEST_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    NOT_FOUND = "NOT_FOUND"
    INSTALL_FAILED = "INSTALL_FAILED"
    UNINSTALL_FAILED = "UNINSTALL_FAILED"
    LOAD_FAILED = "LOAD_FAILED"
    RELOAD_FAILED = "RELOAD_FAILED"
    UNLOAD_FAILED = "UNLOAD_FAILED"
    TIMEOUT = "TIMEOUT"
    DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
    COMPATIBILITY_ERROR = "COMPATIBILITY_ERROR"
    ZIP_BOMB = "ZIP_BOMB"
    ZIP_INVALID = "ZIP_INVALID"
    CONFIG_INVALID = "CONFIG_INVALID"
    INVALID_ID = "INVALID_ID"
    MANAGER_UNAVAILABLE = "MANAGER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_MESSAGES: dict[PluginErrorCode, dict[str, str]] = {
    PluginErrorCode.NETWORK_ERROR: {
        "zh": "下载插件失败，请检查网络连接",
        "en": "Failed to download plugin, please check your network",
        "guidance_zh": "确认网络畅通后重试，或使用本地路径安装",
        "guidance_en": "Retry after confirming network connectivity, or install from a local path",
    },
    PluginErrorCode.INVALID_MANIFEST: {
        "zh": "插件包格式有误",
        "en": "Invalid plugin manifest",
        "guidance_zh": "请联系插件作者修复 plugin.json",
        "guidance_en": "Contact the plugin author to fix plugin.json",
    },
    PluginErrorCode.MANIFEST_NOT_FOUND: {
        "zh": "压缩包中未找到 plugin.json",
        "en": "No plugin.json found in archive",
        "guidance_zh": "请确认安装源包含有效的插件包",
        "guidance_en": "Verify the source contains a valid plugin package",
    },
    PluginErrorCode.PERMISSION_DENIED: {
        "zh": "该插件需要额外权限，请在设置中授权",
        "en": "This plugin requires additional permissions",
        "guidance_zh": "前往插件详情页授予所需权限",
        "guidance_en": "Go to plugin details to grant required permissions",
    },
    PluginErrorCode.ALREADY_EXISTS: {
        "zh": "该插件已安装",
        "en": "Plugin already installed",
        "guidance_zh": "将自动升级到新版本",
        "guidance_en": "Will automatically upgrade to the new version",
    },
    PluginErrorCode.NOT_FOUND: {
        "zh": "未找到该插件",
        "en": "Plugin not found",
        "guidance_zh": "请确认插件 ID 是否正确",
        "guidance_en": "Verify the plugin ID is correct",
    },
    PluginErrorCode.INSTALL_FAILED: {
        "zh": "插件安装失败",
        "en": "Plugin installation failed",
        "guidance_zh": "查看日志了解详情，或尝试重新安装",
        "guidance_en": "Check logs for details, or try reinstalling",
    },
    PluginErrorCode.UNINSTALL_FAILED: {
        "zh": "插件卸载失败",
        "en": "Plugin uninstall failed",
        "guidance_zh": "可能有文件被占用，请关闭相关进程后重试",
        "guidance_en": "Files may be in use; close related processes and retry",
    },
    PluginErrorCode.LOAD_FAILED: {
        "zh": "插件加载失败",
        "en": "Plugin failed to load",
        "guidance_zh": "查看插件日志了解详情",
        "guidance_en": "Check plugin logs for details",
    },
    PluginErrorCode.RELOAD_FAILED: {
        "zh": "插件重载失败",
        "en": "Plugin reload failed",
        "guidance_zh": "尝试禁用后重新启用",
        "guidance_en": "Try disabling and re-enabling the plugin",
    },
    PluginErrorCode.UNLOAD_FAILED: {
        "zh": "插件卸载失败",
        "en": "Plugin unload failed",
        "guidance_zh": "重启应用可彻底清理",
        "guidance_en": "Restart the application to fully clean up",
    },
    PluginErrorCode.TIMEOUT: {
        "zh": "插件操作超时",
        "en": "Plugin operation timed out",
        "guidance_zh": "插件可能有性能问题，请联系作者",
        "guidance_en": "The plugin may have performance issues; contact the author",
    },
    PluginErrorCode.DEPENDENCY_MISSING: {
        "zh": "插件缺少依赖",
        "en": "Plugin dependency missing",
        "guidance_zh": "请先安装所需的依赖插件",
        "guidance_en": "Install required dependency plugins first",
    },
    PluginErrorCode.COMPATIBILITY_ERROR: {
        "zh": "插件与当前版本不兼容",
        "en": "Plugin incompatible with current version",
        "guidance_zh": "请升级插件或 OpenAkita 到兼容版本",
        "guidance_en": "Upgrade the plugin or OpenAkita to a compatible version",
    },
    PluginErrorCode.ZIP_BOMB: {
        "zh": "安装包异常（文件过大或过多）",
        "en": "Suspicious archive (too large or too many files)",
        "guidance_zh": "请确认安装源可信",
        "guidance_en": "Verify the installation source is trustworthy",
    },
    PluginErrorCode.ZIP_INVALID: {
        "zh": "下载的文件不是有效的 zip 压缩包",
        "en": "Downloaded file is not a valid zip archive",
        "guidance_zh": "请确认链接指向正确的插件包",
        "guidance_en": "Verify the URL points to the correct plugin package",
    },
    PluginErrorCode.CONFIG_INVALID: {
        "zh": "插件配置文件格式错误",
        "en": "Invalid plugin configuration file",
        "guidance_zh": "重置配置或手动修复 config.json",
        "guidance_en": "Reset configuration or manually fix config.json",
    },
    PluginErrorCode.INVALID_ID: {
        "zh": "无效的插件 ID",
        "en": "Invalid plugin ID",
        "guidance_zh": "插件 ID 只能包含小写字母、数字、连字符和下划线",
        "guidance_en": "Plugin ID may only contain lowercase letters, digits, hyphens, and underscores",
    },
    PluginErrorCode.MANAGER_UNAVAILABLE: {
        "zh": "插件管理器暂不可用",
        "en": "Plugin manager is not available",
        "guidance_zh": "请等待系统完全启动后重试",
        "guidance_en": "Wait for the system to fully start and retry",
    },
    PluginErrorCode.INTERNAL_ERROR: {
        "zh": "内部错误",
        "en": "Internal error",
        "guidance_zh": "请查看日志并反馈给开发团队",
        "guidance_en": "Check logs and report to the development team",
    },
}


def get_error_message(code: PluginErrorCode, lang: str = "zh") -> str:
    entry = _MESSAGES.get(code, _MESSAGES[PluginErrorCode.INTERNAL_ERROR])
    return entry.get(lang, entry["en"])


def get_error_guidance(code: PluginErrorCode, lang: str = "zh") -> str:
    entry = _MESSAGES.get(code, _MESSAGES[PluginErrorCode.INTERNAL_ERROR])
    key = f"guidance_{lang}"
    return entry.get(key, entry.get("guidance_en", ""))


def make_error_response(
    code: PluginErrorCode,
    lang: str = "zh",
    detail: str = "",
) -> dict[str, Any]:
    """Build a unified error response dict for plugin API endpoints."""
    return {
        "ok": False,
        "error": {
            "code": code.value,
            "message": get_error_message(code, lang),
            "guidance": get_error_guidance(code, lang),
            "detail": detail,
        },
    }


class PluginError(Exception):
    """Structured plugin error with error code for API responses."""

    def __init__(self, code: PluginErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"[{code.value}] {detail}" if detail else code.value)
