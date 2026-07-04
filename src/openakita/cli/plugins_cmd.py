"""``openakita plugins ...`` Typer sub-app.

Currently exposes a single ``reseed`` command -- the action half of the
plugin re-seed feature (hygiene #4 / discovered during F-2).  Copies updated
``.py`` files from the git-tracked seed tree ``plugins/`` into the runtime
copy ``data/plugins/`` that :class:`openakita.plugins.manager.PluginManager`
actually loads from.  Default is dry-run; ``--apply`` does the writes,
``--force`` overrides RUNTIME-NEWER files.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..plugins.reseed import (
    STATUS_IDENTICAL,
    STATUS_RUNTIME_NEWER,
    STATUS_RUNTIME_ONLY,
    STATUS_SOURCE_NEWER,
    STATUS_SOURCE_ONLY,
    DiffEntry,
    apply_reseed,
    compute_drift,
    format_human_delta,
)

plugins_app = typer.Typer(
    name="plugins",
    help="Plugin maintenance commands (drift detection, re-seed, ...)",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


def _resolve_roots(source: Path | None, runtime: Path | None) -> tuple[Path, Path]:
    """Pick the source / runtime roots, falling back to ``settings.project_root``.

    Done lazily inside the command so ``--help`` works even when ``settings``
    cannot be constructed (e.g. missing ``.env``).
    """
    if source is None or runtime is None:
        # Lazy import: keeps ``--help`` cheap and decouples from config.
        from ..config import settings

        project_root = Path(settings.project_root)
        if source is None:
            source = project_root / "plugins"
        if runtime is None:
            runtime = project_root / "data" / "plugins"
    return Path(source), Path(runtime)


def _short_path(root: Path, entry: DiffEntry) -> str:
    """Render ``<root_name>/<plugin>/<rel>`` for the diff table."""
    return f"{root.name}/{entry.plugin_id}/{entry.rel_path}"


def _render_diff_table(
    source: Path,
    runtime: Path,
    entries: list[DiffEntry],
    *,
    title: str,
) -> None:
    """Print one rich ``Table`` per status bucket.  Empty buckets are skipped."""
    if not entries:
        return
    tbl = Table(title=title, show_header=True, header_style="bold cyan")
    tbl.add_column("Plugin", style="bold")
    tbl.add_column("Path")
    tbl.add_column("Delta", justify="right")
    for e in entries[:200]:  # safety cap: avoid printing 1000-line tables
        if e.status == STATUS_SOURCE_NEWER:
            delta = f"+{format_human_delta(e.delta_seconds)}"
        elif e.status == STATUS_RUNTIME_NEWER:
            delta = f"-{format_human_delta(-e.delta_seconds)}"
        else:
            delta = ""
        tbl.add_row(e.plugin_id, e.rel_path, delta)
    if len(entries) > 200:
        tbl.add_row("...", f"and {len(entries) - 200} more", "")
    console.print(tbl)


@plugins_app.command("reseed")
def reseed(
    apply: bool = typer.Option(
        False, "--apply", help="Actually copy SOURCE-NEWER / SOURCE-ONLY files (default: dry-run)."
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Also overwrite RUNTIME-NEWER files (will discard local edits in data/plugins/).",
    ),
    plugin: str | None = typer.Option(
        None, "--plugin", "-p", help="Filter to a single plugin id (e.g. 'clip-sense')."
    ),
    source: Path | None = typer.Option(
        None,
        "--source",
        help="Override source dir (default: <project_root>/plugins).",
    ),
    runtime: Path | None = typer.Option(
        None,
        "--runtime",
        help="Override runtime dir (default: <project_root>/data/plugins).",
    ),
) -> None:
    """Re-seed ``data/plugins/`` from the git-tracked ``plugins/`` source tree.

    The default is dry-run: a per-bucket table of the diff is printed and no
    files are touched.  Pass ``--apply`` to actually copy.  Files whose
    runtime copy is newer than the source are protected by default and
    require ``--force`` to overwrite.
    """
    source_root, runtime_root = _resolve_roots(source, runtime)

    if not source_root.is_dir():
        console.print(f"[bold red]X[/bold red] source dir not found: {source_root}")
        console.print("  Pass --source <dir> or run from the project root.")
        raise typer.Exit(2)
    runtime_root.mkdir(parents=True, exist_ok=True)

    report = compute_drift(source_root, runtime_root, plugin_id=plugin)

    # Per-bucket diff tables (always shown, even on --apply, so the operator
    # sees exactly what was / will be touched).
    _render_diff_table(
        source_root,
        runtime_root,
        report.by_status[STATUS_SOURCE_NEWER],
        title="SOURCE-NEWER (will copy on --apply)",
    )
    _render_diff_table(
        source_root,
        runtime_root,
        report.by_status[STATUS_SOURCE_ONLY],
        title="SOURCE-ONLY (newly seeded -- will copy on --apply)",
    )
    _render_diff_table(
        source_root,
        runtime_root,
        report.by_status[STATUS_RUNTIME_NEWER],
        title="RUNTIME-NEWER (protected -- use --force to overwrite)",
    )
    _render_diff_table(
        source_root,
        runtime_root,
        report.by_status[STATUS_RUNTIME_ONLY],
        title="RUNTIME-ONLY (not in source; left untouched)",
    )

    # Summary
    summary = Table(title="Summary", show_header=True, header_style="bold")
    summary.add_column("Status")
    summary.add_column("Count", justify="right")
    for status in (
        STATUS_SOURCE_NEWER,
        STATUS_SOURCE_ONLY,
        STATUS_RUNTIME_NEWER,
        STATUS_RUNTIME_ONLY,
        STATUS_IDENTICAL,
    ):
        summary.add_row(status, str(report.count(status)))
    console.print(summary)

    if not apply:
        if report.has_drift:
            console.print(
                "[yellow]Dry-run only.[/yellow] Re-run with [bold]--apply[/bold] to actually copy."
            )
        else:
            console.print("[green]OK[/green] plugins/ and data/plugins/ are in sync.")
        return

    # --apply: refuse silently if there is nothing to do.
    if not report.has_drift and not (force and report.count(STATUS_RUNTIME_NEWER)):
        console.print("[green]OK[/green] nothing to copy; trees already in sync.")
        return

    if not force and report.count(STATUS_RUNTIME_NEWER):
        console.print(
            f"[yellow]Note:[/yellow] {report.count(STATUS_RUNTIME_NEWER)} RUNTIME-NEWER "
            "file(s) protected; re-run with --force to overwrite them."
        )

    result = apply_reseed(
        source_root,
        runtime_root,
        report,
        force=force,
        dry_run=False,
    )

    console.print(
        f"[green]Done.[/green] copied={len(result.copied)} "
        f"forced={len(result.forced)} skipped_runtime_newer={len(result.skipped_runtime_newer)} "
        f"errors={len(result.errors)}"
    )
    for entry, err in result.errors:
        console.print(
            f"  [red]X[/red] {entry.plugin_id}/{entry.rel_path}: {err}"
        )
    if result.errors:
        raise typer.Exit(1)
