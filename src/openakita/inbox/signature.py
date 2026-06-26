from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path


async def verify_minisign_signature(
    *,
    message: str,
    signature: str | None,
    public_key: str | None,
    minisign_executable: str = "minisign",
) -> bool:
    """Verify a minisign signature with an external minisign command.

    When no public key is configured, callers should treat verification as
    disabled instead of failed. This function only returns False when a public
    key is present and verification does not pass.
    """
    if not public_key:
        return True
    if not signature:
        return False
    return await asyncio.to_thread(
        _verify_minisign_sync,
        message,
        signature,
        public_key,
        minisign_executable,
    )


def _verify_minisign_sync(
    message: str,
    signature: str,
    public_key: str,
    minisign_executable: str,
) -> bool:
    import subprocess

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        msg_path = tmp_path / "broadcast.json"
        sig_path = tmp_path / "broadcast.json.minisig"
        msg_path.write_text(message, encoding="utf-8")
        sig_path.write_text(signature, encoding="utf-8")
        result = subprocess.run(
            [
                minisign_executable,
                "-V",
                "-P",
                public_key,
                "-m",
                str(msg_path),
                "-x",
                str(sig_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
