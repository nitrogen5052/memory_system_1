"""Command-line interface for portable project memory."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import sys
from typing import Sequence

from .config import ConfigError, load_compatibility
from .installer import ApplyError, apply_plan, rollback_installation
from .planner import build_plan, format_plan
from .runtime import run_doctor
from .verifier import verify_workspace


def main(argv: Sequence[str] | None = None) -> int:
    """Run a memory-system command and return its documented exit status."""
    parser = _parser()
    arguments = parser.parse_args(argv)
    workspace, config_path = _paths(arguments)

    try:
        if arguments.command == "doctor":
            return _doctor(workspace)
        if arguments.command == "plan":
            plan = build_plan(workspace, config_path)
            print(format_plan(plan, json_output=arguments.json), end="")
            return 3 if plan.conflicts else 0
        if arguments.command == "apply":
            return _apply(workspace, config_path, arguments.yes)
        if arguments.command == "verify":
            report = verify_workspace(workspace, config_path, arguments.require_runtime)
            for check in report.checks:
                print(f"{'PASS' if check.passed else 'FAIL'} {check.code}: {check.message}")
            return 0 if report.ok else 5
        if arguments.command == "rollback":
            return _rollback(workspace, arguments.backup)
    except (ApplyError, ConfigError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 6 if arguments.command == "rollback" else 2
    parser.error("a command is required")
    return 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memory-system")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor")
    _workspace_options(doctor)

    plan = commands.add_parser("plan")
    _workspace_options(plan)
    plan.add_argument("--json", action="store_true", help="emit a content-free JSON plan")

    apply = commands.add_parser("apply")
    _workspace_options(apply)
    apply.add_argument("--yes", action="store_true", help="confirm installation without prompting")

    verify = commands.add_parser("verify")
    _workspace_options(verify)
    verify.add_argument("--require-runtime", action="store_true", help="treat runtime warnings as failures")

    rollback = commands.add_parser("rollback")
    _workspace_options(rollback)
    rollback.add_argument("--backup", required=True, help="workspace-relative backup directory")
    return parser


def _workspace_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace root")
    parser.add_argument("--config", type=Path, help="workspace configuration path")


def _paths(arguments: argparse.Namespace) -> tuple[Path, Path | None]:
    workspace = arguments.workspace.resolve()
    config = arguments.config
    if config is None:
        return workspace, workspace / "memory-system.toml"
    return workspace, config if config.is_absolute() else workspace / config


def _doctor(workspace: Path) -> int:
    report = run_doctor(load_compatibility(workspace / "compatibility.toml"))
    print(f"platform: {report.platform}")
    print(f"installed versions: {', '.join(report.installed_versions) or 'none'}")
    print(f"worker: {'reachable' if report.worker_reachable else 'unreachable'} on {report.worker_port}")
    for diagnostic in report.diagnostics:
        print(f"{diagnostic.severity}: {diagnostic.code}: {diagnostic.message}")
    return 2 if any(diagnostic.severity == "error" for diagnostic in report.diagnostics) else 0


def _apply(workspace: Path, config_path: Path | None, confirmed: bool) -> int:
    if not confirmed:
        if not sys.stdin.isatty():
            print("error: apply requires --yes in non-interactive use", file=sys.stderr)
            return 4
        confirmed = input("Apply memory-system plan? [y/N] ").strip().casefold() in {"y", "yes"}
        if not confirmed:
            print("error: installation was not confirmed", file=sys.stderr)
            return 4
    plan = build_plan(workspace, config_path)
    if plan.conflicts:
        print(format_plan(plan), end="")
        return 3
    result = apply_plan(plan, confirmed=True)
    if not result.changed_paths and not result.adopted_paths:
        print("No changes")
    else:
        for path in result.changed_paths:
            print(f"Changed {path}")
        for path in result.adopted_paths:
            print(f"Adopted {path}")
        if result.backup_dir is not None:
            print(f"Backup {result.backup_dir}")
    return 0


def _rollback(workspace: Path, backup: str) -> int:
    try:
        backup_dir = PurePosixPath(backup)
        result = rollback_installation(workspace, backup_dir)
    except (ApplyError, ValueError) as exc:
        print(f"error: invalid rollback metadata: {exc}", file=sys.stderr)
        return 6
    for path in result.changed_paths:
        print(f"Restored {path}")
    return 0
