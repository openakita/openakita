"""
OrgPolicies — policy management + index generation + keyword search.

Manages an organization's policy documents (Markdown), automatically maintains
an index, and provides keyword search.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OrgPolicies:
    """Manage policy documents for an organization."""

    def __init__(self, org_dir: Path) -> None:
        self._org_dir = org_dir
        self._policies_dir = org_dir / "policies"
        self._departments_dir = org_dir / "departments"
        self._policies_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list_policies(self, department: str | None = None) -> list[dict]:
        results: list[dict] = []
        dirs = [self._policies_dir]
        if department:
            dept_dir = self._departments_dir / department
            if dept_dir.exists():
                dirs.append(dept_dir)
        elif self._departments_dir.exists():
            for d in self._departments_dir.iterdir():
                if d.is_dir():
                    dirs.append(d)

        for base in dirs:
            for f in sorted(base.glob("*.md")):
                if f.name == "README.md":
                    continue
                title = self._extract_title(f)
                scope = base.name if base != self._policies_dir else "organization"
                results.append({
                    "filename": f.name,
                    "title": title,
                    "scope": scope,
                    "size": f.stat().st_size,
                    "path": str(f.relative_to(self._org_dir)),
                })
        return results

    def read_policy(self, filename: str, department: str | None = None) -> str | None:
        p = self._resolve_path(filename, department)
        if p and p.is_file():
            return p.read_text(encoding="utf-8")
        return None

    def write_policy(
        self, filename: str, content: str,
        department: str | None = None,
    ) -> Path:
        base = self._departments_dir / department if department else self._policies_dir
        base.mkdir(parents=True, exist_ok=True)

        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError("Invalid filename")

        p = base / filename
        p.write_text(content, encoding="utf-8")
        self._rebuild_index()
        return p

    def delete_policy(self, filename: str, department: str | None = None) -> bool:
        p = self._resolve_path(filename, department)
        if p and p.is_file():
            p.unlink()
            self._rebuild_index()
            return True
        return False

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search policy files by keyword (case-insensitive)."""
        query_lower = query.lower()
        results: list[dict] = []

        all_dirs = [self._policies_dir]
        if self._departments_dir.exists():
            for d in self._departments_dir.iterdir():
                if d.is_dir():
                    all_dirs.append(d)

        for base in all_dirs:
            for f in base.glob("*.md"):
                if f.name == "README.md":
                    continue
                try:
                    content = f.read_text(encoding="utf-8")
                    if query_lower not in content.lower() and query_lower not in f.name.lower():
                        continue
                    matched_lines = [
                        line.strip()
                        for line in content.split("\n")
                        if query_lower in line.lower()
                    ][:5]
                    scope = base.name if base != self._policies_dir else "organization"
                    results.append({
                        "filename": f.name,
                        "title": self._extract_title(f),
                        "scope": scope,
                        "matched_lines": matched_lines,
                        "match_count": len(matched_lines),
                    })
                except Exception:
                    continue

        results.sort(key=lambda x: x["match_count"], reverse=True)
        return results[:limit]

    # ------------------------------------------------------------------
    # Index generation
    # ------------------------------------------------------------------

    def _rebuild_index(self) -> None:
        """Rebuild the policies/README.md index file."""
        lines = [
            "# Policy Index\n",
            "> This file is automatically maintained by the system; do not edit manually.\n",
            "| File | Title | Scope | Size |",
            "|------|------|---------|------|",
        ]

        for pol in self.list_policies():
            lines.append(
                f"| {pol['filename']} | {pol['title']} | {pol['scope']} | {pol['size']}B |"
            )

        readme = self._policies_dir / "README.md"
        readme.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def ensure_index(self) -> None:
        """Public method to trigger index rebuild."""
        self._rebuild_index()

    # ------------------------------------------------------------------
    # Template installation
    # ------------------------------------------------------------------

    def install_default_policies(self, template_name: str = "default") -> int:
        """Install default policy documents. Returns count of installed files."""
        policies = POLICY_TEMPLATES.get(template_name, POLICY_TEMPLATES.get("default", {}))
        count = 0
        for filename, content in policies.items():
            p = self._policies_dir / filename
            if not p.exists():
                p.write_text(content, encoding="utf-8")
                count += 1
        if count > 0:
            self._rebuild_index()
        return count

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_path(self, filename: str, department: str | None = None) -> Path | None:
        if ".." in filename or "/" in filename or "\\" in filename:
            return None
        if department:
            p = self._departments_dir / department / filename
            if p.is_file():
                return p
        p = self._policies_dir / filename
        if p.is_file():
            return p
        return None

    @staticmethod
    def _extract_title(path: Path) -> str:
        try:
            first_lines = path.read_text(encoding="utf-8").split("\n", 5)
            for line in first_lines:
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
        except Exception:
            pass
        return path.stem


# ---------------------------------------------------------------------------
# Default policy templates
# ---------------------------------------------------------------------------

POLICY_TEMPLATES: dict[str, dict[str, str]] = {
    "default": {
        "communication-guidelines.md": """# 沟通规范

## 1. 基本原则
- 优先通过组织连线关系沟通
- 跨级沟通需先通知直属上级
- 紧急事项可直接上报

## 2. 消息格式
- 任务分配：明确目标、截止日期、验收标准
- 工作汇报：进展、阻塞、下一步计划
- 问题上报：问题描述、影响范围、建议方案

## 3. 响应时效
- 紧急消息：15分钟内响应
- 普通消息：1小时内响应
- 非紧急消息：当天内响应
""",
        "task-management.md": """# 任务管理规范

## 1. 任务分配
- 任务需包含明确的目标描述
- 指定负责人和协作人
- 设定合理的截止日期

## 2. 进度汇报
- 重大进展及时汇报
- 遇到阻塞立即上报
- 任务完成后提交成果总结

## 3. 质量要求
- 交付物需满足验收标准
- 重要决策记录到组织黑板
- 经验教训写入部门记忆
""",
        "scaling-policy.md": """# 人员扩编制度

## 1. 克隆申请（加人手）
- 需说明当前工作量和瓶颈
- 指定要克隆的岗位
- 审批流程：上级 -> 用户确认

## 2. 招募申请（新岗位）
- 需说明岗位职责和目标
- 说明与现有岗位的关系
- 审批流程：上级 -> 用户确认

## 3. 冻结与裁撤
- 冻结：保留数据，暂停活动
- 裁撤：仅限临时节点，记忆归档到部门
""",
    },
    "software-team": {
        "code-review.md": """# 代码审查规范

## 1. 审查流程
- 所有代码变更需经过审查
- 前端变更由前端组长审查
- 后端变更由后端组长审查
- 跨组变更由技术负责人审查

## 2. 审查标准
- 代码风格一致性
- 逻辑正确性
- 性能影响评估
- 测试覆盖率

## 3. 合并条件
- 至少一位审查者批准
- 所有自动化测试通过
- 无未解决的评论
""",
        "deployment-process.md": """# 部署流程

## 1. 环境管理
- 开发环境：自动部署
- 测试环境：QA 验证后部署
- 生产环境：技术负责人审批后部署

## 2. 发布流程
1. 功能分支合并到主干
2. 自动化测试通过
3. QA 回归测试
4. 生产环境部署
5. 部署后监控

## 3. 回滚策略
- 发现严重问题立即回滚
- 回滚后复盘分析
""",
    },
    "content-ops": {
        "content-standards.md": """# 内容标准

## 1. 质量要求
- 原创内容，禁止抄袭
- 事实准确，数据有来源
- 语言通顺，逻辑清晰

## 2. 发布流程
1. 选题策划（策划编辑）
2. 初稿撰写（写手）
3. SEO 优化（SEO 优化师）
4. 主编审核
5. 发布

## 3. 内容排期
- 每周至少 3 篇内容
- 热点内容 24 小时内发布
- 长文提前一周准备
""",
        "brand-guidelines.md": """# 品牌规范

## 1. 语气风格
- 专业但不生硬
- 友好但不随意
- 简洁明了

## 2. 视觉规范
- 统一配色方案
- 标准 logo 使用规范
- 配图风格一致

## 3. 禁忌事项
- 不涉及敏感话题
- 不做虚假承诺
- 不贬低竞争对手
""",
    },
}
