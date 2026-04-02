"""
Agent 包 manifest.json 数据模型与校验逻辑

遵循 Open Agent Sharing Specification v1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from openakita.memory.types import normalize_tags

SPEC_VERSION = "1.1"
SUPPORTED_SPEC_VERSIONS = {"1.0", "1.1"}

_ID_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{1,62}[a-z0-9])?$")
_NO_DOUBLE_HYPHEN = re.compile(r"--")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+")

MAX_PACKAGE_SIZE = 50 * 1024 * 1024  # 50MB
MAX_SINGLE_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_ICON_SIZE = 256 * 1024  # 256KB

FORBIDDEN_EXTENSIONS = frozenset({
    ".exe", ".bat", ".cmd", ".sh", ".bash", ".ps1",
    ".py", ".rb", ".pl", ".php", ".jar", ".class",
    ".dll", ".so", ".dylib", ".msi", ".deb", ".rpm",
})


@dataclass
class ManifestAuthor:
    name: str
    url: str = ""

    def validate(self) -> list[str]:
        errors = []
        if not self.name or not self.name.strip():
            errors.append("author.name is required")
        return errors


@dataclass
class ExternalSkillRef:
    """Reference to a third-party skill fetched from its original source at install time."""
    id: str
    source: str  # e.g. "owner/repo@skill-name"
    version: str = ""
    license: str = "unknown"
    url: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExternalSkillRef:
        return cls(
            id=data.get("id", ""),
            source=data.get("source", ""),
            version=data.get("version", ""),
            license=data.get("license", "unknown"),
            url=data.get("url", ""),
            required=data.get("required", True),
        )


@dataclass
class AgentManifest:
    spec_version: str = SPEC_VERSION
    id: str = ""
    name: str = ""
    name_i18n: dict[str, str] = field(default_factory=dict)
    description: str = ""
    description_i18n: dict[str, str] = field(default_factory=dict)
    version: str = "1.0.0"
    author: ManifestAuthor = field(default_factory=lambda: ManifestAuthor(name=""))
    category: str = ""
    tags: list[str] = field(default_factory=list)
    license: str = "MIT"
    min_platform_version: str = ""
    bundled_skills: list[str] = field(default_factory=list)
    required_builtin_skills: list[str] = field(default_factory=list)
    required_external_skills: list[ExternalSkillRef] = field(default_factory=list)
    created_at: str = ""
    checksum: str = ""

    def __post_init__(self):
        self.tags = normalize_tags(self.tags)

    def validate(self) -> list[str]:
        """返回所有校验错误。空列表表示有效。"""
        errors: list[str] = []

        if self.spec_version not in SUPPORTED_SPEC_VERSIONS:
            errors.append(
                f"Unsupported spec_version: {self.spec_version!r} "
                f"(supported: {SUPPORTED_SPEC_VERSIONS})"
            )

        if not self.id:
            errors.append("id is required")
        elif not _ID_PATTERN.match(self.id):
            errors.append(
                f"Invalid id format: {self.id!r} "
                "(must be 3-64 chars, lowercase alphanumeric + hyphens)"
            )
        elif _NO_DOUBLE_HYPHEN.search(self.id):
            errors.append(f"id must not contain consecutive hyphens: {self.id!r}")

        if not self.name:
            errors.append("name is required")

        if not self.description:
            errors.append("description is required")

        if not _SEMVER_PATTERN.match(self.version):
            errors.append(f"Invalid version format: {self.version!r} (expected SemVer)")

        errors.extend(self.author.validate())

        if self.min_platform_version and not _SEMVER_PATTERN.match(
            self.min_platform_version
        ):
            errors.append(
                f"Invalid min_platform_version: {self.min_platform_version!r}"
            )

        return errors

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ["name_i18n", "description_i18n", "tags", "bundled_skills",
                     "required_builtin_skills", "required_external_skills"]:
            if not d.get(key):
                d.pop(key, None)
        for key in ["category", "license", "min_platform_version", "checksum"]:
            if not d.get(key):
                d.pop(key, None)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentManifest:
        author_data = data.get("author", {})
        if isinstance(author_data, dict):
            author = ManifestAuthor(
                name=author_data.get("name", ""),
                url=author_data.get("url", ""),
            )
        else:
            author = ManifestAuthor(name=str(author_data))

        ext_skills_raw = data.get("required_external_skills", [])
        ext_skills = [
            ExternalSkillRef.from_dict(s) if isinstance(s, dict) else s
            for s in ext_skills_raw
        ]

        return cls(
            spec_version=data.get("spec_version", SPEC_VERSION),
            id=data.get("id", ""),
            name=data.get("name", ""),
            name_i18n=data.get("name_i18n", {}),
            description=data.get("description", ""),
            description_i18n=data.get("description_i18n", {}),
            version=data.get("version", "1.0.0"),
            author=author,
            category=data.get("category", ""),
            tags=data.get("tags", []),
            license=data.get("license", "MIT"),
            min_platform_version=data.get("min_platform_version", ""),
            bundled_skills=data.get("bundled_skills", []),
            required_builtin_skills=data.get("required_builtin_skills", []),
            required_external_skills=ext_skills,
            created_at=data.get("created_at", ""),
            checksum=data.get("checksum", ""),
        )


def validate_file_safety(filepath: str) -> list[str]:
    """校验文件路径安全性"""
    errors = []
    normalized = filepath.replace("\\", "/")

    if ".." in normalized.split("/"):
        errors.append(f"Path traversal detected: {filepath}")

    if normalized.startswith("/"):
        errors.append(f"Absolute path not allowed: {filepath}")

    ext = "." + normalized.rsplit(".", 1)[-1].lower() if "." in normalized else ""
    if ext in FORBIDDEN_EXTENSIONS:
        errors.append(f"Forbidden file type: {filepath}")

    return errors
