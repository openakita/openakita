"""BPConfigLoader — 加载 best_practice/ 目录下的 BP 配置。

加载流程:
1. 扫描 best_practice/*/config.yaml
2. 先加载 _shared/ 的 profiles (共享 agents)
3. 每个 BP 目录: 加载 config.yaml → profiles/ → prompts/
4. 注册 AgentProfile 到 ProfileStore
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from .config import load_bp_config, validate_bp_config
from .models import BestPracticeConfig

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class BPConfigLoader:
    """从文件系统加载 BP 配置与 profiles。"""

    def __init__(
        self,
        search_paths: list[Path | str] | None = None,
        profile_store: Any = None,
    ) -> None:
        self._search_paths = [Path(p) for p in (search_paths or [])]
        self._profile_store = profile_store
        self._configs: dict[str, BestPracticeConfig] = {}

    @property
    def configs(self) -> dict[str, BestPracticeConfig]:
        return dict(self._configs)

    def load_all(self) -> dict[str, BestPracticeConfig]:
        """扫描所有搜索路径，加载 BP 配置。返回 {bp_id: config}。"""
        self._configs.clear()

        for base_path in self._search_paths:
            if not base_path.is_dir():
                continue

            # 先加载 _shared/ profiles
            shared_dir = base_path / "_shared"
            if shared_dir.is_dir():
                self._load_profiles_from_dir(shared_dir)

            # 扫描子目录
            for sub_dir in sorted(base_path.iterdir()):
                if not sub_dir.is_dir() or sub_dir.name.startswith("_"):
                    continue

                config_file = sub_dir / "config.yaml"
                if not config_file.exists():
                    continue

                try:
                    config = self._load_single(config_file, sub_dir)
                    if config:
                        self._configs[config.id] = config
                        logger.info(f"[BP] Loaded config: {config.id} ({config.name})")
                except Exception as e:
                    logger.error(f"[BP] Failed to load {config_file}: {e}")

        return dict(self._configs)

    def _load_single(self, config_file: Path, bp_dir: Path) -> BestPracticeConfig | None:
        """加载单个 BP 配置。"""
        text = config_file.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        if not raw:
            return None

        config = load_bp_config(raw)

        # 校验
        errors = validate_bp_config(config)
        if errors:
            logger.warning(f"[BP] Validation errors in {config_file}: {errors}")

        # 加载该 BP 的 profiles
        self._load_profiles_from_dir(bp_dir)

        return config

    def _load_profiles_from_dir(self, bp_dir: Path) -> None:
        """加载 bp_dir/profiles/*.json 并注册到 ProfileStore。"""
        if not self._profile_store:
            return

        profiles_dir = bp_dir / "profiles"
        if not profiles_dir.is_dir():
            return

        for json_file in sorted(profiles_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))

                # 处理 prompt_file → custom_prompt 转换
                prompt_file = data.pop("prompt_file", None)
                if prompt_file and not data.get("custom_prompt"):
                    prompt_path = bp_dir / prompt_file
                    if prompt_path.exists():
                        data["custom_prompt"] = prompt_path.read_text(encoding="utf-8")
                    else:
                        logger.warning(f"[BP] prompt_file not found: {prompt_path}")

                self._register_profile(data, json_file)
            except Exception as e:
                logger.error(f"[BP] Failed to load profile {json_file}: {e}")

    def _register_profile(self, data: dict[str, Any], source_file: Path) -> None:
        """将 profile dict 注册到 ProfileStore。"""
        if not self._profile_store:
            return

        profile_id = data.get("id", "")
        if not profile_id:
            logger.warning(f"[BP] Profile without id in {source_file}")
            return

        try:
            # 检查是否已存在
            if self._profile_store.exists(profile_id):
                logger.warning(
                    f"[BP] Profile '{profile_id}' from '{source_file}' "
                    f"overrides existing profile"
                )

            # 使用 ProfileStore 的标准接口
            from seeagent.agents.profile import AgentProfile
            profile = AgentProfile.from_dict(data)
            self._profile_store.save(profile)
            logger.debug(f"[BP] Registered profile: {profile_id}")
        except ImportError:
            # 如果没有 seeagent.agents.profile，存为 raw dict
            logger.debug(f"[BP] ProfileStore not available, skipping: {profile_id}")
        except Exception as e:
            logger.warning(f"[BP] Failed to register profile '{profile_id}': {e}")

    def has_configs(self) -> bool:
        """检查是否有 BP 配置可加载。"""
        for base_path in self._search_paths:
            if not base_path.is_dir():
                continue
            for sub_dir in base_path.iterdir():
                if sub_dir.is_dir() and not sub_dir.name.startswith("_"):
                    if (sub_dir / "config.yaml").exists():
                        return True
        return False
