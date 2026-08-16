"""Read-only host and local claude-mem runtime diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import sys
from typing import Mapping
import urllib.error
import urllib.request

from .config import Compatibility


_DEFAULT_DATA_DIR_NAME = ".claude-mem"
_DEFAULT_PORT = 37700
_SUPPORTED_SCHEMA_VERSION = 1
_SUPPORTED_METHODOLOGY_VERSION = "1.0"
_OFFICIAL_PACKAGE_NAMES = frozenset({"claude-mem", "@thedotmack/claude-mem"})


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class DoctorReport:
    platform: str
    installed_versions: tuple[str, ...]
    worker_port: int
    worker_reachable: bool
    diagnostics: tuple[Diagnostic, ...]


def resolve_data_dir(environ: Mapping[str, str], home: Path) -> Path:
    """Resolve the configured data directory without modifying the filesystem."""
    configured = environ.get("CLAUDE_MEM_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    settings = _read_settings(home / _DEFAULT_DATA_DIR_NAME / "settings.json")
    configured = _string_setting(settings, "dataDir", "data_dir")
    if configured:
        return Path(configured).expanduser()
    return home / _DEFAULT_DATA_DIR_NAME


def resolve_worker_port(environ: Mapping[str, str], data_dir: Path, uid: int) -> int:
    """Resolve a local worker port, falling back to the per-user default."""
    port = _port(environ.get("CLAUDE_MEM_PORT"))
    if port is not None:
        return port
    settings = _read_settings(data_dir / "settings.json")
    for key in ("workerPort", "worker_port", "port"):
        port = _port(settings.get(key))
        if port is not None:
            return port
    return _DEFAULT_PORT + (uid % 100)


def discover_installed_versions(home: Path) -> tuple[str, ...]:
    """Return versions declared by official plugin cache package manifests."""
    manifests: list[Path] = []
    for root in (home / ".claude" / "plugins", home / ".codex" / "plugins" / "cache"):
        if root.is_dir():
            manifests.extend(root.glob("**/package.json"))
    versions: set[str] = set()
    for manifest in sorted(manifests):
        package = _read_package(manifest)
        if package is not None:
            versions.add(package)
    return tuple(sorted(versions, key=_version_key))


def probe_worker(port: int, timeout_seconds: float = 2.0) -> bool:
    """Probe exactly one loopback worker endpoint once."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/search?query=%2A&limit=1",
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            if response.status != 200 or "application/json" not in content_type.lower():
                return False
            json.loads(response.read())
            return True
    except (OSError, urllib.error.URLError, ValueError):
        return False


def run_doctor(compatibility: Compatibility) -> DoctorReport:
    """Collect local-only readiness diagnostics without changing host state."""
    diagnostics: list[Diagnostic] = []
    host_platform = _host_platform()
    if sys.version_info < (3, 11):
        diagnostics.append(Diagnostic("python-too-old", "error", "Python 3.11 or newer is required."))
    if host_platform == "Windows":
        diagnostics.append(
            Diagnostic("unsupported-platform", "error", "Native Windows is not supported; use WSL.")
        )
    if not _valid_compatibility(compatibility):
        diagnostics.append(
            Diagnostic("invalid-compatibility", "error", "Compatibility policy does not match schema 1 / methodology 1.0.")
        )

    home = Path.home()
    data_dir = resolve_data_dir(os.environ, home)
    uid = os.getuid() if hasattr(os, "getuid") else 0
    worker_port = resolve_worker_port(os.environ, data_dir, uid)
    versions = discover_installed_versions(home)
    if not versions:
        diagnostics.append(Diagnostic("claude-mem-not-found", "warning", "No installed official claude-mem version was discovered."))
    elif _valid_compatibility(compatibility):
        _add_version_diagnostics(diagnostics, versions, compatibility)

    reachable = probe_worker(worker_port)
    if not reachable:
        diagnostics.append(
            Diagnostic("worker-unreachable", "warning", "The local claude-mem worker is not reachable.")
        )
    return DoctorReport(host_platform, versions, worker_port, reachable, tuple(diagnostics))


def _host_platform() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform.startswith("linux"):
        return "WSL" if "microsoft" in platform.release().casefold() else "Linux"
    if sys.platform.startswith("win"):
        return "Windows"
    return sys.platform


def _read_settings(path: Path) -> Mapping[str, object]:
    try:
        with path.open(encoding="utf-8") as source:
            settings = json.load(source)
    except (OSError, json.JSONDecodeError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _string_setting(settings: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = settings.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _port(value: object) -> int | None:
    try:
        port = int(value) if not isinstance(value, bool) else 0
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _read_package(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("name") not in _OFFICIAL_PACKAGE_NAMES:
        return None
    version = data.get("version")
    return version if isinstance(version, str) and version else None


def _valid_compatibility(compatibility: Compatibility) -> bool:
    return (
        isinstance(compatibility, Compatibility)
        and compatibility.schema_version == _SUPPORTED_SCHEMA_VERSION
        and compatibility.methodology_version == _SUPPORTED_METHODOLOGY_VERSION
        and _port(compatibility.default_worker_port) is not None
        and bool(compatibility.minimum_claude_mem_version)
    )


def _add_version_diagnostics(
    diagnostics: list[Diagnostic], versions: tuple[str, ...], compatibility: Compatibility
) -> None:
    minimum = _version_key(compatibility.minimum_claude_mem_version)
    tested = {_version_key(version) for version in compatibility.tested_claude_mem_versions}
    latest_tested = max(tested, default=minimum)
    if any(_version_key(version) < minimum for version in versions):
        diagnostics.append(Diagnostic("version-too-old", "warning", "A discovered claude-mem version is below the supported minimum."))
    if any(_version_key(version) > latest_tested for version in versions):
        diagnostics.append(Diagnostic("untested-version", "warning", "A discovered claude-mem version is newer than the tested set."))


def _version_key(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for component in version.split("."):
        digits = "".join(character for character in component if character.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)
