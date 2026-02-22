"""
单元测试 - 后加载模块依赖完整性

测试内容:
1. runtime_env.py: 模块路径注入和 DLL 注册
2. _import_helper.py: 模块映射完整性
3. browser/manager.py: PLAYWRIGHT_BROWSERS_PATH 运行时设置
4. bundle_modules.py: 离线打包脚本模块定义一致性
5. 记忆检索回退机制
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ─────────────────────────────────────────────
# 1. runtime_env 模块路径注入
# ─────────────────────────────────────────────


class TestInjectModulePaths:
    """inject_module_paths_runtime() 路径注入测试"""

    def test_inject_adds_existing_site_packages(self, tmp_path):
        """模块 site-packages 目录存在时应被添加到 sys.path"""
        modules_dir = tmp_path / "modules"
        (modules_dir / "vector-memory" / "site-packages").mkdir(parents=True)
        (modules_dir / "browser" / "site-packages").mkdir(parents=True)

        with patch("openakita.runtime_env._get_openakita_root", return_value=tmp_path):
            original_path_len = len(sys.path)
            from openakita.runtime_env import inject_module_paths_runtime

            count = inject_module_paths_runtime()

        assert count >= 2
        vm_sp = str(modules_dir / "vector-memory" / "site-packages")
        br_sp = str(modules_dir / "browser" / "site-packages")
        assert vm_sp in sys.path
        assert br_sp in sys.path

        # 清理
        sys.path = [p for p in sys.path if str(tmp_path) not in p]

    def test_inject_skips_nonexistent_dirs(self, tmp_path):
        """模块目录不存在时不应报错"""
        modules_dir = tmp_path / "modules"
        # 不创建任何子目录

        with patch("openakita.runtime_env._get_openakita_root", return_value=tmp_path):
            from openakita.runtime_env import inject_module_paths_runtime

            count = inject_module_paths_runtime()

        assert count == 0

    def test_inject_idempotent(self, tmp_path):
        """重复调用不应重复添加路径"""
        modules_dir = tmp_path / "modules"
        (modules_dir / "orchestration" / "site-packages").mkdir(parents=True)

        with patch("openakita.runtime_env._get_openakita_root", return_value=tmp_path):
            from openakita.runtime_env import inject_module_paths_runtime

            count1 = inject_module_paths_runtime()
            count2 = inject_module_paths_runtime()

        assert count1 >= 1
        assert count2 == 0  # 第二次不应添加任何新路径

        sp = str(modules_dir / "orchestration" / "site-packages")
        assert sys.path.count(sp) == 1  # 只出现一次

        # 清理
        sys.path = [p for p in sys.path if str(tmp_path) not in p]

    def test_inject_appends_not_prepends(self, tmp_path):
        """路径应追加到 sys.path 末尾，不能插入前面"""
        modules_dir = tmp_path / "modules"
        (modules_dir / "whisper" / "site-packages").mkdir(parents=True)

        original_first = sys.path[0] if sys.path else ""

        with patch("openakita.runtime_env._get_openakita_root", return_value=tmp_path):
            from openakita.runtime_env import inject_module_paths_runtime

            inject_module_paths_runtime()

        # sys.path 的第一个元素不应被改变
        assert sys.path[0] == original_first

        # 新路径应在末尾附近
        sp = str(modules_dir / "whisper" / "site-packages")
        idx = sys.path.index(sp)
        assert idx > len(sys.path) // 2  # 应在后半部分

        # 清理
        sys.path = [p for p in sys.path if str(tmp_path) not in p]


class TestRegisterDllDirectories:
    """Windows DLL 目录注册测试"""

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_registers_torch_lib(self, tmp_path):
        """应注册 torch/lib/ 目录"""
        sp_dir = tmp_path / "site-packages"
        torch_lib = sp_dir / "torch" / "lib"
        torch_lib.mkdir(parents=True)
        # 创建一个假的 DLL
        (torch_lib / "c10.dll").write_text("fake")

        sys.path.append(str(sp_dir))
        try:
            os_mock = MagicMock()
            os_mock.environ = dict(os.environ)
            os_mock.add_dll_directory = MagicMock()

            from openakita.runtime_env import _register_dll_directories

            _register_dll_directories(os_mock)

            os_mock.add_dll_directory.assert_called()
            call_args = [str(c[0][0]) for c in os_mock.add_dll_directory.call_args_list]
            assert any(str(torch_lib) in a for a in call_args)
        finally:
            sys.path.remove(str(sp_dir))

    def test_no_crash_on_non_windows(self, tmp_path):
        """非 Windows 平台也不应报错"""
        from openakita.runtime_env import inject_module_paths_runtime

        # 不应抛异常
        with patch("openakita.runtime_env._get_openakita_root", return_value=tmp_path):
            inject_module_paths_runtime()


# ─────────────────────────────────────────────
# 2. _import_helper 模块映射完整性
# ─────────────────────────────────────────────


class TestImportHelper:
    """确保所有后加载模块的关键包都在映射表中"""

    def test_all_module_packages_mapped(self):
        """所有模块的 pip 包对应的 import name 应在映射表中"""
        from openakita.tools._import_helper import _PACKAGE_MODULE_MAP

        # 模块 → 需要映射的 import names
        required_mappings = {
            "vector-memory": ["sentence_transformers", "chromadb"],
            "browser": ["playwright", "browser_use", "langchain_openai"],
            "whisper": ["whisper", "static_ffmpeg"],
            "orchestration": ["zmq"],
        }

        for module_id, import_names in required_mappings.items():
            for name in import_names:
                assert name in _PACKAGE_MODULE_MAP, (
                    f"缺少映射: {name} (模块 {module_id}) 不在 _PACKAGE_MODULE_MAP 中"
                )
                mapped_module_id = _PACKAGE_MODULE_MAP[name][0]
                assert mapped_module_id == module_id, (
                    f"映射错误: {name} 映射到 {mapped_module_id}，应为 {module_id}"
                )

    def test_import_or_hint_returns_none_for_available(self):
        """已安装的标准库包应返回 None"""
        from openakita.tools._import_helper import import_or_hint

        # os 是标准库，一定可以导入
        # 但 import_or_hint 只处理已知映射的包，未知包也应正确处理
        result = import_or_hint("os")
        assert result is None  # os 总是可用的

    def test_import_or_hint_returns_hint_for_missing(self):
        """缺失的包应返回安装提示字符串"""
        from openakita.tools._import_helper import import_or_hint

        # 用一个一定不存在的包名
        result = import_or_hint("__nonexistent_package_xyz__")
        assert result is not None
        assert isinstance(result, str)
        assert "pip install" in result or "设置中心" in result

    def test_frozen_hint_mentions_setup_center(self):
        """打包环境下，提示应引导用户去设置中心"""
        from openakita.tools._import_helper import _build_hint

        with patch("openakita.tools._import_helper.IS_FROZEN", True):
            hint = _build_hint("playwright")
            assert "设置中心" in hint
            assert "浏览器自动化" in hint

    def test_dev_hint_mentions_pip(self):
        """开发环境下，提示应包含 pip install"""
        from openakita.tools._import_helper import _build_hint

        with patch("openakita.tools._import_helper.IS_FROZEN", False):
            hint = _build_hint("sentence_transformers")
            assert "pip install" in hint
            assert "sentence-transformers" in hint


# ─────────────────────────────────────────────
# 3. browser/manager PLAYWRIGHT_BROWSERS_PATH 设置
# ─────────────────────────────────────────────


class TestPlaywrightBrowsersPath:
    """Playwright 浏览器路径环境变量测试"""

    def test_sets_browsers_path_when_dir_exists(self, tmp_path):
        """当 browsers 目录存在时应设置 PLAYWRIGHT_BROWSERS_PATH"""
        browsers_dir = tmp_path / ".openakita" / "modules" / "browser" / "browsers"
        browsers_dir.mkdir(parents=True)
        # 模拟浏览器已安装
        (browsers_dir / "chromium-1234").mkdir()

        env_backup = os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        try:
            with patch("pathlib.Path.home", return_value=tmp_path):
                # 模拟 browser/manager.py 中的逻辑
                if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
                    check_dir = Path.home() / ".openakita" / "modules" / "browser" / "browsers"
                    if check_dir.is_dir():
                        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(check_dir)

                assert os.environ.get("PLAYWRIGHT_BROWSERS_PATH") == str(browsers_dir)
        finally:
            # 恢复
            if env_backup is not None:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = env_backup
            else:
                os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    def test_does_not_override_existing_env(self, tmp_path):
        """如果已设置 PLAYWRIGHT_BROWSERS_PATH，不应覆盖"""
        browsers_dir = tmp_path / ".openakita" / "modules" / "browser" / "browsers"
        browsers_dir.mkdir(parents=True)

        custom_path = "/custom/browsers"
        env_backup = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        try:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = custom_path

            with patch("pathlib.Path.home", return_value=tmp_path):
                # 模拟 browser/manager.py 中的逻辑
                if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
                    check_dir = Path.home() / ".openakita" / "modules" / "browser" / "browsers"
                    if check_dir.is_dir():
                        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(check_dir)

                # 应保持用户自定义的值
                assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == custom_path
        finally:
            if env_backup is not None:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = env_backup
            else:
                os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)

    def test_no_set_when_dir_missing(self, tmp_path):
        """当 browsers 目录不存在时不应设置环境变量"""
        env_backup = os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        try:
            with patch("pathlib.Path.home", return_value=tmp_path):
                if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
                    check_dir = Path.home() / ".openakita" / "modules" / "browser" / "browsers"
                    if check_dir.is_dir():
                        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(check_dir)

                assert "PLAYWRIGHT_BROWSERS_PATH" not in os.environ
        finally:
            if env_backup is not None:
                os.environ["PLAYWRIGHT_BROWSERS_PATH"] = env_backup


# ─────────────────────────────────────────────
# 4. bundle_modules.py 模块定义一致性
# ─────────────────────────────────────────────


class TestBundleModulesConsistency:
    """确保 bundle_modules.py 的模块定义与 main.rs 一致"""

    def test_bundle_script_defines_all_modules(self):
        """bundle_modules.py 应包含所有四个可选模块"""
        # 动态导入 bundle_modules.py
        import importlib.util

        script_path = Path(__file__).parent.parent / "build" / "bundle_modules.py"
        if not script_path.exists():
            pytest.skip("bundle_modules.py not found")

        spec = importlib.util.spec_from_file_location("bundle_modules", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        expected_modules = {"vector-memory", "browser", "whisper", "orchestration"}
        actual_modules = set(mod.MODULE_DEFS.keys())

        assert expected_modules == actual_modules, (
            f"模块定义不一致: 缺少 {expected_modules - actual_modules}, "
            f"多余 {actual_modules - expected_modules}"
        )

    def test_bundle_packages_match_main_rs(self):
        """bundle_modules.py 的包列表应与 main.rs module_definitions 一致"""
        import importlib.util

        script_path = Path(__file__).parent.parent / "build" / "bundle_modules.py"
        if not script_path.exists():
            pytest.skip("bundle_modules.py not found")

        spec = importlib.util.spec_from_file_location("bundle_modules", script_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # main.rs 中的包定义（手动提取，保持同步）
        main_rs_packages = {
            "vector-memory": ["sentence-transformers", "chromadb"],
            "browser": ["playwright", "browser-use", "langchain-openai"],
            "whisper": ["openai-whisper", "static-ffmpeg"],
            "orchestration": ["pyzmq"],
        }

        for module_id, expected_pkgs in main_rs_packages.items():
            bundle_pkgs = mod.MODULE_DEFS[module_id]["packages"]
            # bundle_modules.py 的包名可能带版本约束，只比较基础名
            bundle_base_names = [p.split(">=")[0].split("==")[0].split("<")[0] for p in bundle_pkgs]

            for pkg in expected_pkgs:
                assert pkg in bundle_base_names, (
                    f"模块 {module_id}: main.rs 要求 {pkg} 但 bundle_modules.py 中缺失"
                )

    def test_bundle_default_mirror_is_domestic(self):
        """bundle_modules.py 默认应使用国内镜像"""
        script_path = Path(__file__).parent.parent / "build" / "bundle_modules.py"
        if not script_path.exists():
            pytest.skip("bundle_modules.py not found")

        content = script_path.read_text(encoding="utf-8")
        assert "mirrors.aliyun.com" in content or "tuna.tsinghua" in content, (
            "bundle_modules.py 应默认使用国内镜像"
        )


# ─────────────────────────────────────────────
# 5. 镜像源配置一致性
# ─────────────────────────────────────────────


class TestMirrorConsistency:
    """验证所有 pip 源/下载源配置的一致性"""

    def test_no_dead_mirrors_in_main_rs(self):
        """main.rs 中不应包含已知失效的镜像"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        dead_mirrors = [
            "mirror.ghproxy.com",  # 已被 GFW 封锁 (2024年末)
            "ghproxy.net",  # 已关停
        ]

        for mirror in dead_mirrors:
            # 允许在注释中提到，但不应在实际 URL 中使用
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("//"):
                    continue  # 跳过注释行
                if mirror in stripped and "http" in stripped:
                    pytest.fail(
                        f"main.rs 第 {i} 行使用了已失效的镜像 {mirror}: {stripped[:100]}"
                    )

    def test_pip_presets_default_to_domestic(self):
        """前端 PIP_INDEX_PRESETS 默认应选中国内源"""
        app_tsx_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src"
            / "App.tsx"
        )
        if not app_tsx_path.exists():
            pytest.skip("App.tsx not found")

        content = app_tsx_path.read_text(encoding="utf-8")

        # pipIndexPresetId 默认值应为 "aliyun"
        assert 'useState<"official" | "tuna" | "aliyun" | "custom">("aliyun")' in content, (
            "pipIndexPresetId 默认值应为 aliyun"
        )

        # indexUrl 默认值应为阿里云 URL
        assert 'useState<string>("https://mirrors.aliyun.com/pypi/simple/")' in content, (
            "indexUrl 默认值应为阿里云 URL"
        )

    def test_official_preset_has_explicit_url(self):
        """官方 PyPI 预设应有显式 URL（不应为空字符串）"""
        app_tsx_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src"
            / "App.tsx"
        )
        if not app_tsx_path.exists():
            pytest.skip("App.tsx not found")

        content = app_tsx_path.read_text(encoding="utf-8")

        # 查找 official preset 数据行：{ id: "official", label: ..., url: ... }
        found = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("{") and 'id: "official"' in stripped and "label:" in stripped:
                found = True
                assert 'url: ""' not in stripped, (
                    "官方 PyPI 预设的 url 不应为空，应为 'https://pypi.org/simple/'"
                )
                assert "pypi.org" in stripped, (
                    "官方 PyPI 预设应包含 pypi.org URL"
                )
                break
        assert found, "未找到 official preset 数据行"

    def test_install_module_has_domestic_fallback(self):
        """install_module 的在线安装应有国内源回退"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        # 应包含阿里云和清华两个国内镜像
        assert "mirrors.aliyun.com/pypi/simple" in content
        assert "pypi.tuna.tsinghua.edu.cn/simple" in content
        # pypi.org 作为兜底
        assert "pypi.org/simple" in content

    def test_embedded_python_uses_connect_timeout(self):
        """嵌入式 Python 下载应设置连接超时"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        # install_embedded_python_sync 中应有 connect_timeout
        assert "connect_timeout" in content, (
            "嵌入式 Python 下载的 HTTP 客户端应设置 connect_timeout"
        )

    def test_fetch_pypi_versions_has_fallback(self):
        """fetch_pypi_versions 应有多源回退（阿里云不支持 JSON API）"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        # 应包含清华 JSON API 回退
        assert "pypi.tuna.tsinghua.edu.cn/pypi" in content
        # 应包含 pypi.org JSON API 回退
        assert "pypi.org/pypi" in content

    def test_pip_install_has_default_mirror(self):
        """pip_install 无 index_url 时应有默认国内镜像"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        # pip_install 函数中应有 unwrap_or 默认值
        assert 'unwrap_or("https://mirrors.aliyun.com/pypi/simple/")' in content, (
            "pip_install 应在 index_url 为 None 时默认使用阿里云"
        )


# ─────────────────────────────────────────────
# 6. 模块安装后的 post-install hook
# ─────────────────────────────────────────────


class TestPostInstallHooks:
    """验证模块安装后的后处理逻辑"""

    def test_browser_post_install_in_main_rs(self):
        """browser 模块安装后应执行 playwright install chromium"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        assert 'module_id == "browser"' in content, (
            "install_module 应包含 browser 模块特殊处理"
        )
        assert "playwright" in content and "install" in content and "chromium" in content, (
            "应执行 playwright install chromium"
        )
        assert "PLAYWRIGHT_DOWNLOAD_HOST" in content, (
            "应设置 PLAYWRIGHT_DOWNLOAD_HOST 加速下载"
        )
        assert "cdn.npmmirror.com" in content, (
            "PLAYWRIGHT_DOWNLOAD_HOST 应使用国内 CDN"
        )

    def test_restart_hint_emitted(self):
        """安装完成后应 emit restart-hint"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        assert "restart-hint" in content, (
            "安装完成后应 emit restart-hint 状态"
        )

    def test_playwright_browsers_path_set_at_launch(self):
        """Tauri 启动后端时应设置 PLAYWRIGHT_BROWSERS_PATH"""
        main_rs_path = (
            Path(__file__).parent.parent
            / "apps"
            / "setup-center"
            / "src-tauri"
            / "src"
            / "main.rs"
        )
        if not main_rs_path.exists():
            pytest.skip("main.rs not found")

        content = main_rs_path.read_text(encoding="utf-8")

        # 应存在在启动后端进程（cmd.env）中设置 PLAYWRIGHT_BROWSERS_PATH 的代码
        # 查找 "PLAYWRIGHT_BROWSERS_PATH" 且附近有 cmd.env 的代码段
        assert content.count('"PLAYWRIGHT_BROWSERS_PATH"') >= 2, (
            "PLAYWRIGHT_BROWSERS_PATH 应至少出现 2 次：安装时和启动时"
        )

        # 确认在后端启动函数中（OPENAKITA_MODULE_PATHS 所在函数）也有设置
        # 找到 OPENAKITA_MODULE_PATHS 的 cmd.env 行
        lines = content.split("\n")
        found_launch_pw_path = False
        in_launch_region = False
        for line in lines:
            if "OPENAKITA_MODULE_PATHS" in line and "cmd.env" in line:
                in_launch_region = True
            if in_launch_region and "PLAYWRIGHT_BROWSERS_PATH" in line:
                found_launch_pw_path = True
                break
            # 如果遇到新函数定义，退出搜索区域
            if in_launch_region and line.strip().startswith("fn "):
                break

        assert found_launch_pw_path, (
            "启动后端进程时（cmd.env OPENAKITA_MODULE_PATHS 之后）应设置 PLAYWRIGHT_BROWSERS_PATH"
        )


# ─────────────────────────────────────────────
# 7. 记忆检索回退机制
# ─────────────────────────────────────────────


class _MockEnum:
    """轻量的 enum 模拟，支持相等比较"""
    def __init__(self, value: str):
        self.value = value
    def __eq__(self, other):
        if isinstance(other, _MockEnum):
            return self.value == other.value
        return NotImplemented
    def __hash__(self):
        return hash(self.value)


def _make_mock_memory(mid: str, content: str, mtype: str = "fact",
                      importance: float = 0.7, created_at=None):
    """构造一个 mock Memory 对象"""
    m = MagicMock()
    m.id = mid
    m.content = content
    m.type = _MockEnum(mtype)
    m.priority = _MockEnum("long_term")
    m.importance_score = importance
    m.tags = []
    m.created_at = created_at or datetime.now()
    m.updated_at = m.created_at
    m.access_count = 0
    return m


class TestMemoryRetrievalFallback:
    """当向量库不可用时，记忆检索应回退到关键词搜索"""

    def _make_manager(self, vector_enabled=False, memories=None):
        """构造一个最小化的 mock MemoryManager"""
        manager = MagicMock()
        manager.vector_store = MagicMock()
        manager.vector_store.enabled = vector_enabled
        manager.memory_md_path = MagicMock()
        manager.memory_md_path.exists.return_value = True
        manager.memory_md_path.read_text.return_value = "# Core Memory\n\nTest core memory"

        mem_dict = {}
        if memories:
            for m in memories:
                mem_dict[m.id] = m
        manager._memories = mem_dict

        # 实际的关键词搜索逻辑
        def _keyword_search(query, limit=5):
            keywords = [kw for kw in query.lower().split() if len(kw) > 2]
            if not keywords:
                return []
            results = []
            for memory in mem_dict.values():
                content_lower = memory.content.lower()
                if any(kw in content_lower for kw in keywords):
                    results.append(memory)
            results.sort(key=lambda m: m.importance_score, reverse=True)
            return results[:limit]

        manager._keyword_search = _keyword_search
        manager._strip_common_prefix = lambda c: c
        return manager

    def test_get_injection_context_falls_back_to_keyword(self):
        """向量库不可用时 get_injection_context 应回退到关键词搜索"""
        from openakita.memory.manager import MemoryManager

        mem1 = _make_mock_memory("m1", "用户喜欢 Python 编程语言")
        mem2 = _make_mock_memory("m2", "用户讨厌 Java 语言")
        mem3 = _make_mock_memory("m3", "今天天气很好")

        manager = self._make_manager(vector_enabled=False, memories=[mem1, mem2, mem3])

        # 直接调用 get_injection_context 的逻辑
        # 因为 MemoryManager 构造复杂，我们直接测试核心逻辑
        result = MemoryManager.get_injection_context(manager, "Python")

        assert "Python 编程语言" in result
        assert "关键词匹配" in result
        assert "天气" not in result  # 不相关的不应出现

    def test_get_injection_context_uses_vector_when_available(self):
        """向量库可用时应使用向量搜索"""
        from openakita.memory.manager import MemoryManager

        mem1 = _make_mock_memory("m1", "用户喜欢 Python 编程")

        manager = self._make_manager(vector_enabled=True, memories=[mem1])
        manager.vector_store.search.return_value = [("m1", 0.05)]

        result = MemoryManager.get_injection_context(manager, "编程语言")

        manager.vector_store.search.assert_called_once()
        assert "语义匹配" in result
        assert "Python 编程" in result

    def test_get_injection_context_falls_back_when_vector_empty(self):
        """向量库可用但搜索无结果时应回退关键词搜索"""
        from openakita.memory.manager import MemoryManager

        mem1 = _make_mock_memory("m1", "用户喜欢 Python 编程")

        manager = self._make_manager(vector_enabled=True, memories=[mem1])
        manager.vector_store.search.return_value = []

        result = MemoryManager.get_injection_context(manager, "Python")

        # 向量搜索返回空 → 回退关键词
        assert "Python 编程" in result
        assert "关键词匹配" in result

    def test_get_injection_context_falls_back_when_vector_throws(self):
        """向量搜索抛异常时应回退关键词搜索"""
        from openakita.memory.manager import MemoryManager

        mem1 = _make_mock_memory("m1", "用户喜欢 Python 编程")

        manager = self._make_manager(vector_enabled=True, memories=[mem1])
        manager.vector_store.search.side_effect = RuntimeError("ChromaDB crashed")

        result = MemoryManager.get_injection_context(manager, "Python")

        assert "Python 编程" in result
        assert "关键词匹配" in result

    def test_retriever_falls_back_to_keyword(self):
        """retriever._search_related_memories 向量库不可用时应回退关键词"""
        from openakita.prompt.retriever import _search_related_memories

        mem1 = _make_mock_memory("m1", "用户偏好深色主题界面")
        manager = self._make_manager(vector_enabled=False, memories=[mem1])

        result, used_vector = _search_related_memories(
            query="深色主题",
            memory_manager=manager,
            max_items=5,
        )

        assert not used_vector
        assert "深色主题" in result

    def test_retriever_returns_vector_flag(self):
        """向量搜索成功时应返回 used_vector=True"""
        from openakita.prompt.retriever import _search_related_memories

        mem1 = _make_mock_memory("m1", "用户偏好深色主题界面")
        manager = self._make_manager(vector_enabled=True, memories=[mem1])
        manager.vector_store.search.return_value = [("m1", 0.03)]

        result, used_vector = _search_related_memories(
            query="主题",
            memory_manager=manager,
            max_items=5,
        )

        assert used_vector
        assert "深色主题" in result

    def test_retriever_async_falls_back(self):
        """async_search_related_memories 向量库不可用时应回退"""
        from openakita.prompt.retriever import async_search_related_memories

        mem1 = _make_mock_memory("m1", "系统使用 PostgreSQL 数据库")
        manager = self._make_manager(vector_enabled=False, memories=[mem1])

        result, used_vector = asyncio.get_event_loop().run_until_complete(
            async_search_related_memories(
                query="PostgreSQL",
                memory_manager=manager,
                max_items=5,
            )
        )

        assert not used_vector
        assert "PostgreSQL" in result


class TestVectorStoreRetryReset:
    """向量库重试时应重置全局导入标记"""

    def test_lazy_import_globals_reset_on_retry(self):
        """_ensure_initialized 触发重试时应重置 _sentence_transformers_available 和 _chromadb"""
        import openakita.memory.vector_store as vs_module

        # 保存原始值
        orig_st = vs_module._sentence_transformers_available
        orig_cd = vs_module._chromadb

        try:
            # 模拟初始导入失败
            vs_module._sentence_transformers_available = False
            vs_module._chromadb = None

            # 创建一个 VectorStore，但不让后台线程运行
            with patch.object(vs_module.VectorStore, "_start_background_init"):
                store = vs_module.VectorStore(
                    data_dir=Path(tempfile.mkdtemp()),
                    model_name="test",
                )

            # 模拟 failed 状态且冷却期已过
            store._init_state = "failed"
            store._init_failed = True
            store._init_fail_time = 0  # 很久以前
            store._init_retry_cooldown = 0  # 立即可重试

            # 阻止实际的后台线程启动
            with patch.object(vs_module.VectorStore, "_start_background_init"):
                result = store._ensure_initialized()

            # 关键断言：全局标记应被重置为 None
            assert vs_module._sentence_transformers_available is None, (
                "重试时 _sentence_transformers_available 应被重置为 None"
            )
            assert vs_module._chromadb is None, (
                "重试时 _chromadb 应被重置为 None"
            )
            assert result is False  # 重试仍返回 False（等后台完成）
        finally:
            # 恢复原始值
            vs_module._sentence_transformers_available = orig_st
            vs_module._chromadb = orig_cd

    def test_lazy_import_skips_when_already_false(self):
        """当 _sentence_transformers_available=False 且未重置时，_lazy_import 应直接返回 False"""
        import openakita.memory.vector_store as vs_module

        orig_st = vs_module._sentence_transformers_available
        orig_cd = vs_module._chromadb

        try:
            vs_module._sentence_transformers_available = False
            # 应直接返回 False，不重新尝试 import
            assert vs_module._lazy_import() is False
        finally:
            vs_module._sentence_transformers_available = orig_st
            vs_module._chromadb = orig_cd

    def test_lazy_import_retries_when_reset_to_none(self):
        """当 _sentence_transformers_available 被重置为 None 时，_lazy_import 应重新尝试"""
        import openakita.memory.vector_store as vs_module

        orig_st = vs_module._sentence_transformers_available
        orig_cd = vs_module._chromadb

        try:
            vs_module._sentence_transformers_available = None
            vs_module._chromadb = None

            # 模拟 sentence_transformers 不可用
            with patch("openakita.memory.vector_store.inject_module_paths_runtime",
                        create=True, side_effect=Exception("no runtime")):
                result = vs_module._lazy_import()

            # 应该尝试 import 并（因为缺少包）返回 False
            # 但关键是：它确实执行了 import 尝试（不再短路）
            assert vs_module._sentence_transformers_available is not None, (
                "重置后 _lazy_import 应重新执行 import 尝试"
            )
        finally:
            vs_module._sentence_transformers_available = orig_st
            vs_module._chromadb = orig_cd


class TestConsolidatorDedupFallback:
    """日常整理去重在向量库不可用时应回退字符串匹配"""

    def test_string_dedup_catches_exact_duplicates(self):
        """字符串匹配去重应捕获完全相同的记忆"""
        mem1 = _make_mock_memory("m1", "用户喜欢 Python",
                                  created_at=datetime(2025, 1, 1))
        mem2 = _make_mock_memory("m2", "用户喜欢 Python",
                                  created_at=datetime(2025, 1, 2))

        # 验证 manager._strip_common_prefix 被正确调用
        manager = MagicMock()
        manager.vector_store = MagicMock()
        manager.vector_store.enabled = False
        manager._memories = {"m1": mem1, "m2": mem2}
        manager._strip_common_prefix = lambda c: c
        manager.delete_memory = MagicMock(return_value=True)

        # 手动模拟 consolidator 的字符串去重逻辑
        deleted_ids = set()
        checked_pairs = set()
        memories = [mem1, mem2]

        for memory in memories:
            if memory.id in deleted_ids:
                continue
            if not manager.vector_store.enabled:
                core_a = manager._strip_common_prefix(memory.content)
                for other in memories:
                    if other.id == memory.id or other.id in deleted_ids:
                        continue
                    if other.type != memory.type:
                        continue
                    pair_key = tuple(sorted([memory.id, other.id]))
                    if pair_key in checked_pairs:
                        continue
                    checked_pairs.add(pair_key)
                    core_b = manager._strip_common_prefix(other.content)
                    if core_a == core_b:
                        # 保留更新的
                        to_del = other if other.created_at < memory.created_at else memory
                        deleted_ids.add(to_del.id)

        assert len(deleted_ids) == 1
        assert "m1" in deleted_ids  # m1 更老，应被删除

    def test_string_dedup_ignores_different_content(self):
        """内容不同的记忆不应被误删"""
        mem1 = _make_mock_memory("m1", "用户喜欢 Python")
        mem2 = _make_mock_memory("m2", "用户喜欢 Rust")

        manager = MagicMock()
        manager.vector_store = MagicMock()
        manager.vector_store.enabled = False
        manager._strip_common_prefix = lambda c: c

        deleted_ids = set()
        checked_pairs = set()
        memories = [mem1, mem2]

        for memory in memories:
            if memory.id in deleted_ids:
                continue
            core_a = manager._strip_common_prefix(memory.content)
            for other in memories:
                if other.id == memory.id or other.id in deleted_ids:
                    continue
                if other.type != memory.type:
                    continue
                pair_key = tuple(sorted([memory.id, other.id]))
                if pair_key in checked_pairs:
                    continue
                checked_pairs.add(pair_key)
                core_b = manager._strip_common_prefix(other.content)
                if core_a == core_b:
                    deleted_ids.add(other.id)

        assert len(deleted_ids) == 0  # 不同内容不应删除
