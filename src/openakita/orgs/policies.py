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
        "communication-guidelines.md": """# Communication Guidelines

## 1. Basic Principles
- Prioritize communication through organization structure
- Cross-level communication requires notifying direct supervisor first
- Urgent matters can be escalated directly

## 2. Message Format
- Task assignment: clear objectives, deadlines, acceptance criteria
- Work reports: progress, blockers, next steps
- Issue reporting: issue description, impact scope, suggested solutions

## 3. Response Time
- Urgent messages: respond within 15 minutes
- Regular messages: respond within 1 hour
- Non-urgent messages: respond same day
""",
        "task-management.md": """# Task Management Guidelines

## 1. Task Assignment
- Tasks must include clear objective descriptions
- Designate responsible person and collaborators
- Set reasonable deadlines

## 2. Progress Reporting
- Report major progress promptly
- Report blockers immediately
- Submit summary upon completion

## 3. Quality Requirements
- Deliverables must meet acceptance criteria
- Important decisions recorded to organization board
- Lessons learned documented in department memory
""",
        "scaling-policy.md": """# Personnel Scaling Policy

## 1. Cloning Request (Add Resources)
- Explain current workload and bottlenecks
- Specify position to clone
- Approval workflow: Supervisor -> User confirmation

## 2. Recruitment Request (New Position)
- Explain role responsibilities and goals
- Clarify relationship to existing positions
- Approval workflow: Supervisor -> User confirmation

## 3. Freeze and Termination
- Freeze: retain data, pause activities
- Termination: only for temporary nodes, archive memory to department
""",
    },
    "software-team": {
        "code-review.md": """# Code Review Guidelines

## 1. Review Process
- All code changes require review
- Frontend changes reviewed by frontend lead
- Backend changes reviewed by backend lead
- Cross-team changes reviewed by tech lead

## 2. Review Standards
- Code style consistency
- Logic correctness
- Performance impact assessment
- Test coverage

## 3. Merge Conditions
- At least one reviewer approval
- All automated tests pass
- No unresolved comments
""",
        "deployment-process.md": """# Deployment Process

## 1. Environment Management
- Dev environment: auto-deploy
- Test environment: deploy after QA validation
- Production environment: deploy after tech lead approval

## 2. Release Workflow
1. Merge feature branch to main
2. Automated tests pass
3. QA regression testing
4. Production deployment
5. Post-deployment monitoring

## 3. Rollback Strategy
- Rollback immediately on critical issues
- Post-rollback analysis
""",
    },
    "content-ops": {
        "content-standards.md": """# Content Standards

## 1. Quality Requirements
- Original content, no plagiarism
- Factually accurate, data sourced
- Clear language, logical flow

## 2. Publishing Workflow
1. Topic planning (planning editor)
2. Draft writing (writer)
3. SEO optimization (SEO specialist)
4. Editor review
5. Publish

## 3. Content Schedule
- At least 3 pieces per week
- Hot topics published within 24 hours
- Long-form content prepared one week ahead
""",
        "brand-guidelines.md": """# Brand Guidelines

## 1. Tone and Style
- Professional but not rigid
- Friendly but not casual
- Clear and concise

## 2. Visual Standards
- Consistent color scheme
- Standard logo usage guidelines
- Consistent image style

## 3. Prohibited Actions
- Do not cover sensitive topics
- Do not make false promises
- Do not disparage competitors
""",
    },
}
