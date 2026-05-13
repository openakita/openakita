"""Unified facade for OpenAkita runtime and execution environment decisions.

This module is intentionally thin.  The historical implementation still lives
in ``runtime_env.py`` (global app/agent/toolchain runtime) and
``runtime_envs.py`` (scoped agent/skill/scratch venvs).  New call sites should
import from here so the public boundary has one name even while the internals
remain split.
"""

from __future__ import annotations

from .runtime_env import (
    IS_FROZEN,
    apply_agent_python_environment,
    apply_managed_node_environment,
    apply_runtime_pip_environment,
    build_user_subprocess_environment,
    can_pip_install,
    ensure_ssl_certs,
    get_agent_pip_command,
    get_agent_python_executable,
    get_app_python_executable,
    get_channel_deps_dir,
    get_channel_deps_seed_dirs,
    get_managed_node_bin_dir,
    get_managed_node_seed,
    get_managed_python_seed,
    get_pip_install_args,
    get_python_executable,
    get_readonly_seed_roots,
    get_runtime_environment_report,
    get_runtime_root,
    get_workspace_dependency_cache_root,
    inject_module_paths,
    inject_module_paths_runtime,
    log_runtime_environment_report,
    resolve_pip_index,
    resolve_toolchain_command,
    verify_python_executable,
)
from .runtime_envs import (
    ExecutionEnvScope,
    ExecutionEnvSpec,
    apply_execution_environment,
    describe_execution_env,
    ensure_execution_env,
    get_execution_env_seed_roots,
    get_execution_envs_root,
    resolve_agent_env,
    resolve_scratch_env,
    resolve_skill_env,
)

__all__ = [
    "IS_FROZEN",
    "ExecutionEnvScope",
    "ExecutionEnvSpec",
    "apply_agent_python_environment",
    "apply_execution_environment",
    "apply_managed_node_environment",
    "apply_runtime_pip_environment",
    "build_user_subprocess_environment",
    "can_pip_install",
    "describe_execution_env",
    "ensure_execution_env",
    "ensure_ssl_certs",
    "get_agent_pip_command",
    "get_agent_python_executable",
    "get_app_python_executable",
    "get_channel_deps_dir",
    "get_channel_deps_seed_dirs",
    "get_execution_env_seed_roots",
    "get_execution_envs_root",
    "get_managed_node_bin_dir",
    "get_managed_node_seed",
    "get_managed_python_seed",
    "get_pip_install_args",
    "get_python_executable",
    "get_readonly_seed_roots",
    "get_runtime_environment_report",
    "get_runtime_root",
    "get_workspace_dependency_cache_root",
    "inject_module_paths",
    "inject_module_paths_runtime",
    "log_runtime_environment_report",
    "resolve_agent_env",
    "resolve_pip_index",
    "resolve_scratch_env",
    "resolve_skill_env",
    "resolve_toolchain_command",
    "verify_python_executable",
]
