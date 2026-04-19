"""
OrgReporter — Organization report generation

Unified entry point for generating standup meeting minutes, weekly reports,
task summaries, audit logs, and other reports.  Fetches raw events from
OrgEventStore, organizes them, and writes Markdown report files.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runtime import OrgRuntime

logger = logging.getLogger(__name__)


class OrgReporter:
    """Generate organizational reports from events and state."""

    def __init__(self, runtime: OrgRuntime) -> None:
        self._runtime = runtime

    def generate_standup_report(self, org_id: str) -> Path:
        """Generate a standup meeting report for today."""
        org = self._runtime.get_org(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        es = self._runtime.get_event_store(org_id)
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        events = es.query(since=today, limit=500)

        node_summaries: dict[str, list[str]] = {}
        for evt in events:
            actor = evt.get("actor", "system")
            etype = evt.get("event_type", "")
            data = evt.get("data", {})
            if actor not in node_summaries:
                node_summaries[actor] = []
            node_summaries[actor].append(f"- [{etype}] {json.dumps(data, ensure_ascii=False)[:120]}")

        lines = [
            f"# Standup Minutes — {today}",
            f"\n**Organization**: {org.name}",
            f"**Status**: {org.status.value}",
            f"**Node count**: {len(org.nodes)}",
            "",
        ]

        for node in org.nodes:
            lines.append(f"## {node.role_title} ({node.id})")
            lines.append(f"- Status: {node.status.value}")
            lines.append(f"- Department: {node.department or 'Unassigned'}")
            node_events = node_summaries.get(node.id, [])
            if node_events:
                lines.append("- Today's activity:")
                lines.extend(f"  {e}" for e in node_events[:10])
            else:
                lines.append("- No activity today")
            lines.append("")

        content = "\n".join(lines)
        reports_dir = self._runtime._manager._org_dir(org_id) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"standup_{today}.md"
        report_path.write_text(content, encoding="utf-8")

        logger.info(f"[Reporter] Standup report generated: {report_path}")
        return report_path

    def generate_weekly_report(self, org_id: str, weeks_back: int = 0) -> Path:
        """Generate a weekly summary report."""
        org = self._runtime.get_org(org_id)
        if not org:
            raise ValueError(f"Organization not found: {org_id}")

        es = self._runtime.get_event_store(org_id)
        now = datetime.now(UTC)
        week_end = now - timedelta(weeks=weeks_back)
        week_start = week_end - timedelta(days=7)

        events = es.query(
            since=week_start.isoformat(),
            until=week_end.isoformat(),
            limit=2000,
        )

        task_completed = 0
        task_failed = 0
        messages_sent = 0
        nodes_activated = 0
        errors = []
        decisions = []
        scalings = []

        for evt in events:
            etype = evt.get("event_type", "")
            if etype == "task_completed":
                task_completed += 1
            elif etype == "task_failed":
                task_failed += 1
                errors.append(evt)
            elif etype == "message_sent":
                messages_sent += 1
            elif etype == "node_activated":
                nodes_activated += 1
            elif etype == "heartbeat_decision":
                decisions.append(evt)
            elif etype.startswith("scaling_"):
                scalings.append(evt)

        date_str = week_start.strftime("%Y-%m-%d")
        lines = [
            f"# Weekly Report — {date_str} ~ {week_end.strftime('%Y-%m-%d')}",
            f"\n**Organization**: {org.name}",
            "",
            "## Overview",
            f"- Tasks completed: {task_completed}",
            f"- Tasks failed: {task_failed}",
            f"- Messages exchanged: {messages_sent}",
            f"- Node activations: {nodes_activated}",
            f"- Token usage: {org.total_tokens_used}",
            "",
        ]

        if errors:
            lines.append("## Errors & Exceptions")
            for err in errors[:10]:
                data = err.get("data", {})
                lines.append(f"- [{err.get('actor', '?')}] {data.get('error', 'unknown')[:100]}")
            lines.append("")

        if decisions:
            lines.append("## Management Decisions")
            for dec in decisions[:10]:
                data = dec.get("data", {})
                lines.append(f"- {data.get('decision', json.dumps(data, ensure_ascii=False)[:100])}")
            lines.append("")

        if scalings:
            lines.append("## Personnel Changes")
            for sc in scalings[:10]:
                lines.append(f"- [{sc.get('event_type', '')}] {json.dumps(sc.get('data', {}), ensure_ascii=False)[:100]}")
            lines.append("")

        content = "\n".join(lines)
        reports_dir = self._runtime._manager._org_dir(org_id) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"weekly_{date_str}.md"
        report_path.write_text(content, encoding="utf-8")

        logger.info(f"[Reporter] Weekly report generated: {report_path}")
        return report_path

    def generate_task_summary(self, org_id: str, task_id: str) -> Path:
        """Generate a summary report for a specific task."""
        es = self._runtime.get_event_store(org_id)
        events = es.query(limit=2000)

        task_events = [
            e for e in events
            if e.get("data", {}).get("task_id") == task_id
            or e.get("metadata", {}).get("trace_id") == task_id
        ]

        lines = [
            f"# Task Summary — {task_id}",
            "",
        ]

        if not task_events:
            lines.append("No related event records found.")
        else:
            for evt in task_events:
                lines.append(
                    f"- [{evt.get('timestamp', '')}] "
                    f"{evt.get('event_type', '')} "
                    f"by {evt.get('actor', '?')}: "
                    f"{json.dumps(evt.get('data', {}), ensure_ascii=False)[:150]}"
                )

        content = "\n".join(lines)
        reports_dir = self._runtime._manager._org_dir(org_id) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"task_summary_{task_id}.md"
        report_path.write_text(content, encoding="utf-8")

        return report_path

    def generate_audit_report(self, org_id: str, days: int = 7) -> Path:
        """Generate an audit log report."""
        es = self._runtime.get_event_store(org_id)
        log = es.get_audit_log(days=days)

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        lines = [
            f"# Audit Log — Last {days} Day(s)",
            f"\nGenerated at: {date_str}",
            "",
        ]

        important_types = {
            "org_started", "org_stopped", "scaling_approved", "scaling_rejected",
            "node_frozen", "node_unfrozen", "policy_proposed", "conflict_detected",
            "task_failed", "user_command",
        }

        for entry in log:
            etype = entry.get("event_type", "")
            marker = "⚠️ " if etype in important_types else ""
            lines.append(
                f"- {marker}[{entry.get('timestamp', '')[:19]}] "
                f"**{etype}** by {entry.get('actor', '?')}"
            )

        content = "\n".join(lines)
        reports_dir = self._runtime._manager._org_dir(org_id) / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"audit_{date_str}.md"
        report_path.write_text(content, encoding="utf-8")

        return report_path
