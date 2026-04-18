"""
Model download source manager - automatic fallback across multiple mirrors

Problem solved: HuggingFace is extremely slow from within mainland China

Supported sources:
- huggingface: Official HuggingFace Hub (recommended outside China)
- hf-mirror:   HuggingFace mirror https://hf-mirror.com (recommended within China)
- modelscope:  ModelScope community (alternative within China)
- auto:        Automatically probe the network and pick the fastest source (default)

Usage:
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
# Source definitions
# ---------------------------------------------------------------------------

HF_MIRROR_ENDPOINT = "https://hf-mirror.com"


class ModelSource(StrEnum):
    AUTO = "auto"
    HUGGINGFACE = "huggingface"
    HF_MIRROR = "hf-mirror"
    MODELSCOPE = "modelscope"


# Name mapping for some models on ModelScope (HF name -> ModelScope name)
# Most model names match HuggingFace; only mismatched ones need to be mapped here
_MODELSCOPE_NAME_MAP: dict[str, str] = {
    # Models already consistent do not need mapping
    # For mismatched models, add: "hf_org/hf_model": "ms_org/ms_model"
}


def _modelscope_name(hf_name: str) -> str:
    """Map a HuggingFace model name to its ModelScope name"""
    return _MODELSCOPE_NAME_MAP.get(hf_name, hf_name)


# ---------------------------------------------------------------------------
# Network probing
# ---------------------------------------------------------------------------


def _probe_url(url: str, timeout: float = 3.0) -> float:
    """
    Test URL reachability; returns response time in seconds, or inf on failure.

    Uses only a HEAD request (or simple GET) to measure latency.
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
    Automatically detect the best download source.

    Strategy (prefer China-side mirrors):
    0. First check system locale — for Chinese environments go straight to hf-mirror, skipping network probing
    1. First probe hf-mirror (China-side mirror); if <2s use it directly (covers most users in China)
    2. If hf-mirror is unreachable, probe huggingface.co
    3. If both are slow (>3s), try ModelScope first (if installed)
    4. Final fallback: hf-mirror (likely to work in China; downloads may succeed even if the probe times out)
    """
    # Prefer hf-mirror in Chinese system environments to avoid wasting time on network probing
    import locale

    try:
        lang = locale.getlocale()[0] or os.environ.get("LANG", "")
        if lang and lang.lower().startswith("zh"):
            logger.info("[ModelHub] Detected Chinese system environment; preferring hf-mirror")
            return ModelSource.HF_MIRROR
    except Exception:
        pass

    # Fast probe: test hf-mirror first, and if fast enough use it directly, saving huggingface.co probe time
    mirror_time = _probe_url(HF_MIRROR_ENDPOINT, timeout=2.0)
    logger.info(
        f"[ModelHub] Probed hf-mirror latency: "
        f"{'timeout' if mirror_time == float('inf') else f'{mirror_time:.2f}s'}"
    )
    if mirror_time < 2.0:
        logger.info(f"[ModelHub] hf-mirror responded well ({mirror_time:.2f}s); using it directly")
        return ModelSource.HF_MIRROR

    # hf-mirror is not ideal; probe huggingface.co as well
    hf_time = _probe_url("https://huggingface.co", timeout=2.0)
    logger.info(
        f"[ModelHub] Probed huggingface latency: "
        f"{'timeout' if hf_time == float('inf') else f'{hf_time:.2f}s'}"
    )

    # Pick the fastest
    if hf_time < mirror_time and hf_time < 2.0:
        logger.info(f"[ModelHub] Auto-selected download source: huggingface ({hf_time:.2f}s)")
        return ModelSource.HUGGINGFACE

    # If both are slow, check ModelScope
    best_time = min(mirror_time, hf_time)
    if best_time > 3.0:
        try:
            import modelscope  # noqa: F401

            logger.info("[ModelHub] HF sources are slow, ModelScope is available -> using ModelScope")
            return ModelSource.MODELSCOPE
        except ImportError:
            pass

    # Fallback to hf-mirror (even if probing timed out, actual downloads may work; more reliable than huggingface.co)
    if best_time == float("inf"):
        logger.warning("[ModelHub] All sources timed out; falling back to hf-mirror")
        return ModelSource.HF_MIRROR

    # Mirror wasn't fastest but is usable
    if mirror_time < float("inf"):
        logger.info(f"[ModelHub] Auto-selected download source: hf-mirror ({mirror_time:.2f}s)")
        return ModelSource.HF_MIRROR

    logger.info(f"[ModelHub] Auto-selected download source: huggingface ({hf_time:.2f}s)")
    return ModelSource.HUGGINGFACE


# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------


def _apply_source_env(source: ModelSource) -> None:
    """Set environment variables for the chosen source and sync huggingface_hub's internal cache.

    When huggingface_hub is imported, it caches os.environ["HF_ENDPOINT"] into
    huggingface_hub.constants.HF_ENDPOINT (a module-level constant); later modifications
    to os.environ do not affect that cache. Both locations must therefore be patched.
    """
    if source == ModelSource.HF_MIRROR:
        os.environ["HF_ENDPOINT"] = HF_MIRROR_ENDPOINT
        _sync_hf_hub_endpoint(HF_MIRROR_ENDPOINT)
        logger.info(f"[ModelHub] Set HF_ENDPOINT={HF_MIRROR_ENDPOINT}")
    elif source == ModelSource.HUGGINGFACE:
        # Remove any leftover mirror endpoint
        os.environ.pop("HF_ENDPOINT", None)
        _sync_hf_hub_endpoint("https://huggingface.co")
    elif source == ModelSource.MODELSCOPE:
        # ModelScope uses its own CDN; clear any leftover HF_ENDPOINT to avoid interference
        os.environ.pop("HF_ENDPOINT", None)
        _sync_hf_hub_endpoint("https://huggingface.co")

    # Set huggingface_hub's download timeout (the default 10s connect timeout is too short; mirrors can be slow)
    os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")


def _sync_hf_hub_endpoint(endpoint: str) -> None:
    """Sync the endpoint constant already cached inside huggingface_hub.

    huggingface_hub caches the HF_ENDPOINT environment variable into a module constant at import time;
    subsequent modifications to os.environ do not affect the cached value. Module attributes must be patched directly.

    The attribute name varies by version:
    - >=0.25: constants.ENDPOINT (without the HF_ prefix)
    - older:  constants.HF_ENDPOINT
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
                logger.debug(f"[ModelHub] Synced huggingface_hub.constants.{attr}={endpoint}")

    for attr in ("ENDPOINT", "HF_ENDPOINT"):
        if hasattr(hub_mod, attr):
            setattr(hub_mod, attr, endpoint)


def _resolve_source(source: str | ModelSource) -> ModelSource:
    """Resolve a user-provided source string to a ModelSource enum value"""
    if isinstance(source, ModelSource):
        return source
    try:
        return ModelSource(source.lower().strip())
    except ValueError:
        logger.warning(f"[ModelHub] Unknown download source '{source}', falling back to auto")
        return ModelSource.AUTO


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------


def _load_from_modelscope(model_name: str, device: str = "cpu"):
    """Download the model via ModelScope, then load it with SentenceTransformer"""
    from sentence_transformers import SentenceTransformer

    ms_name = _modelscope_name(model_name)

    try:
        from modelscope import snapshot_download

        logger.info(f"[ModelHub] Downloading model from ModelScope: {ms_name}")
        local_path = snapshot_download(ms_name)
        logger.info(f"[ModelHub] Model downloaded to: {local_path}")
        return SentenceTransformer(str(local_path), device=device)

    except ImportError:
        logger.warning(
            "[ModelHub] ⚠ modelscope package is not installed; cannot download via ModelScope. "
            "Falling back to hf-mirror (China-side mirror). "
            "To use ModelScope, install it with: pip install modelscope"
        )
        _apply_source_env(ModelSource.HF_MIRROR)
        return SentenceTransformer(model_name, device=device)

    except Exception as e:
        logger.warning(f"[ModelHub] ⚠ ModelScope download failed ({e}); falling back to hf-mirror (China-side mirror)")
        _apply_source_env(ModelSource.HF_MIRROR)
        return SentenceTransformer(model_name, device=device)


def _load_from_hf(model_name: str, device: str = "cpu"):
    """Load the model via HuggingFace (including mirrors); on failure, automatically try alternative sources.

    Fallback order:
    1. Currently configured source (possibly huggingface or hf-mirror)
    2. hf-mirror (if it's not already the current source)
    3. ModelScope (last resort)
    """
    from sentence_transformers import SentenceTransformer

    current_endpoint = os.environ.get("HF_ENDPOINT", "")
    logger.debug(f"[ModelHub] _load_from_hf: HF_ENDPOINT={current_endpoint or '(not set)'}")

    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as e:
        logger.warning(f"[ModelHub] Current source download failed: {e}")

        # If current source isn't hf-mirror, try switching to the China-side mirror first
        if HF_MIRROR_ENDPOINT not in current_endpoint:
            logger.info("[ModelHub] Switching to hf-mirror (China-side mirror) and retrying...")
            _apply_source_env(ModelSource.HF_MIRROR)
            try:
                return SentenceTransformer(model_name, device=device)
            except Exception as e2:
                logger.warning(f"[ModelHub] hf-mirror also failed: {e2}")

        # Finally, try ModelScope
        try:
            logger.info("[ModelHub] Trying ModelScope as a last resort...")
            return _load_from_modelscope(model_name, device)
        except Exception:
            # All sources failed; raise the original exception
            raise e


def load_embedding_model(
    model_name: str,
    source: str | ModelSource = "auto",
    device: str = "cpu",
    max_retries: int = 2,
    initial_backoff: float = 3.0,
):
    """
    Load an embedding model with automatic multi-source fallback, overall retry, and exponential backoff.

    Retry strategy (three layers of defense):
      Layer 1: huggingface_hub's internal retry (5 times, 1-2-4s backoff)
      Layer 2: source-level fallback (current source -> hf-mirror -> modelscope)
      Layer 3: this function's overall retry (default 2 rounds, 3-6s backoff)

    Note: this function runs on a background thread (called by VectorStore),
    so it does not block backend startup. On failure, VectorStore's cooldown/retry mechanism takes over.

    Args:
        model_name: model name (e.g. "shibing624/text2vec-base-chinese")
        source: download source ("auto" | "huggingface" | "hf-mirror" | "modelscope")
        device: runtime device ("cpu" | "cuda")
        max_retries: overall retry count (default 2)
        initial_backoff: seconds to wait before the first retry (default 3s; grows exponentially)

    Returns:
        A SentenceTransformer model instance

    Raises:
        ImportError: sentence-transformers is not installed
        Exception: all retries failed
    """
    resolved = _resolve_source(source)

    # auto mode: detect the best source first
    if resolved == ModelSource.AUTO:
        # If the model is already cached locally, load offline (avoids sending a HEAD request to huggingface.co)
        if _is_model_cached(model_name):
            logger.info(f"[ModelHub] Model is cached; loading offline: {model_name}")
            from sentence_transformers import SentenceTransformer

            old_offline = os.environ.get("HF_HUB_OFFLINE")
            os.environ["HF_HUB_OFFLINE"] = "1"
            try:
                model = SentenceTransformer(model_name, device=device)
                return model
            except Exception as e:
                logger.warning(f"[ModelHub] Offline cache load failed ({e}); will re-download")
            finally:
                if old_offline is None:
                    os.environ.pop("HF_HUB_OFFLINE", None)
                else:
                    os.environ["HF_HUB_OFFLINE"] = old_offline
            # Fall through: cache may be corrupt, proceed with normal source-detection + download flow

        resolved = detect_best_source()
        logger.info(f"[ModelHub] auto mode selected: {resolved.value}")

    # Configure environment variables
    _apply_source_env(resolved)

    # -- Overall retry loop (Layer 3) --
    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(
                f"[ModelHub] Loading model '{model_name}' "
                f"(source={resolved.value}, device={device}, "
                f"attempt {attempt}/{max_retries})"
            )

            if resolved == ModelSource.MODELSCOPE:
                return _load_from_modelscope(model_name, device)
            else:
                return _load_from_hf(model_name, device)

        except Exception as e:
            last_error = e
            if attempt < max_retries:
                backoff = initial_backoff * (2 ** (attempt - 1))
                logger.warning(f"[ModelHub] ⚠ Model load failed (attempt {attempt}/{max_retries}): {e}")
                logger.info(
                    f"[ModelHub] Retrying in {backoff:.0f}s... ({max_retries - attempt} retries left)"
                )
                time.sleep(backoff)
                # Reconfigure the environment before retry (it may have been changed by a fallback)
                _apply_source_env(resolved)
            else:
                logger.error(f"[ModelHub] ✗ Model load ultimately failed (after {max_retries} retries): {e}")
                logger.error(
                    "[ModelHub] Troubleshooting tips: "
                    "(1) check network connectivity "
                    "(2) switch the model download source in the settings center "
                    "(3) manually download the model to ~/.cache/huggingface/hub/"
                )

    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Cache detection
# ---------------------------------------------------------------------------


def _is_model_cached(model_name: str) -> bool:
    """
    Check whether the model is already cached locally (to avoid unnecessary network probing).

    Checks the default HuggingFace Hub cache directory:
    - Linux/macOS: ~/.cache/huggingface/hub/
    - Windows: C:\\Users\\<user>\\.cache\\huggingface\\hub\\
    """
    try:
        cache_dir = Path.home() / ".cache" / "huggingface" / "hub"
        # HF Hub cache directory format: models--<org>--<model>
        safe_name = "models--" + model_name.replace("/", "--")
        model_cache = cache_dir / safe_name

        if model_cache.exists() and any(model_cache.iterdir()):
            return True

        # Also check the custom HF_HOME directory
        hf_home = os.environ.get("HF_HOME")
        if hf_home:
            custom_cache = Path(hf_home) / "hub" / safe_name
            if custom_cache.exists() and any(custom_cache.iterdir()):
                return True

        return False
    except Exception:
        return False
