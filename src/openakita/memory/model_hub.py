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
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 源定义
# ---------------------------------------------------------------------------

HF_MIRROR_ENDPOINT = "https://hf-mirror.com"


class ModelSource(str, Enum):
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


def _probe_url(url: str, timeout: float = 5.0) -> float:
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

    策略:
    1. 并行测试 huggingface.co 和 hf-mirror.com 的延迟
    2. 选择延迟最低的
    3. 如果两者都很慢 (>5s)，优先尝试 ModelScope（如果已安装）
    4. 最终兜底: hf-mirror（国内大概率可用）
    """
    import concurrent.futures

    probes = {
        ModelSource.HUGGINGFACE: "https://huggingface.co",
        ModelSource.HF_MIRROR: HF_MIRROR_ENDPOINT,
    }

    results: dict[ModelSource, float] = {}

    # 并行探测，减少等待时间
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(_probe_url, url, 5.0): source
            for source, url in probes.items()
        }
        for future in concurrent.futures.as_completed(futures):
            source = futures[future]
            elapsed = future.result()
            results[source] = elapsed
            logger.info(
                f"[ModelHub] 探测 {source.value} 延迟: "
                f"{'超时' if elapsed == float('inf') else f'{elapsed:.2f}s'}"
            )

    # 选最快的
    best_source = min(results, key=results.get)  # type: ignore[arg-type]
    best_time = results[best_source]

    # 如果两个 HF 源都很慢，检查 ModelScope 是否可用
    if best_time > 5.0:
        try:
            import modelscope  # noqa: F401

            logger.info("[ModelHub] HF 源均较慢，ModelScope 可用 → 使用 ModelScope")
            return ModelSource.MODELSCOPE
        except ImportError:
            pass

    # 如果都超时，兜底 hf-mirror（国内概率最高）
    if best_time == float("inf"):
        logger.warning("[ModelHub] 所有源均超时，兜底使用 hf-mirror")
        return ModelSource.HF_MIRROR

    logger.info(f"[ModelHub] 自动选择下载源: {best_source.value} ({best_time:.2f}s)")
    return best_source


# ---------------------------------------------------------------------------
# 源配置
# ---------------------------------------------------------------------------


def _apply_source_env(source: ModelSource) -> None:
    """根据选择的源设置环境变量"""
    if source == ModelSource.HF_MIRROR:
        os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
        logger.info(f"[ModelHub] 设置 HF_ENDPOINT={HF_MIRROR_ENDPOINT}")
    elif source == ModelSource.HUGGINGFACE:
        # 移除可能残留的镜像端点
        if os.environ.get("HF_ENDPOINT"):
            del os.environ["HF_ENDPOINT"]
    # ModelScope 不需要设置 HF_ENDPOINT


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
            "[ModelHub] modelscope 未安装，回退到 hf-mirror。"
            "可通过 pip install modelscope 安装。"
        )
        _apply_source_env(ModelSource.HF_MIRROR)
        return SentenceTransformer(model_name, device=device)

    except Exception as e:
        logger.warning(f"[ModelHub] ModelScope 下载失败 ({e})，回退到 hf-mirror")
        _apply_source_env(ModelSource.HF_MIRROR)
        return SentenceTransformer(model_name, device=device)


def _load_from_hf(model_name: str, device: str = "cpu"):
    """通过 HuggingFace (含镜像) 加载模型，失败时自动尝试备选源"""
    from sentence_transformers import SentenceTransformer

    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as e:
        current_endpoint = os.environ.get("HF_ENDPOINT", "")

        # 如果用的是官方源，尝试镜像
        if HF_MIRROR_ENDPOINT not in current_endpoint:
            logger.warning(
                f"[ModelHub] HuggingFace 官方下载失败 ({e})，尝试 hf-mirror..."
            )
            _apply_source_env(ModelSource.HF_MIRROR)
            try:
                return SentenceTransformer(model_name, device=device)
            except Exception as e2:
                logger.warning(f"[ModelHub] hf-mirror 也失败了 ({e2})")

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
):
    """
    加载 embedding 模型，支持多源自动切换。

    Args:
        model_name: 模型名称 (如 "shibing624/text2vec-base-chinese")
        source: 下载源 ("auto" | "huggingface" | "hf-mirror" | "modelscope")
        device: 运行设备 ("cpu" | "cuda")

    Returns:
        SentenceTransformer 模型实例

    Raises:
        ImportError: sentence-transformers 未安装
        Exception: 所有下载源均失败
    """
    resolved = _resolve_source(source)

    # auto 模式：先探测最佳源
    if resolved == ModelSource.AUTO:
        # 如果模型已经在本地缓存，直接加载不需要探测
        if _is_model_cached(model_name):
            logger.info(f"[ModelHub] 模型已缓存，直接加载: {model_name}")
            from sentence_transformers import SentenceTransformer

            return SentenceTransformer(model_name, device=device)

        resolved = detect_best_source()
        logger.info(f"[ModelHub] auto 模式选择了: {resolved.value}")

    # 配置环境变量
    _apply_source_env(resolved)

    logger.info(
        f"[ModelHub] 加载模型 '{model_name}' (源={resolved.value}, 设备={device})"
    )

    # 根据源加载
    if resolved == ModelSource.MODELSCOPE:
        return _load_from_modelscope(model_name, device)
    else:
        return _load_from_hf(model_name, device)


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
