#!/usr/bin/env python3
"""
单一版本源管理工具。

目标：
- 仓库根目录 `VERSION` 作为唯一版本来源（例如 1.2.5）
- 一键同步到：
  - pyproject.toml                ([project].version)
  - apps/setup-center/package.json (version)
  - apps/setup-center/src-tauri/tauri.conf.json (version)
  - apps/setup-center/src-tauri/Cargo.toml ([package].version)
  - apps/setup-center/src-tauri/Cargo.lock (openakita-setup-center package entry)
  - apps/setup-center/android/app/build.gradle (versionName + versionCode)
- CI/Release 可用 `check` 阻止漏改。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text_if_changed(path: Path, new_text: str) -> bool:
    old = _read_text(path)
    if old == new_text:
        return False
    path.write_text(new_text, encoding="utf-8", newline="\n")
    return True


_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[a-zA-Z0-9\.\-\+]+)?$")


def _validate_version(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("version 不能为空")
    if not _SEMVER_RE.match(v):
        raise ValueError(f"version 格式不合法: {v!r}（建议 1.2.3 / 1.2.3-rc.1）")
    return v


def read_version_file() -> str:
    return _validate_version((ROOT / "VERSION").read_text(encoding="utf-8"))


def write_version_file(v: str) -> bool:
    v = _validate_version(v)
    path = ROOT / "VERSION"
    old = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if old == v:
        return False
    path.write_text(v + "\n", encoding="utf-8", newline="\n")
    return True


def _update_pyproject(version: str) -> bool:
    path = ROOT / "pyproject.toml"
    text = _read_text(path)

    # 仅修改 [project] section 内的 version 行，避免误伤其他 tool 里的 version。
    m = re.search(r"(?ms)^\[project\]\s*\n(.*?)(?=^\[|\Z)", text)
    if not m:
        raise RuntimeError("pyproject.toml 找不到 [project] 段")
    section = m.group(0)
    sec_body = m.group(1)

    vm = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', section)
    if not vm:
        raise RuntimeError("pyproject.toml 的 [project] 段找不到 version = \"...\"")
    old_version = vm.group(1)
    new_section = re.sub(
        r'(?m)^version\s*=\s*"([^"]+)"\s*$',
        f'version = "{version}"',
        section,
        count=1,
    )
    if new_section == section:
        return False

    new_text = text[: m.start()] + new_section + text[m.end() :]
    changed = _write_text_if_changed(path, new_text)
    if changed:
        print(f"pyproject.toml: {old_version} -> {version}")
    return changed


def _update_json_version(path: Path, version: str) -> bool:
    data = json.loads(_read_text(path))
    old = data.get("version")
    if old == version:
        return False
    data["version"] = version
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    changed = _write_text_if_changed(path, new_text)
    if changed:
        print(f"{path.relative_to(ROOT)}: {old} -> {version}")
    return changed


def _update_cargo_toml(version: str) -> bool:
    path = ROOT / "apps/setup-center/src-tauri/Cargo.toml"
    text = _read_text(path)
    m = re.search(r"(?ms)^\[package\]\s*\n(.*?)(?=^\[|\Z)", text)
    if not m:
        raise RuntimeError("Cargo.toml 找不到 [package] 段")
    section = m.group(0)
    vm = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', section)
    if not vm:
        raise RuntimeError("Cargo.toml 的 [package] 段找不到 version")
    old = vm.group(1)
    new_section = re.sub(
        r'(?m)^version\s*=\s*"([^"]+)"\s*$',
        f'version = "{version}"',
        section,
        count=1,
    )
    if new_section == section:
        return False
    new_text = text[: m.start()] + new_section + text[m.end() :]
    changed = _write_text_if_changed(path, new_text)
    if changed:
        print(f"apps/setup-center/src-tauri/Cargo.toml: {old} -> {version}")
    return changed


def _update_cargo_lock(version: str) -> bool:
    path = ROOT / "apps/setup-center/src-tauri/Cargo.lock"
    lines = _read_text(path).splitlines(True)

    in_pkg = False
    is_target_pkg = False
    changed = False
    old_version = None

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if line.strip() == "[[package]]":
            in_pkg = True
            is_target_pkg = False
            old_version = None
            continue
        if in_pkg and line.startswith("name = "):
            name = line.split("=", 1)[1].strip().strip('"')
            is_target_pkg = name == "openakita-setup-center"
            continue
        if in_pkg and is_target_pkg and line.startswith("version = "):
            old_version = line.split("=", 1)[1].strip().strip('"')
            if old_version != version:
                lines[i] = f'version = "{version}"\n'
                changed = True
            # 不 break：让状态机继续走到下一个 [[package]]
            continue
        # package block ends implicitly when next [[package]] starts; handled above

    if changed:
        _write_text_if_changed(path, "".join(lines))
        print(f"apps/setup-center/src-tauri/Cargo.lock: {old_version} -> {version}")
    return changed


def _update_bundled_version(version: str) -> bool:
    """Update _bundled_version.txt with clean version (no git hash; hash is appended at build time)."""
    path = ROOT / "src" / "openakita" / "_bundled_version.txt"
    old = path.read_text(encoding="utf-8").strip() if path.exists() else ""
    if old == version:
        return False
    path.write_text(version, encoding="utf-8", newline="\n")
    print(f"src/openakita/_bundled_version.txt: {old} -> {version}")
    return True


def _version_to_code(version: str) -> int:
    """Convert semver to an integer versionCode: 1.25.8 → 12508, 2.0.0 → 20000."""
    parts = version.split("-")[0].split(".")
    major, minor, patch = (int(parts[i]) if i < len(parts) else 0 for i in range(3))
    return major * 10000 + minor * 100 + patch


def _update_android_gradle(version: str) -> bool:
    path = ROOT / "apps/setup-center/android/app/build.gradle"
    if not path.exists():
        return False
    text = _read_text(path)

    new_code = _version_to_code(version)

    # Update versionName
    old_name_m = re.search(r'(?m)(versionName\s+)"([^"]*)"', text)
    old_name = old_name_m.group(2) if old_name_m else None

    # Update versionCode — use semver-derived code, but never decrease
    old_code_m = re.search(r"(?m)(versionCode\s+)(\d+)", text)
    old_code = int(old_code_m.group(2)) if old_code_m else 0
    if new_code <= old_code:
        new_code = old_code + 1

    new_text = text
    if old_name_m:
        new_text = re.sub(
            r'(?m)(versionName\s+)"[^"]*"',
            f'\\1"{version}"',
            new_text,
            count=1,
        )
    if old_code_m:
        new_text = re.sub(
            r"(?m)(versionCode\s+)\d+",
            f"\\g<1>{new_code}",
            new_text,
            count=1,
        )

    if new_text == text:
        return False
    changed = _write_text_if_changed(path, new_text)
    if changed:
        print(
            f"apps/setup-center/android/app/build.gradle: "
            f"versionName {old_name!r} -> {version!r}, "
            f"versionCode {old_code} -> {new_code}"
        )
    return changed


def sync(version: str) -> int:
    version = _validate_version(version)
    changed_any = False

    changed_any |= _update_pyproject(version)
    changed_any |= _update_json_version(ROOT / "apps/setup-center/package.json", version)
    changed_any |= _update_json_version(ROOT / "apps/setup-center/src-tauri/tauri.conf.json", version)
    changed_any |= _update_cargo_toml(version)
    changed_any |= _update_cargo_lock(version)
    changed_any |= _update_bundled_version(version)
    changed_any |= _update_android_gradle(version)

    if not changed_any:
        print("OK: versions already in sync.")
    return 0


def check(expected: str | None) -> int:
    v = read_version_file()
    if expected is not None:
        expected = expected.lstrip("v").strip()
        expected = _validate_version(expected)
        if v != expected:
            print(f"ERROR: VERSION={v} 与期望版本 {expected} 不一致", file=sys.stderr)
            return 2

    # 只读对比：复用 sync 的更新器，但不写文件（用读取验证）
    mismatches: list[str] = []

    # pyproject
    py = _read_text(ROOT / "pyproject.toml")
    pm = re.search(r'(?ms)^\[project\]\s*\n.*?^version\s*=\s*"([^"]+)"\s*$', py)
    if not pm or pm.group(1) != v:
        mismatches.append("pyproject.toml")

    def _json_version(p: Path) -> str | None:
        try:
            return json.loads(_read_text(p)).get("version")
        except Exception:
            return None

    if _json_version(ROOT / "apps/setup-center/package.json") != v:
        mismatches.append("apps/setup-center/package.json")
    if _json_version(ROOT / "apps/setup-center/src-tauri/tauri.conf.json") != v:
        mismatches.append("apps/setup-center/src-tauri/tauri.conf.json")

    cargo_toml = _read_text(ROOT / "apps/setup-center/src-tauri/Cargo.toml")
    cm = re.search(r'(?ms)^\[package\]\s*\n.*?^version\s*=\s*"([^"]+)"\s*$', cargo_toml)
    if not cm or cm.group(1) != v:
        mismatches.append("apps/setup-center/src-tauri/Cargo.toml")

    cargo_lock = _read_text(ROOT / "apps/setup-center/src-tauri/Cargo.lock")
    # 找到 openakita-setup-center 这个 package 的 version
    lm = re.search(
        r'(?ms)^\[\[package\]\]\s*\nname\s*=\s*"openakita-setup-center"\s*\nversion\s*=\s*"([^"]+)"\s*$',
        cargo_lock,
    )
    if not lm or lm.group(1) != v:
        mismatches.append("apps/setup-center/src-tauri/Cargo.lock")

    # Android build.gradle
    gradle_path = ROOT / "apps/setup-center/android/app/build.gradle"
    if gradle_path.exists():
        gradle_text = _read_text(gradle_path)
        gm = re.search(r'(?m)versionName\s+"([^"]*)"', gradle_text)
        if not gm or gm.group(1) != v:
            mismatches.append("apps/setup-center/android/app/build.gradle")

    # Plugin SDK: version.py 和 pyproject.toml 必须一致（SDK 版本独立于主包）
    sdk_pyproject = ROOT / "openakita-plugin-sdk" / "pyproject.toml"
    sdk_version_py = ROOT / "openakita-plugin-sdk" / "src" / "openakita_plugin_sdk" / "version.py"
    if sdk_pyproject.exists() and sdk_version_py.exists():
        sdk_pp_text = _read_text(sdk_pyproject)
        spm = re.search(r'(?ms)^\[project\]\s*\n.*?^version\s*=\s*"([^"]+)"\s*$', sdk_pp_text)
        sdk_vpy_text = _read_text(sdk_version_py)
        svm = re.search(r'SDK_VERSION\s*=\s*"([^"]+)"', sdk_vpy_text)
        if spm and svm and spm.group(1) != svm.group(1):
            mismatches.append(
                f"openakita-plugin-sdk 版本不一致: "
                f"pyproject.toml={spm.group(1)} vs version.py SDK_VERSION={svm.group(1)}"
            )

    if mismatches:
        print(f"ERROR: 版本未统一到 VERSION={v}，不一致文件：", file=sys.stderr)
        for x in mismatches:
            print(f"- {x}", file=sys.stderr)
        print("\n请运行：python scripts/version.py sync", file=sys.stderr)
        return 3

    print(f"OK: versions in sync (VERSION={v}).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub_set = sub.add_parser("set", help="设置 VERSION 并同步所有文件")
    sub_set.add_argument("version")

    sub_sync = sub.add_parser("sync", help="从 VERSION 同步到所有文件")

    sub_check = sub.add_parser("check", help="校验所有文件版本是否与 VERSION 一致")
    sub_check.add_argument("--expected", default=None, help="可选：期望版本（支持 v1.2.3 或 1.2.3）")

    args = p.parse_args()

    if args.cmd == "set":
        write_version_file(args.version)
        return sync(read_version_file())
    if args.cmd == "sync":
        return sync(read_version_file())
    if args.cmd == "check":
        return check(args.expected)
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())


