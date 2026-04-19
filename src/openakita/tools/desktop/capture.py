"""
Windows Desktop Automation - Screenshot module

High-performance screenshot implementation based on mss, supporting:
- Full screen / specified monitor screenshot
- Region screenshot
- Window screenshot
- Automatic compression / scaling
- Screenshot caching
"""

import base64
import io
import sys
import time

from PIL import Image

from .config import get_config
from .types import BoundingBox, ScreenshotInfo

# Platform check
if sys.platform != "win32":
    raise ImportError(
        f"Desktop automation module is Windows-only. Current platform: {sys.platform}"
    )

try:
    import mss
    import mss.tools
except ImportError:
    from openakita.tools._import_helper import import_or_hint

    raise ImportError(import_or_hint("mss"))


def _get_self_hwnd() -> int | None:
    """Get the HWND of the current process's console/window for exclusion."""
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        return hwnd if hwnd else None
    except Exception:
        return None


def _hide_self_window() -> int | None:
    """Temporarily hide the current process window before screenshot.

    Returns the HWND if hidden, or None.
    """
    try:
        import ctypes

        hwnd = _get_self_hwnd()
        if hwnd:
            SW_HIDE = 0
            ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
            import time

            time.sleep(0.05)  # let the compositor update
            return hwnd
    except Exception:
        pass
    return None


def _restore_window(hwnd: int) -> None:
    """Restore a previously hidden window."""
    try:
        import ctypes

        SW_SHOW = 5
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOW)
    except Exception:
        pass


class ScreenCapture:
    """
    Screen capture class

    High-performance screenshot implementation using the mss library.
    Safety enhancements (inspired by CC Computer Use):
    - Automatically excludes its own window during capture
    - Coordinate system consistent with screenshot dimensions
    """

    def __init__(self):
        self._sct: mss.mss | None = None
        self._last_screenshot: Image.Image | None = None
        self._last_screenshot_time: float = 0
        self._last_screenshot_info: ScreenshotInfo | None = None
        self._exclude_self: bool = True

    @property
    def sct(self) -> mss.mss:
        """Get the mss instance (lazy-loaded)"""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def get_monitors(self) -> list[dict]:
        """
        Get all monitor information

        Returns:
            List of monitors, each containing left, top, width, height.
            Index 0 is the combined area of all monitors.
            Index 1+ are individual monitors.
        """
        return list(self.sct.monitors)

    def get_screen_size(self, monitor: int = 0) -> tuple[int, int]:
        """
        Get screen dimensions

        Args:
            monitor: Monitor index; 0 means all monitors combined.

        Returns:
            (width, height)
        """
        monitors = self.sct.monitors
        if monitor >= len(monitors):
            monitor = 0
        m = monitors[monitor]
        return (m["width"], m["height"])

    def capture(
        self,
        monitor: int | None = None,
        region: tuple[int, int, int, int] | None = None,
        use_cache: bool = True,
    ) -> Image.Image:
        """
        Capture the screen

        Args:
            monitor: Monitor index; None uses default configuration.
            region: Region (x, y, width, height); None means full screen.
            use_cache: Whether to use cache (repeated captures within a short interval return cached result).

        Returns:
            PIL Image object
        """
        config = get_config().capture

        # Check cache
        if use_cache and self._last_screenshot is not None:
            cache_age = time.time() - self._last_screenshot_time
            if cache_age < config.cache_ttl:
                # If requesting the same region, return cached result
                if self._last_screenshot_info and (
                    monitor == self._last_screenshot_info.monitor
                    and region == self._last_screenshot_info.region
                ):
                    return self._last_screenshot.copy()

        # Determine capture area
        if region is not None:
            # Use specified region
            x, y, w, h = region
            capture_area = {
                "left": x,
                "top": y,
                "width": w,
                "height": h,
            }
        else:
            # Use monitor
            mon_idx = monitor if monitor is not None else config.default_monitor
            monitors = self.sct.monitors
            if mon_idx >= len(monitors):
                mon_idx = 0
            capture_area = monitors[mon_idx]

        # Hide self window before capture (inspired by CC prepareForAction)
        hidden_hwnd = None
        if self._exclude_self:
            hidden_hwnd = _hide_self_window()

        try:
            sct_img = self.sct.grab(capture_area)
        finally:
            if hidden_hwnd:
                _restore_window(hidden_hwnd)

        # Convert to PIL Image
        img = Image.frombytes(
            "RGB",
            (sct_img.width, sct_img.height),
            sct_img.rgb,
        )

        # Update cache
        self._last_screenshot = img.copy()
        self._last_screenshot_time = time.time()
        self._last_screenshot_info = ScreenshotInfo(
            width=img.width,
            height=img.height,
            monitor=monitor if monitor is not None else config.default_monitor,
            region=region,
        )

        return img

    def capture_window(
        self,
        bbox: BoundingBox,
        window_title: str | None = None,
    ) -> Image.Image:
        """
        Capture a specified window region

        Args:
            bbox: Window bounding box
            window_title: Window title (for logging)

        Returns:
            PIL Image object
        """
        region = bbox.to_region()  # (x, y, width, height)
        img = self.capture(region=region, use_cache=False)

        # Update screenshot info
        if self._last_screenshot_info:
            self._last_screenshot_info.window_title = window_title

        return img

    def capture_region(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> Image.Image:
        """
        Capture a specified region

        Args:
            x, y: Top-left corner coordinates
            width, height: Width and height

        Returns:
            PIL Image object
        """
        return self.capture(region=(x, y, width, height), use_cache=False)

    def resize_for_api(
        self,
        img: Image.Image,
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> Image.Image:
        """
        Resize image for API calls

        Maintains aspect ratio, scaling down to fit within max dimensions.

        Args:
            img: Original image
            max_width: Maximum width; None uses configuration.
            max_height: Maximum height; None uses configuration.

        Returns:
            Resized image
        """
        config = get_config().capture
        max_w = max_width or config.max_width
        max_h = max_height or config.max_height

        # If image is already small enough, return as-is
        if img.width <= max_w and img.height <= max_h:
            return img

        # Calculate scale ratio
        ratio = min(max_w / img.width, max_h / img.height)
        new_width = int(img.width * ratio)
        new_height = int(img.height * ratio)

        # High-quality resize
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def to_base64(
        self,
        img: Image.Image,
        format: str = "JPEG",
        quality: int | None = None,
        resize_for_api: bool = True,
    ) -> str:
        """
        Convert image to base64 encoding

        Args:
            img: PIL Image object
            format: Image format (JPEG, PNG)
            quality: JPEG quality; None uses configuration.
            resize_for_api: Whether to auto-resize to save API cost.

        Returns:
            Base64-encoded string
        """
        config = get_config().capture

        # Optional resize
        if resize_for_api:
            img = self.resize_for_api(img)

        # Convert to bytes
        buffer = io.BytesIO()
        if format.upper() == "JPEG":
            # Convert to RGB (JPEG does not support alpha channel)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buffer, format="JPEG", quality=quality or config.compression_quality)
        else:
            img.save(buffer, format=format)

        # Encode to base64
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def to_data_url(
        self,
        img: Image.Image,
        format: str = "JPEG",
        quality: int | None = None,
        resize_for_api: bool = True,
    ) -> str:
        """
        Convert image to data URL format

        Suitable for APIs requiring data:image/... format.

        Args:
            img: PIL Image object
            format: Image format
            quality: JPEG quality
            resize_for_api: Whether to auto-resize

        Returns:
            Data URL string
        """
        b64 = self.to_base64(img, format, quality, resize_for_api)
        mime_type = "image/jpeg" if format.upper() == "JPEG" else f"image/{format.lower()}"
        return f"data:{mime_type};base64,{b64}"

    def save(
        self,
        img: Image.Image,
        path: str,
        format: str | None = None,
        quality: int | None = None,
    ) -> str:
        """
        Save screenshot to file

        Args:
            img: PIL Image object
            path: Save path
            format: Image format; None infers from path.
            quality: JPEG quality

        Returns:
            Saved file path
        """
        config = get_config().capture

        save_kwargs = {}
        if format and format.upper() == "JPEG":
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            save_kwargs["quality"] = quality or config.compression_quality

        img.save(path, format=format, **save_kwargs)
        return path

    def clear_cache(self) -> None:
        """Clear screenshot cache"""
        self._last_screenshot = None
        self._last_screenshot_time = 0
        self._last_screenshot_info = None

    def close(self) -> None:
        """Release resources"""
        if self._sct is not None:
            self._sct.close()
            self._sct = None
        self.clear_cache()

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Global instance
_capture: ScreenCapture | None = None


def get_capture() -> ScreenCapture:
    """Get the global screen capture instance"""
    global _capture
    if _capture is None:
        _capture = ScreenCapture()
    return _capture


def screenshot(
    monitor: int | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    """
    Convenience function: capture the screen

    Args:
        monitor: Monitor index
        region: Region (x, y, width, height)

    Returns:
        PIL Image object
    """
    return get_capture().capture(monitor=monitor, region=region)


def screenshot_base64(
    monitor: int | None = None,
    region: tuple[int, int, int, int] | None = None,
    resize: bool = True,
) -> str:
    """
    Convenience function: capture the screen and return base64

    Args:
        monitor: Monitor index
        region: Region
        resize: Whether to resize to save API cost

    Returns:
        Base64-encoded string
    """
    capture = get_capture()
    img = capture.capture(monitor=monitor, region=region)
    return capture.to_base64(img, resize_for_api=resize)
