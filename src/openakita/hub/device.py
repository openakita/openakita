"""
Device identity management.

Generates and persists a random UUID as a stable device identifier.
No hardware fingerprinting — simple, privacy-friendly, sufficient for dedup.
"""

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_DEVICE_FILE = "device.json"


def get_or_create_device_id(data_dir: Path) -> str:
    """Return the device_id, creating one on first run.

    The ID is a 16-character hex string persisted in ``data_dir/device.json``.
    """
    from openakita.utils.atomic_io import atomic_json_write, read_json_safe

    fp = data_dir / _DEVICE_FILE
    data = read_json_safe(fp)
    if isinstance(data, dict):
        did = data.get("device_id", "")
        if did:
            return did
        logger.warning("Corrupt device.json — regenerating device_id")

    did = uuid.uuid4().hex[:16]
    atomic_json_write(fp, {"device_id": did})
    logger.info("Generated new device_id: %s", did)
    return did
