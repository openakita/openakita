"""
桌面通知模块 - 跨平台系统通知（Windows/macOS/Linux）

任务完成时通过操作系统原生通知提醒用户。
Windows Toast / macOS Notification Center / Linux notify-send
系统通知自带音效，无需额外音频文件。
"""

import asyncio
import logging
import platform
import subprocess
import sys

logger = logging.getLogger(__name__)

_system = platform.system()


def _notify_windows(title: str, body: str, sound: bool = True) -> bool:
    """Windows Toast 通知（PowerShell，无需额外依赖）"""
    sound_xml = ""
    if sound:
        sound_xml = '<audio src="ms-winsoundevent:Notification.Default"/>'
    else:
        sound_xml = '<audio silent="true"/>'

    # 转义 PowerShell 字符串中的特殊字符
    safe_title = title.replace('"', '`"').replace("'", "''")
    safe_body = body.replace('"', '`"').replace("'", "''")

    # 获取 Logo 路径
    logo_xml = ""
    try:
        from ..config import settings

        logo_path = settings.project_root / "docs" / "assets" / "logo.png"
        if logo_path.exists():
            logo_uri = f"file:///{logo_path.as_posix()}"
            logo_xml = f'<image placement="appLogoOverride" src="{logo_uri}"/>'
    except Exception:
        pass

    ps_script = f"""
$aumid = 'com.openakita.setupcenter'
$rp = "HKCU:\\SOFTWARE\\Classes\\AppUserModelId\\$aumid"
if (!(Test-Path $rp)) {{ New-Item $rp -Force | Out-Null; Set-ItemProperty $rp -Name DisplayName -Value 'OpenAkita Desktop' }}

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType = WindowsRuntime] | Out-Null

$template = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{safe_title}</text>
      <text>{safe_body}</text>
      {logo_xml}
    </binding>
  </visual>
  {sound_xml}
</toast>
"@

$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($toast)
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(
                f"Windows toast PowerShell failed (rc={result.returncode}): {stderr[:200]}"
            )
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("Windows toast PowerShell timed out (10s)")
        return False
    except FileNotFoundError:
        logger.warning("PowerShell not found, cannot send Windows toast notification")
        return False
    except Exception as e:
        logger.warning(f"Windows toast failed: {e}")
        return False


def _notify_macos(title: str, body: str, sound: bool = True) -> bool:
    """macOS 通知（osascript，无需额外依赖）"""
    sound_clause = ' sound name "default"' if sound else ""
    script = (
        f'display notification "{_escape_applescript(body)}" '
        f'with title "{_escape_applescript(title)}"{sound_clause}'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(f"macOS osascript failed (rc={result.returncode}): {stderr[:200]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("macOS osascript timed out (10s)")
        return False
    except FileNotFoundError:
        logger.warning("osascript not found, cannot send macOS notification")
        return False
    except Exception as e:
        logger.warning(f"macOS notification failed: {e}")
        return False


def _escape_applescript(text: str) -> str:
    """转义 AppleScript 字符串中的特殊字符"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _notify_linux(title: str, body: str, sound: bool = True) -> bool:
    """Linux 通知（notify-send，大多数桌面环境预装）"""
    try:
        cmd = [
            "notify-send",
            "--app-name=OpenAkita",
            "--urgency=normal",
            title,
            body,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(f"notify-send failed (rc={result.returncode}): {stderr[:200]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.warning("notify-send timed out (10s)")
        return False
    except FileNotFoundError:
        logger.warning("notify-send not found, cannot send Linux notification")
        return False
    except Exception as e:
        logger.warning(f"Linux notification failed: {e}")
        return False


def _fallback_beep() -> None:
    """兜底：如果系统通知失败，至少发出终端提示音"""
    try:
        if _system == "Windows":
            import winsound

            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        pass


def send_desktop_notification(
    title: str,
    body: str,
    *,
    sound: bool = True,
    fallback_beep: bool = True,
) -> bool:
    """
    发送桌面通知（同步版本）。

    Args:
        title: 通知标题
        body: 通知正文
        sound: 是否播放通知音（默认 True）
        fallback_beep: 通知失败时是否发出终端提示音

    Returns:
        是否成功发送通知
    """
    logger.info(f"Sending desktop notification: [{title}] {body[:60]}")
    ok = False
    try:
        if _system == "Windows":
            ok = _notify_windows(title, body, sound)
        elif _system == "Darwin":
            ok = _notify_macos(title, body, sound)
        elif _system == "Linux":
            ok = _notify_linux(title, body, sound)
        else:
            logger.warning(f"Unsupported platform for desktop notification: {_system}")
    except Exception as e:
        logger.warning(f"Desktop notification error: {e}")

    if ok:
        logger.info(f"Desktop notification sent successfully ({_system})")
    else:
        logger.warning(f"Desktop notification failed ({_system}), fallback_beep={fallback_beep}")
        if fallback_beep:
            _fallback_beep()

    return ok


async def send_desktop_notification_async(
    title: str,
    body: str,
    *,
    sound: bool = True,
    fallback_beep: bool = True,
) -> bool:
    """发送桌面通知（异步版本，不阻塞事件循环）"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: send_desktop_notification(title, body, sound=sound, fallback_beep=fallback_beep),
    )


def notify_task_completed(
    task_name: str,
    success: bool,
    *,
    duration_seconds: float = 0,
    sound: bool = True,
) -> bool:
    """
    任务完成通知的便捷函数。

    Args:
        task_name: 任务名称/描述
        success: 是否成功
        duration_seconds: 任务耗时（秒）
        sound: 是否播放提示音
    """
    if success:
        title = "✅ OpenAkita 任务完成"
        body = task_name
    else:
        title = "❌ OpenAkita 任务失败"
        body = task_name

    if duration_seconds > 0:
        if duration_seconds >= 60:
            minutes = int(duration_seconds // 60)
            seconds = int(duration_seconds % 60)
            body += f"（耗时 {minutes}分{seconds}秒）"
        else:
            body += f"（耗时 {int(duration_seconds)}秒）"

    return send_desktop_notification(title, body, sound=sound)


async def notify_task_completed_async(
    task_name: str,
    success: bool,
    *,
    duration_seconds: float = 0,
    sound: bool = True,
) -> bool:
    """任务完成通知的异步便捷函数。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: notify_task_completed(
            task_name,
            success,
            duration_seconds=duration_seconds,
            sound=sound,
        ),
    )
