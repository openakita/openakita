"""Shared runtime installer for optional IM channel dependencies."""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from openakita.channels.deps import CHANNEL_DEPS
from openakita.config import settings
from openakita.python_compat import patch_simplejson_jsondecodeerror
from openakita.runtime_manager import (
    IS_FROZEN,
    apply_runtime_pip_environment,
    get_app_python_executable,
    get_channel_deps_dir,
    get_python_executable,
    inject_module_paths_runtime,
    resolve_pip_index,
)

logger = logging.getLogger(__name__)
PrintFn = Callable[[str], None]


def _patch_backports_zstd() -> None:
    """Patch incomplete ``backports.zstd`` so urllib3 can import in bundles."""
    try:
        import backports.zstd as _bzstd
    except ImportError:
        return

    if hasattr(_bzstd, "ZstdError"):
        return

    class _ZstdError(Exception):
        """Stub ``ZstdError`` for backports.zstd compatibility."""

    _bzstd.ZstdError = _ZstdError
    logger.debug("Patched backports.zstd: added missing ZstdError stub")


def _enabled_channels_from_settings() -> list[str]:
    enabled: list[str] = []
    if settings.feishu_enabled:
        enabled.append("feishu")
    if settings.dingtalk_enabled:
        enabled.append("dingtalk")
    if settings.wework_enabled:
        enabled.append("wework")
    if settings.wework_ws_enabled:
        enabled.append("wework_ws")
    if settings.onebot_enabled:
        enabled.append("onebot")
    if settings.qqbot_enabled:
        enabled.append("qqbot")
    if settings.wechat_enabled:
        enabled.append("wechat")

    for bot_cfg in settings.im_bots or []:
        if bot_cfg.get("enabled", True):
            channel_type = bot_cfg.get("type", "")
            if channel_type and channel_type not in enabled:
                enabled.append(channel_type)
    return enabled


def _enabled_channels_from_env(env: dict[str, str]) -> list[str]:
    enabled_key_map = {
        "feishu": "FEISHU_ENABLED",
        "dingtalk": "DINGTALK_ENABLED",
        "wework": "WEWORK_ENABLED",
        "wework_ws": "WEWORK_WS_ENABLED",
        "onebot": "ONEBOT_ENABLED",
        "onebot_reverse": "ONEBOT_ENABLED",
        "qqbot": "QQBOT_ENABLED",
        "wechat": "WECHAT_ENABLED",
    }
    return [
        channel
        for channel, enabled_key in enabled_key_map.items()
        if env.get(enabled_key, "").strip().lower() in ("true", "1", "yes")
    ]


def _build_isolated_pip_env(py_path: Path) -> dict[str, str]:
    pip_env = apply_runtime_pip_environment(python_executable=str(py_path))
    if IS_FROZEN and py_path.parent.name == "_internal":
        path_parts = [str(py_path.parent)]
        for sub in ("Lib", "DLLs"):
            p = py_path.parent / sub
            if p.is_dir():
                path_parts.append(str(p))
        pip_env["PYTHONPATH"] = os.pathsep.join(path_parts)
    return pip_env


def _probe_python_runtime(py: str, env: dict[str, str], *, extra: dict) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [py, "-c", "import encodings, pip; print('ok')"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            **extra,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or "").strip()[-600:]


def _find_bundled_channel_wheels(py_path: Path) -> Path | None:
    candidates = [
        py_path.parent.parent / "modules" / "channel-deps" / "wheels",
        py_path.parent / "modules" / "channel-deps" / "wheels",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _purge_incompatible_websockets(target_dir: Path) -> list[str]:
    """清理 channel-deps 里残留的不兼容 ``websockets`` 安装。

    背景：``lark-oapi 1.6.x`` 间接要求 ``websockets<16``，但 ``--target`` 模式
    下 pip 不会主动卸载已存在的更高版本，于是 `websockets 16.x` 的 dist-info
    会一直挂在 channel-deps 里，让后续 `lark-oapi` 安装解析失败。

    本函数只动 ``channel-deps`` 这个隔离目录，绝不动用户全局 site-packages。
    返回被清理掉的条目名（用于日志/事件）。
    """
    if not target_dir.is_dir():
        return []

    removed: list[str] = []
    import re
    import shutil

    pattern = re.compile(r"^websockets-(\d+)\.[^-]+\.dist-info$", re.IGNORECASE)
    for entry in target_dir.iterdir():
        match = pattern.match(entry.name)
        if not match:
            continue
        try:
            major = int(match.group(1))
        except ValueError:
            continue
        if major < 16:
            continue
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed.append(entry.name)
        except Exception as exc:
            logger.warning("Failed to purge stale dist-info %s: %s", entry.name, exc)

    if removed:
        logger.info(
            "Purged %d incompatible websockets entries from channel-deps: %s",
            len(removed), ", ".join(removed),
        )
    return removed


def _select_pip_python() -> str | None:
    py = get_app_python_executable() or get_python_executable()
    if not py or (IS_FROZEN and py == sys.executable):
        return None
    return py


def _default_mirrors() -> list[tuple[str, str]]:
    effective_index = resolve_pip_index()
    mirrors: list[tuple[str, str]] = [
        (effective_index["url"], effective_index.get("trusted_host", ""))
    ]
    mirrors.extend(
        [
            ("https://mirrors.aliyun.com/pypi/simple/", "mirrors.aliyun.com"),
            ("https://pypi.tuna.tsinghua.edu.cn/simple/", "pypi.tuna.tsinghua.edu.cn"),
            ("https://pypi.mirrors.ustc.edu.cn/simple/", "pypi.mirrors.ustc.edu.cn"),
            ("https://pypi.org/simple/", "pypi.org"),
        ]
    )
    return mirrors


def ensure_channel_dependencies(
    *,
    workspace_env: dict[str, str] | None = None,
    print_fn: PrintFn | None = None,
) -> dict:
    """Install missing optional IM dependencies into the isolated channel target."""
    _patch_backports_zstd()
    patch_simplejson_jsondecodeerror(logger=logger)
    try:
        inject_module_paths_runtime()
    except Exception:
        logger.debug("failed to inject runtime module paths", exc_info=True)

    enabled_channels = (
        _enabled_channels_from_env(workspace_env)
        if workspace_env is not None
        else _enabled_channels_from_settings()
    )
    if not enabled_channels:
        return {"status": "ok", "installed": [], "missing": [], "message": "没有启用的 IM 通道"}

    missing: list[str] = []
    failed_import_names: list[str] = []
    for channel in enabled_channels:
        for import_name, pip_name in CHANNEL_DEPS.get(channel, []):
            try:
                importlib.import_module(import_name)
            except ImportError as exc:
                if (
                    import_name == "lark_oapi"
                    and "JSONDecodeError" in str(exc)
                    and "simplejson" in str(exc)
                ):
                    patch_simplejson_jsondecodeerror(logger=logger)
                    try:
                        importlib.import_module(import_name)
                        logger.info("lark_oapi import recovered after simplejson compatibility patch")
                        continue
                    except Exception:
                        pass
                if pip_name not in missing:
                    missing.append(pip_name)
                failed_import_names.append(import_name)
            except Exception as exc:
                logger.warning(
                    "Import check for %s (%s) hit unexpected error: %s: %s",
                    import_name,
                    channel,
                    type(exc).__name__,
                    exc,
                )

    if not missing:
        return {"status": "ok", "installed": [], "missing": [], "message": "所有依赖已就绪"}

    pkg_list = ", ".join(missing)
    py = _select_pip_python()
    if not py:
        message = f"未找到 OpenAkita 托管 Python，无法自动安装: {pkg_list}"
        logger.warning(message)
        if print_fn:
            print_fn(f"[yellow]⚠[/yellow] {message}\n  请前往「设置中心 → Python 环境」点击「一键修复」")
        return {"status": "error", "installed": [], "missing": missing, "message": message}

    target_dir = get_channel_deps_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    py_path = Path(py)
    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW

    pip_env = _build_isolated_pip_env(py_path)
    runtime_ok, probe_err = _probe_python_runtime(py, pip_env, extra=extra)
    if not runtime_ok and IS_FROZEN and py_path.parent.name == "_internal":
        pip_env["PYTHONHOME"] = str(py_path.parent)
        runtime_ok, probe_err = _probe_python_runtime(py, pip_env, extra=extra)
        if runtime_ok:
            logger.info("内置 Python 通过 PYTHONHOME 修正后可用: %s", py)

    if not runtime_ok:
        message = f"Python 运行时异常（无法导入 encodings/pip）: {probe_err}"
        logger.error("自动安装依赖前的 Python 运行时探测失败: %s", probe_err)
        if print_fn:
            print_fn(f"[red]✗[/red] {message}\n  建议：前往「设置中心 → Python 环境」点击「一键修复」。")
        return {"status": "error", "installed": [], "missing": missing, "message": message}

    def _on_install_success(source_label: str, packages: list[str]) -> None:
        logger.info("依赖安装成功 (source=%s, target=%s): %s", source_label, target_dir, ", ".join(packages))
        if print_fn:
            print_fn(f"[green]✓[/green] 依赖安装成功: {', '.join(packages)}")
        stale = [
            key
            for key in sys.modules
            if any(key == name or key.startswith(name + ".") for name in failed_import_names)
        ]
        for key in stale:
            del sys.modules[key]
        importlib.invalidate_caches()
        target_str = str(target_dir)
        if target_str not in sys.path:
            sys.path.append(target_str)
        try:
            inject_module_paths_runtime()
        except Exception:
            logger.debug("failed to inject module paths after channel deps install", exc_info=True)

    # 安装前先清掉 channel-deps 里残留的不兼容 websockets 16.x dist-info。
    # 即使本轮没指定 lark-oapi（例如只装钉钉），残留也无害；命中时只是把
    # 由 wework_ws / qqbot / onebot 在新版本约束下重装的 websockets 15.x
    # 升回干净状态。
    purged = _purge_incompatible_websockets(target_dir)
    if purged and print_fn:
        print_fn(
            f"[yellow]⚙[/yellow] 清理 channel-deps 残留的不兼容 websockets dist-info"
            f"（共 {len(purged)} 项），避免阻塞 lark-oapi 安装"
        )

    # 子进程超时：lark-oapi 间接拉 httpx/pycryptodome/qrcode/anyio 等近 30MB，
    # 国内镜像首次下载可能 >120s。统一抬到 600s（10 分钟）足够覆盖最坏情况，
    # pip 自己的 socket --timeout 同步从 60s 提到 120s。
    subprocess_timeout = 600
    pip_socket_timeout = "120"

    installed = False
    bundled_wheels = _find_bundled_channel_wheels(py_path) if IS_FROZEN else None
    if bundled_wheels is not None:
        if print_fn:
            print_fn(f"[yellow]⏳[/yellow] 自动安装 IM 通道依赖: [bold]{pkg_list}[/bold] (源: offline wheels)")
        offline_cmd = [
            py,
            "-m",
            "pip",
            "install",
            "--target",
            str(target_dir),
            "--no-index",
            "--find-links",
            str(bundled_wheels),
            "--prefer-binary",
            *missing,
        ]
        try:
            offline = subprocess.run(
                offline_cmd,
                env=pip_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=subprocess_timeout,
                **extra,
            )
            if offline.returncode == 0:
                _on_install_success("offline", missing)
                installed = True
            else:
                logger.warning("离线 wheels 安装失败，回退在线镜像: %s", (offline.stderr or offline.stdout or "").strip()[-400:])
        except Exception as exc:
            logger.warning("离线 wheels 安装异常，回退在线镜像: %s", exc)

    last_err = ""

    def _pip_install_via_mirrors(packages: list[str], label_prefix: str = "") -> tuple[bool, str]:
        """返回 ``(success, last_err_tail)``。所有镜像源都失败时返回 ``(False, 错误尾巴)``。"""
        nonlocal last_err
        local_err = ""
        for idx, (index_url, trusted_host) in enumerate(_default_mirrors()):
            source_label = trusted_host or index_url
            if print_fn:
                if idx == 0:
                    print_fn(
                        f"[yellow]⏳[/yellow] {label_prefix}自动安装 IM 通道依赖: "
                        f"[bold]{', '.join(packages)}[/bold] (源: {source_label}) ..."
                    )
                else:
                    print_fn(f"[yellow]⏳[/yellow] 切换镜像源重试: {source_label} ...")
            pip_cmd = [
                py,
                "-m",
                "pip",
                "install",
                "--target",
                str(target_dir),
                "-i",
                index_url,
                "--prefer-binary",
                "--timeout",
                pip_socket_timeout,
                *packages,
            ]
            if trusted_host:
                pip_cmd.extend(["--trusted-host", trusted_host])
            try:
                result = subprocess.run(
                    pip_cmd,
                    env=pip_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=subprocess_timeout,
                    **extra,
                )
                if result.returncode == 0:
                    _on_install_success(source_label, packages)
                    return True, ""
                local_err = (result.stderr or result.stdout or "").strip()[-500:]
                last_err = local_err
                logger.warning("镜像源 %s 安装失败 (exit %s): %s", source_label, result.returncode, local_err[-300:])
            except subprocess.TimeoutExpired:
                local_err = (
                    f"镜像源 {source_label} 在 {subprocess_timeout}s 内未完成下载"
                )
                last_err = local_err
                logger.warning(local_err)
            except Exception as exc:
                local_err = f"镜像源 {source_label} 安装异常: {exc}"
                last_err = local_err
                logger.warning(local_err)
        return False, local_err

    installed_packages: list[str] = []
    failed_packages: list[str] = []
    install_errors: dict[str, str] = {}
    if not installed:
        installed, batch_err = _pip_install_via_mirrors(missing)
        if installed:
            installed_packages = list(missing)
        elif batch_err and len(missing) == 1:
            # 单包模式下，批量失败 == 这个包失败；记下来供上层透传。
            install_errors[missing[0]] = batch_err

    if not installed and len(missing) > 1:
        logger.info("批量安装失败，尝试逐个安装 ...")
        for package in missing:
            ok, err_tail = _pip_install_via_mirrors([package], label_prefix="[逐个] ")
            if ok:
                installed_packages.append(package)
            else:
                failed_packages.append(package)
                if err_tail:
                    install_errors[package] = err_tail
        installed = bool(installed_packages)

    if not installed:
        # 把每个包的错误尾巴拼进 message，运维一眼就能看出"超时/冲突/网络"。
        detail_pairs = [f"{pkg}: {err[-200:]}" for pkg, err in install_errors.items()]
        detail = " | ".join(detail_pairs) if detail_pairs else (last_err or pkg_list)
        message = f"安装失败: {detail}"
        logger.error("所有镜像源均安装失败: %s | errors=%s", pkg_list, install_errors)
        if print_fn:
            print_fn(
                f"[red]✗[/red] 依赖安装失败（已尝试所有镜像源）: {pkg_list}\n"
                "  请检查网络连接，或前往「设置中心 → Python 环境」点击「一键修复」"
            )
        return {
            "status": "error",
            "installed": [],
            "missing": missing,
            "message": message,
            "errors": install_errors,
        }

    still_broken: list[str] = []
    wrong_source: list[str] = []
    target_resolved = target_dir.resolve()
    for name in failed_import_names:
        try:
            mod = importlib.import_module(name)
            mod_file = Path(getattr(mod, "__file__", "") or "").resolve()
            if mod_file and target_resolved not in [mod_file, *mod_file.parents]:
                wrong_source.append(f"{name} -> {mod_file}")
        except Exception as exc:
            logger.error("依赖 %s 安装后仍无法导入: %s", name, exc, exc_info=True)
            still_broken.append(name)

    status = "ok"
    message = f"已安装: {', '.join(installed_packages or missing)}"
    if failed_packages or still_broken or wrong_source:
        status = "warning"
        details = []
        if failed_packages:
            details.append(f"部分依赖安装失败: {', '.join(failed_packages)}")
        if still_broken:
            details.append(f"安装后仍无法导入: {', '.join(still_broken)}")
        if wrong_source:
            details.append(f"导入来源不在隔离目录: {', '.join(wrong_source[:5])}")
        message = "；".join(details)
        if print_fn:
            print_fn(f"[yellow]⚠[/yellow] {message}")

    return {
        "status": status,
        "installed": installed_packages or missing,
        "missing": failed_packages,
        "message": message,
        "wrong_source": wrong_source,
        "still_broken": still_broken,
        "errors": install_errors,
    }
