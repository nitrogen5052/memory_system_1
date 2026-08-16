from __future__ import annotations

import json
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory_system.config import Compatibility


def _runtime():
    return importlib.import_module("memory_system.runtime")


def _compatibility() -> Compatibility:
    return Compatibility(
        schema_version=1,
        methodology_version="1.0",
        minimum_claude_mem_version="13.15.0",
        tested_claude_mem_versions=("13.15.0",),
        default_worker_port=37700,
    )


def _settings(home: Path, data: dict[str, object]) -> None:
    settings = home / ".claude-mem" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps(data), encoding="utf-8")


@pytest.mark.parametrize(
    ("system", "release", "expected"),
    [
        ("darwin", "23.6.0", "macOS"),
        ("linux", "6.8.0", "Linux"),
        ("linux", "5.15.153.1-microsoft-standard-WSL2", "WSL"),
    ],
)
def test_doctor_reports_supported_host_platforms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, system: str, release: str, expected: str
) -> None:
    runtime = _runtime()
    monkeypatch.setattr(runtime.sys, "platform", system)
    monkeypatch.setattr(runtime.platform, "release", lambda: release)
    monkeypatch.setattr(runtime.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(runtime.os, "environ", {})
    monkeypatch.setattr(runtime.os, "getuid", lambda: 42)
    monkeypatch.setattr(runtime, "probe_worker", lambda port: False)

    report = runtime.run_doctor(_compatibility())

    assert report.platform == expected
    assert not any(diagnostic.code == "unsupported-platform" for diagnostic in report.diagnostics)


def test_doctor_rejects_native_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = _runtime()
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(runtime.os, "environ", {})
    monkeypatch.setattr(runtime, "probe_worker", lambda port: False)

    report = runtime.run_doctor(_compatibility())

    assert report.platform == "Windows"
    assert any(
        diagnostic.code == "unsupported-platform" and diagnostic.severity == "error"
        for diagnostic in report.diagnostics
    )


def test_data_dir_prefers_environment_over_settings(tmp_path: Path) -> None:
    runtime = _runtime()
    _settings(tmp_path, {"dataDir": "/from-settings"})

    data_dir = runtime.resolve_data_dir({"CLAUDE_MEM_DATA_DIR": "/from-environment"}, tmp_path)

    assert data_dir == Path("/from-environment")


def test_data_dir_uses_settings_then_default(tmp_path: Path) -> None:
    runtime = _runtime()
    _settings(tmp_path, {"dataDir": "/from-settings"})

    assert runtime.resolve_data_dir({}, tmp_path) == Path("/from-settings")

    (tmp_path / ".claude-mem" / "settings.json").unlink()
    assert runtime.resolve_data_dir({}, tmp_path) == tmp_path / ".claude-mem"


def test_worker_port_prefers_environment_then_settings_then_uid_fallback(tmp_path: Path) -> None:
    runtime = _runtime()
    data_dir = tmp_path / ".claude-mem"
    data_dir.mkdir()
    _settings(tmp_path, {"workerPort": 38123})

    assert runtime.resolve_worker_port({"CLAUDE_MEM_PORT": "39000"}, data_dir, 42) == 39000
    assert runtime.resolve_worker_port({}, data_dir, 42) == 38123

    (data_dir / "settings.json").unlink()
    assert runtime.resolve_worker_port({}, data_dir, 42) == 37742


def test_malformed_settings_do_not_prevent_safe_defaults(tmp_path: Path) -> None:
    runtime = _runtime()
    settings = tmp_path / ".claude-mem" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")

    assert runtime.resolve_data_dir({}, tmp_path) == settings.parent
    assert runtime.resolve_worker_port({}, settings.parent, 8) == 37708


def test_discovers_official_claude_mem_plugin_version(tmp_path: Path) -> None:
    runtime = _runtime()
    package = tmp_path / ".codex" / "plugins" / "cache" / "claude-mem" / "13.15.0" / "package.json"
    package.parent.mkdir(parents=True)
    package.write_text(json.dumps({"name": "claude-mem", "version": "13.15.0"}), encoding="utf-8")

    assert runtime.discover_installed_versions(tmp_path) == ("13.15.0",)


def test_worker_probe_accepts_only_json_http_200(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    seen: dict[str, object] = {}

    class Response:
        status = 200
        headers = {"Content-Type": "application/json; charset=utf-8"}

        def read(self) -> bytes:
            return b"{}"

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def urlopen(request: object, timeout: float) -> Response:
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(runtime.urllib.request, "urlopen", urlopen)

    assert runtime.probe_worker(37742) is True
    assert seen == {
        "url": "http://127.0.0.1:37742/api/search?query=%2A&limit=1",
        "timeout": 2.0,
    }


def test_worker_probe_rejects_http_200_with_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()

    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return b"not-json"

    monkeypatch.setattr(runtime.urllib.request, "urlopen", lambda request, timeout: Response())

    assert runtime.probe_worker(37742) is False


def test_worker_probe_reports_connection_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _runtime()
    def refused(request: object, timeout: float) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(runtime.urllib.request, "urlopen", refused)

    assert runtime.probe_worker(37742) is False


def test_doctor_warns_for_unreachable_untested_newer_version_and_invalid_compatibility(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = _runtime()
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.platform, "release", lambda: "6.8.0")
    monkeypatch.setattr(runtime.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(runtime.os, "environ", {})
    monkeypatch.setattr(runtime.os, "getuid", lambda: 1)
    monkeypatch.setattr(runtime, "discover_installed_versions", lambda home: ("13.16.0",))
    monkeypatch.setattr(runtime, "probe_worker", lambda port: False)

    report = runtime.run_doctor(_compatibility())

    assert [(diagnostic.code, diagnostic.severity) for diagnostic in report.diagnostics] == [
        ("untested-version", "warning"),
        ("worker-unreachable", "warning"),
    ]

    invalid = SimpleNamespace(
        schema_version=2,
        methodology_version="1.0",
        minimum_claude_mem_version="13.15.0",
        tested_claude_mem_versions=("13.15.0",),
        default_worker_port=37700,
    )
    invalid_report = runtime.run_doctor(invalid)
    assert any(
        diagnostic.code == "invalid-compatibility" and diagnostic.severity == "error"
        for diagnostic in invalid_report.diagnostics
    )


def test_doctor_rejects_python_below_3_11(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime = _runtime()
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.platform, "release", lambda: "6.8.0")
    monkeypatch.setattr(runtime.sys, "version_info", (3, 10, 14))
    monkeypatch.setattr(runtime.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(runtime.os, "environ", {})
    monkeypatch.setattr(runtime.os, "getuid", lambda: 1)
    monkeypatch.setattr(runtime, "probe_worker", lambda port: False)

    report = runtime.run_doctor(_compatibility())

    assert any(
        diagnostic.code == "python-too-old" and diagnostic.severity == "error"
        for diagnostic in report.diagnostics
    )
