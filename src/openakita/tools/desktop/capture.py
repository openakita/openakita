"""
Windows 桌面自动化 - 截图模块

基于 mss 实现高性能截图，支持：
- 全屏/指定显示器截图
- 区域截图
- 窗口截图
- 自动压缩/缩放
- 截图缓存
"""

import base64
import io
import sys
import time

from PIL import Image

from .config import get_config
from .types import BoundingBox, ScreenshotInfo

# 平台检查
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


class ScreenCapture:
    """
    屏幕截图类

    使用 mss 库实现高性能截图
    """

    def __init__(self):
        self._sct: mss.mss | None = None
        self._last_screenshot: Image.Image | None = None
        self._last_screenshot_time: float = 0
        self._last_screenshot_info: ScreenshotInfo | None = None

    @property
    def sct(self) -> mss.mss:
        """获取 mss 实例（懒加载）"""
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def get_monitors(self) -> list[dict]:
        """
        获取所有显示器信息

        Returns:
            显示器列表，每个包含 left, top, width, height
            索引 0 是所有显示器的组合区域
            索引 1+ 是各个独立显示器
        """
        return list(self.sct.monitors)

    def get_screen_size(self, monitor: int = 0) -> tuple[int, int]:
        """
        获取屏幕尺寸

        Args:
            monitor: 显示器索引，0 表示所有显示器组合

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
        截取屏幕

        Args:
            monitor: 显示器索引，None 使用默认配置
            region: 区域 (x, y, width, height)，None 表示全屏
            use_cache: 是否使用缓存（短时间内重复截图返回缓存）

        Returns:
            PIL Image 对象
        """
        config = get_config().capture

        # 检查缓存
        if use_cache and self._last_screenshot is not None:
            cache_age = time.time() - self._last_screenshot_time
            if cache_age < config.cache_ttl:
                # 如果请求的是同样的区域，返回缓存
                if self._last_screenshot_info and (
                    monitor == self._last_screenshot_info.monitor
                    and region == self._last_screenshot_info.region
                ):
                    return self._last_screenshot.copy()

        # 确定截图区域
        if region is not None:
            # 使用指定区域
            x, y, w, h = region
            capture_area = {
                "left": x,
                "top": y,
                "width": w,
                "height": h,
            }
        else:
            # 使用显示器
            mon_idx = monitor if monitor is not None else config.default_monitor
            monitors = self.sct.monitors
            if mon_idx >= len(monitors):
                mon_idx = 0
            capture_area = monitors[mon_idx]

        # 截图
        sct_img = self.sct.grab(capture_area)

        # 转换为 PIL Image
        img = Image.frombytes(
            "RGB",
            (sct_img.width, sct_img.height),
            sct_img.rgb,
        )

        # 更新缓存
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
        截取指定窗口区域

        Args:
            bbox: 窗口边界框
            window_title: 窗口标题（用于记录）

        Returns:
            PIL Image 对象
        """
        region = bbox.to_region()  # (x, y, width, height)
        img = self.capture(region=region, use_cache=False)

        # 更新截图信息
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
        截取指定区域

        Args:
            x, y: 左上角坐标
            width, height: 宽高

        Returns:
            PIL Image 对象
        """
        return self.capture(region=(x, y, width, height), use_cache=False)

    def resize_for_api(
        self,
        img: Image.Image,
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> Image.Image:
        """
        为 API 调用调整图片大小

        保持宽高比，缩放到不超过最大尺寸

        Args:
            img: 原始图片
            max_width: 最大宽度，None 使用配置
            max_height: 最大高度，None 使用配置

        Returns:
            调整后的图片
        """
        config = get_config().capture
        max_w = max_width or config.max_width
        max_h = max_height or config.max_height

        # 如果图片已经足够小，直接返回
        if img.width <= max_w and img.height <= max_h:
            return img

        # 计算缩放比例
        ratio = min(max_w / img.width, max_h / img.height)
        new_width = int(img.width * ratio)
        new_height = int(img.height * ratio)

        # 使用高质量缩放
        return img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    def to_base64(
        self,
        img: Image.Image,
        format: str = "JPEG",
        quality: int | None = None,
        resize_for_api: bool = True,
    ) -> str:
        """
        将图片转换为 base64 编码

        Args:
            img: PIL Image 对象
            format: 图片格式 (JPEG, PNG)
            quality: JPEG 质量，None 使用配置
            resize_for_api: 是否自动缩放以节省 API 成本

        Returns:
            base64 编码的字符串
        """
        config = get_config().capture

        # 可选缩放
        if resize_for_api:
            img = self.resize_for_api(img)

        # 转换为字节
        buffer = io.BytesIO()
        if format.upper() == "JPEG":
            # 转换为 RGB（JPEG 不支持 alpha 通道）
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buffer, format="JPEG", quality=quality or config.compression_quality)
        else:
            img.save(buffer, format=format)

        # 编码为 base64
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def to_data_url(
        self,
        img: Image.Image,
        format: str = "JPEG",
        quality: int | None = None,
        resize_for_api: bool = True,
    ) -> str:
        """
        将图片转换为 data URL 格式

        适用于需要 data:image/... 格式的 API

        Args:
            img: PIL Image 对象
            format: 图片格式
            quality: JPEG 质量
            resize_for_api: 是否自动缩放

        Returns:
            data URL 字符串
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
        保存截图到文件

        Args:
            img: PIL Image 对象
            path: 保存路径
            format: 图片格式，None 则从路径推断
            quality: JPEG 质量

        Returns:
            保存的文件路径
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
        """清除截图缓存"""
        self._last_screenshot = None
        self._last_screenshot_time = 0
        self._last_screenshot_info = None

    def close(self) -> None:
        """释放资源"""
        if self._sct is not None:
            self._sct.close()
            self._sct = None
        self.clear_cache()

    def __enter__(self) -> "ScreenCapture":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# 全局实例
_capture: ScreenCapture | None = None


def get_capture() -> ScreenCapture:
    """获取全局截图实例"""
    global _capture
    if _capture is None:
        _capture = ScreenCapture()
    return _capture


def screenshot(
    monitor: int | None = None,
    region: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    """
    便捷函数：截取屏幕

    Args:
        monitor: 显示器索引
        region: 区域 (x, y, width, height)

    Returns:
        PIL Image 对象
    """
    return get_capture().capture(monitor=monitor, region=region)


def screenshot_base64(
    monitor: int | None = None,
    region: tuple[int, int, int, int] | None = None,
    resize: bool = True,
) -> str:
    """
    便捷函数：截取屏幕并返回 base64

    Args:
        monitor: 显示器索引
        region: 区域
        resize: 是否缩放以节省 API 成本

    Returns:
        base64 编码字符串
    """
    capture = get_capture()
    img = capture.capture(monitor=monitor, region=region)
    return capture.to_base64(img, resize_for_api=resize)
