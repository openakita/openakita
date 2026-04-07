"""
模型下载源管理 - 多镜像源自动切换

解决问题: HuggingFace 在中国大陆下载速度极慢

支持的源:
- huggingface: HuggingFace Hub 官方 (海外推荐)
- hf-mirror:   HuggingFace 镜像 https://hf-mirror.com (国内推荐)
- modelscope:  ModelScope 魔搭社区 (国内备选)
- auto:        自动探测网络，选择最快的源 (默认)

使用方式:
    from openakita.memory.model_hub import load_embedding_model

    model = load_embedding_model(
        model_name="shibing624/text2vec-base-chinese",
        source="auto",
        device="cpu",
    )
"""

from __future__ import annotations

import logging
import os
import time
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 源定义
# ---------------------------------------------------------------------------

HF_MIRROR_ENDPOINT = "https://hf-mirror.com"


class ModelSource(StrEnum):
    AUTO = "auto"
    HUGGINGFACE = "huggingface"
    HF_MIRROR = "hf-mirror"
    MODELSCOPE = "modelscope"


# ModelScope 上部分模型的名称映射 (HF name -> ModelScope name)
# 大部分模型名称与 HuggingFace 一致，仅在不一致时才需要映射
_MODELSCOPE_NAME_MAP: dict[str, str] = {
    # 已知一致的不需要映射
    # 如有不一致的模型可在此添加: "hf_org/hf_model": "ms_org/ms_model"
}


def _modelscope_name(hf_name: str) -> str:
    """将 HuggingFace 模型名称映射为 ModelScope 名称"""
    return _MODELSCOPE_NAME_MAP.get(hf_name, hf_name)


# ---------------------------------------------------------------------------
# 网络探测
# ---------------------------------------------------------------------------


def _probe_url(url: str, timeout: float = 3.0) -> float:
    """
    测试 URL 连通性，返回响应时间（秒）；失败返回 inf。

    仅做 HEAD 请求或简单 GET 来衡量延迟。
    """
    import urllib.request

    try:
        start = time.monotonic()
        req = urllib.request.Request(url, method="HEAD")
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
        elapsed = time.monotonic() - start
        return elapsed
    except Exception:
        return float("inf")


def detect_best_source() -> ModelSource:
    """
    自动探测最佳下载源。

    策略（国内镜像优先）:
    0. 先检查系统 locale —— 中文环境直接用 hf-mirror，跳过网络探测
    1. 先探测 hf-mirror（国内镜像），如果 <2s 直接使用（大多数国内用户）
    2. 如果 hf-mirror 不通，再探测 huggingface.co
    3. 如果两者都很慢 (>3s)，优先尝试 ModelScope（如果已安装）
    4. 最终兜底: hf-mirror（国内大概率可用，即使探测超时也可能下载正常）
    """
    # 中文系统环境优先 hf-mirror，避免网络探测浪费时间
    import locale

    try:
        lang = locale.getlocale()[0] or os.environ.get("LANG", "")
        if lang and lang.lower().startswith("zh"):
            logger.info("[ModelHub] 检测到中文系统环境，优先使用 hf-mirror")
            return ModelSource.HF_MIRROR
    except Exception:
        pass

    # 快速探测：先测 hf-mirror，如果够快就直接用，省掉 huggingface.co 的探测时间
    mirror_time = _probe_url(HF_MIRROR_ENDPOINT, timeout=2.0)
    logger.info(
        f"[ModelHub] 探测 hf-mirror 延迟: "
        f"{'超时' if mirror_time == float('inf') else f'{mirror_time:.2f}s'}"
    )
    if mirror_time < 2.0:
        logger.info(f"[ModelHub] hf-mirror 响应良好 ({mirror_time:.2f}s)，直接使用")
        return ModelSource.HF_MIRROR

    # hf-mirror 不理想，再探测 huggingface.co
    hf_time = _probe_url("https://huggingface.co", timeout=2.0)
    logger.info(
        f"[ModelHub] 探测 huggingface 延迟: "
        f"{'超时' if hf_time == float('inf') else f'{hf_time:.2f}s'}"
    )

    # 选最快的
    if hf_time < mirror_time and hf_time < 2.0:
        logger.info(f"[ModelHub] 自动选择下载源: huggingface ({hf_time:.2f}s)")
        return ModelSource.HUGGINGFACE

    # 如果两个都比较慢，检查 ModelScope
    best_time = min(mirror_time, hf_time)
    if best_time > 3.0:
        try:
            import modelscope  # noqa: F401

            logger.info("[ModelHub] HF 源均较慢，ModelScope 可用 → 使用 ModelScope")
            return ModelSource.MODELSCOPE
        except ImportError:
            pass

    # 兜底 hf-mirror（即使探测超时，实际下载可能正常，比 huggingface.co 可靠性高）
    if best_time == float("inf"):
        logger.warning("[ModelHub] 所有源均超时，兜底使用 hf-mirror")
        return ModelSource.HF_MIRROR

    # mirror 虽然不是最快但可用
    if mirror_time < float("inf"):
        logger.info(f"[ModelHub] 自动选择下载源: hf-mirror ({mirror_time:.2f}s)")
        return ModelSource.HF_MIRROR

    logger.info(f"[ModelHub] 自动选择下载源: huggingface ({hf_time:.2f}s)")
    return ModelSource.HUGGINGFACE


# ---------------------------------------------------------------------------
# 源配置
# ---------------------------------------------------------------------------


def _apply_source_env(source: ModelSource) -> None:
    """根据选择的源设置环境变量，并同步 huggingface_hub 内部缓存。

    huggingface_hub 在模块导入时把 os.environ["HF_ENDPOINT"] 缓存到
    huggingface_hub.constants.HF_ENDPOINT（模块级常量），后续修改 os.environ
    不会影响这个缓存。因此这里需要同时 patch 两处。
    """
    if source == ModelSource.HF_MIRROR:
        os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
        _sync_hf_hub_endpoint(HF_MIRROR_ENDPOINT)
        logger.info(f"[ModelHub] 设置 HF_ENDPOINT={HF_MIRROR_ENDPOINT}")
    elif source == ModelSource.HUGGINGFACE:
        # 移除可能残留的镜像端点
        os.environ.pop("HF_ENDPOINT", None)
        _sync_hf_hub_endpoint("https://huggingface.co")
    elif source == ModelSource.MODELSCOPE:
        # ModelScope 使用自有 CDN，清理残留的 HF_ENDPOINT 以避免干扰
        os.environ.pop("HF_ENDPOINT", None)
        _sync_hf_hub_endpoint("https://huggingface.co")

    # 设置 huggingface_hub 的下载超时（默认 10s 连接超时太短，镜像源可能慢）
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")


def _sync_hf_hub_endpoint(endpoint: str) -> None:
    """同步 huggingface_hub 内部已缓存的 endpoint 常量。

    huggingface_hub 在模块导入时将 HF_ENDPOINT 环境变量缓存到模块常量，
    后续修改 os.environ 不会影响缓存值。必须直接 patch 模块属性。

    不同版本的属性名不同:
    - >=0.25: constants.ENDPOINT (不带 HF_ 前缀)
    - 旧版本: constants.HF_ENDPOINT
    """
    import sys

    hub_mod = sys.modules.get("huggingface_hub")
    if hub_mod is None:
        return

    constants = getattr(hub_mod, "constants", None)
    if constants is not None:
        for attr in ("ENDPOINT", "HF_ENDPOINT"):
            if hasattr(constants, attr):
                setattr(constants, attr, endpoint)
                logger.debug(f"[ModelHub] 已同步 huggingface_hub.constants.{attr}={endpoint}")

    for attr in ("ENDPOINT", "HF_ENDPOINT"):
        if hasattr(hub_mod, attr):
            setattr(hub_mod, attr, endpoint)


def _resolve_source(source: str | ModelSource) -> ModelSource:
    """将用户输入的源字符串解析为 ModelSource 枚举"""
    if isinstance(source, ModelSource):
        return source
    try:
        return ModelSource(source.lower().strip())
    except ValueError:
        logger.warning(f"[ModelHub] 未知下载源 '{source}'，使用 auto")
        return ModelSource.AUTO


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------


def _load_from_modelscope(model_name: str, device: str = "cpu"):
    """通过 ModelScope 下载模型，再用 SentenceTransformer 加载"""
    from sentence_transformers import SentenceTransformer

    ms_name = _modelscope_name(model_name)

    try:
        from modelscope import snapshot_download

        logger.info(f"[ModelHub] 从 ModelScope 下载模型: {ms_name}")
        local_path = snapshot_download(ms_name)
        logger.info(f"[ModelHub] 模型已下载到: {local_path}")
        return SentenceTransformer(str(local_path), device=device)

    except ImportError:
        logger.warning(
            "[ModelHub] ⚠ modelscope 包未安装，无法使用 ModelScope 下载。"
            "正在回退到 hf-mirror (国内镜像)。"
            "如需使用 ModelScope，请安装: pip install modelscope"
        )
        _apply_source_env(ModelSource.HF_MIRROR)
        return SentenceTransformer(model_name, device=device)

    except Exception as e:
        logger.warning(f"[ModelHub] ⚠ ModelScope 下载失败 ({e})，回退到 hf-mirror (国内镜像)")
        _apply_source_env(ModelSource.HF_MIRROR)
        return SentenceTransformer(model_name, device=device)


def _load_from_hf(model_name: str, device: str = "cpu"):
    """通过 HuggingFace (含镜像) 加载模型，失败时自动尝试备选源

    回退顺序:
    1. 当前配置的源（可能是 huggingface 或 hf-mirror）
    2. hf-mirror（如果当前不是 hf-mirror）
    3. ModelScope（最后手段）
    """
    from sentence_transformers import SentenceTransformer

    current_endpoint = os.environ.get("HF_ENDPOINT", "")
    logger.debug(f"[ModelHub] _load_from_hf: HF_ENDPOINT={current_endpoint or '(未设置)'}")

    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as e:
        logger.warning(f"[ModelHub] 当前源下载失败: {e}")

        # 如果当前用的不是 hf-mirror，先尝试切换到国内镜像
        if HF_MIRROR_ENDPOINT not in current_endpoint:
            logger.info("[ModelHub] 切换到 hf-mirror (国内镜像) 重试...")
            _apply_source_env(ModelSource.HF_MIRROR)
            try:
                return SentenceTransformer(model_name, device=device)
            except Exception as e2:
                logger.warning(f"[ModelHub] hf-mirror 也失败了: {e2}")

        # 最后尝试 ModelScope
        try:
            logger.info("[ModelHub] 尝试 ModelScope 作为最后手段...")
            return _load_from_modelscope(model_name, device)
        except Exception:
            # 所有源都失败，抛出原始异常
            raise e


def load_embedding_model(
    model_name: str,
    source: str | ModelSource = "auto",
    device: str = "cpu",
    max_retries: int = 2,
    initial_backoff: float = 3.0,
):
    """
    加载 embedding 模型，支持多源自动切换 + 整体重试 + 指数退避。

    重试策略（三层防御）:
      Layer 1: huggingface_hub 内部重试（5 次，1-2-4s 退避）
      Layer 2: 源级别回退（当前源 → hf-mirror → modelscope）
      Layer 3: 本函数的整体重试（默认 2 轮，3-6s 退避）

    注意：本函数在后台线程中运行（由 VectorStore 调用），
    不会阻塞后端启动。失败后由 VectorStore 的冷却重试机制接管。

    Args:
        model_name: 模型名称 (如 "shibing624/text2vec-base-chinese")
        source: 下载源 ("auto" | "huggingface" | "hf-mirror" | "modelscope")
        device: 运行设备 ("cpu" | "cuda")
        max_retries: 整体重试次数 (默认 2)
        initial_backoff: 首次重试等待秒数 (默认 3s，后续指数增长)

    Returns:
        SentenceTransformer 模型实例

    Raises:
        ImportError: sentence-transformers 未安装
        Exception: 所有重试均失败
    """
    resolved = _resolve_source(source)

    # auto 模式：先探测最佳源
    if resolved == ModelSource.AUTO:
        # 如果模型已经在本地缓存，离线加载（避免向 huggingface.co 发 HEAD 请求）
        if _is_model_cached(model_name):
            logger.info(f"[ModelHub] 模型已缓存，离线加载: {model_name}")
            from sentence_transformers import SentenceTransformer

            old_offline = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                model = SentenceTransformer(model_name, device=device)
                return model
            except Exception as e:
                logger.warning(f"[ModelHub] 离线加载缓存失败 ({e})，将重新下载")
            finally:
                if old_offline is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = old_offline
            # Fall through: 缓存可能损坏，走正常源探测 + 下载流程

        resolved = detect_best_source()
        logger.info(f"[ModelHub] auto 模式选择了: {resolved.value}")

    # 配置环境变量
    _apply_source_env(resolved)

    # ── 整体重试循环（Layer 3）──
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[ModelHub] 加载模型 '{model_name}' "
                f"(源={resolved.value}, 设备={device}, "
                f"尝试 {attempt}/{max_retries})"
            )

            if resolved == ModelSource.MODELSCOPE:
                return _load_from_modelscope(model_name, device)
            else:
                return _load_from_hf(model_name, device)

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                backoff = initial_backoff * (2 ** (attempt - 1))
                logger.warning(f"[ModelHub] ⚠ 模型加载失败 (尝试 {attempt}/{max_retries}): {e}")
                logger.info(
                    f"[ModelHub] 将在 {backoff:.0f}s 后重试... (剩余 {max_retries - attempt} 次)"
                )
                time.sleep(backoff)
                # 重试前重新配置环境（可能因回退被改过）
                _apply_source_env(resolved)
            else:
                logger.error(f"[ModelHub] ✗ 模型加载最终失败 (已重试 {max_retries} 次): {e}")
                logger.error(
                    "[ModelHub] 排查建议: "
                    "① 检查网络连接 "
                    "② 在设置中心切换模型下载源 "
                    "③ 手动下载模型到 ~/.cache/huggingface/hub/"
                )

    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 缓存检测
# ---------------------------------------------------------------------------


def _is_model_cached(model_name: str) -> bool:
    """
    检测模型是否已在本地缓存（避免不必要的网络探测）。

    检查 HuggingFace Hub 默认缓存目录:
    - Linux/macOS: ~/.cache/huggingface/hub/
    - Windows: C:\\Users\\<user>\\.cache\\huggingface\\hub\\
    """
    try:
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        # HF Hub 的缓存目录格式: models--<org>--<model>
        safe_name = "models--" + model_name.replace("/", "--")
        model_cache = cache_dir / safe_name

        if model_cache.exists() and any(model_cache.iterdir()):
            return True

        # 也检查 HF_HOME 自定义目录
        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            custom_cache = Path(hf_home) / "hub" / safe_name
            if custom_cache.exists() and any(custom_cache.iterdir()):
                return True

        return False
    except Exception:
        return False
