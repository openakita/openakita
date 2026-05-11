from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENAKITA = ROOT / ".venv" / "Scripts" / "openakita.exe"
URL = "http://127.0.0.1:18900/web/"
HEALTH_URL = "http://127.0.0.1:18900/api/health"


def is_port_open(host: str = "127.0.0.1", port: int = 18900) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def is_health_ready() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=1.5) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def main() -> int:
    os.chdir(ROOT)

    if not OPENAKITA.exists():
        print(f"[ERROR] Missing {OPENAKITA}")
        print("Please install OpenAkita in this project folder first.")
        input("Press Enter to exit...")
        return 1

    print("========================================")
    print("OpenAkita Web GUI launcher")
    print(f"Project: {ROOT}")
    print(f"GUI: {URL}")
    print("========================================")
    print()

    process: subprocess.Popen[str] | None = None
    if is_port_open():
        print("Backend port 18900 is already open; opening GUI directly.")
    else:
        print("Starting OpenAkita backend service...")
        process = subprocess.Popen(
            [str(OPENAKITA), "serve"],
            cwd=str(ROOT),
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )

    print("Waiting for backend: http://127.0.0.1:18900/api/health")
    ready = False
    for _ in range(90):
        if is_health_ready():
            ready = True
            break
        if process is not None and process.poll() is not None:
            print(f"[ERROR] Backend exited early with code {process.returncode}")
            input("Press Enter to exit...")
            return process.returncode or 1
        time.sleep(1)

    if not ready:
        print("[ERROR] Backend did not become ready within 90 seconds.")
        print("Check the terminal output above for errors.")
        input("Press Enter to exit...")
        return 1

    print(f"Opening GUI: {URL}")
    webbrowser.open(URL, new=2)
    print()
    print("Keep this window open while using OpenAkita.")
    print("Close this window or press Ctrl+C to stop the backend service.")

    if process is None:
        input("Press Enter to exit this launcher window...")
        return 0

    try:
        return process.wait()
    except KeyboardInterrupt:
        print("Stopping OpenAkita backend...")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
