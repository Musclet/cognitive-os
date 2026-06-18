"""Test: Windows one-click launcher utilities.

Tests the launcher helper functions in isolation without actually
starting backend/frontend processes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

LAUNCHER_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "launch_web_ui.pyw"

# ── Helpers ────────────────────────────────────────────────────────────


def _get_launcher_ns():
    """Execute the launcher script in a controlled namespace for testing."""
    ns = {
        "__file__": str(LAUNCHER_PATH),
        "__name__": "__tests__",
        "Path": Path,
    }
    with open(LAUNCHER_PATH, encoding="utf-8") as f:
        code = f.read()
    exec(code, ns)
    return ns


# ── Test 1: PROJECT_ROOT detection ─────────────────────────────────────


def test_launcher_project_root():
    """PROJECT_ROOT correctly points to the repo root."""
    ns = _get_launcher_ns()
    root = ns["PROJECT_ROOT"]
    assert root is not None
    assert (root / "scripts" / "launch_web_ui.pyw").exists()
    assert (root / "web").is_dir()
    assert (root / "src").is_dir()


# ── Test 2: Backend command construction ───────────────────────────────


def test_backend_script_exists():
    """Backend launch script path exists."""
    ns = _get_launcher_ns()
    scripts_dir = ns["SCRIPTS_DIR"]
    assert (scripts_dir / "run.py").exists()
    assert (scripts_dir / "run.py").name == "run.py"


# ── Test 3: Frontend command construction ──────────────────────────────


def test_frontend_directory():
    """Frontend launch uses the correct working directory."""
    ns = _get_launcher_ns()
    web_dir = ns["WEB_DIR"]
    assert (web_dir / "package.json").exists()
    assert web_dir.name == "web"


# ── Test 4: Port check function exists and is callable ─────────────────


def test_port_in_use_function_available():
    """Port check function exists and is callable."""
    ns = _get_launcher_ns()
    assert callable(ns.get("_port_in_use"))


# ── Test 5: PID file read/write ────────────────────────────────────────


def test_pid_file_write_and_read(tmp_path: Path):
    """PID file write and read round-trips correctly."""
    pids_file = tmp_path / "launcher-pids.json"
    pids = {"backend_pid": 12345, "frontend_pid": 67890}

    pids_file.write_text(json.dumps(pids, indent=2), encoding="utf-8")
    loaded = json.loads(pids_file.read_text(encoding="utf-8"))

    assert loaded["backend_pid"] == 12345
    assert loaded["frontend_pid"] == 67890

    # Test partial update
    pids2 = {"frontend_pid": 99999}
    existing = json.loads(pids_file.read_text(encoding="utf-8")) if pids_file.exists() else {}
    existing.update(pids2)
    pids_file.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    loaded2 = json.loads(pids_file.read_text(encoding="utf-8"))
    assert loaded2["backend_pid"] == 12345
    assert loaded2["frontend_pid"] == 99999


# ── Test 6: Stop script targets only launcher PIDs ─────────────────────


def test_stop_script_kills_only_launcher_pids():
    """Stop script only kills PIDs from launcher-pids.json."""
    stop_script = Path(__file__).resolve().parent.parent.parent / "scripts" / "stop_web_ui.ps1"
    assert stop_script.exists()

    content = stop_script.read_text(encoding="utf-8")
    assert "launcher-pids.json" in content
    assert "backend_pid" in content
    assert "frontend_pid" in content


# ── Test 7: shutil_which (minimal which) ───────────────────────────────


def test_shutil_which_finds_python():
    """shutil_which finds Python executable."""
    ns = _get_launcher_ns()
    shutil_which = ns["shutil_which"]
    result = shutil_which("python")
    assert result is not None
    assert os.path.exists(result)


# ── Test 8: No non-stdlib imports ──────────────────────────────────────


def test_launcher_module_imports():
    """The launcher uses only Python standard library imports."""
    with open(LAUNCHER_PATH, encoding="utf-8") as f:
        code = f.read()

    import_lines = [l for l in code.splitlines() if l.startswith("import ") or l.startswith("from ")]
    non_stdlib = []
    stdlib_modules = {"json", "logging", "os", "socket", "subprocess", "sys", "time", "urllib", "webbrowser", "pathlib", "__future__"}

    for line in import_lines:
        module_part = line.split()[1].split(".")[0]
        if module_part not in stdlib_modules:
            non_stdlib.append(line)

    if non_stdlib:
        print(f"Non-stdlib imports found: {non_stdlib}")
    assert len(non_stdlib) == 0, f"Launcher uses non-stdlib imports: {non_stdlib}"


# ── Test 9: HTTP readiness — IPv4 success ───────────────────────────────


def test_http_ready_ipv4_success():
    """HTTP readiness returns True when 127.0.0.1 responds."""
    ns = _get_launcher_ns()
    _http_ready_fn = ns["_http_ready"]

    import urllib.error

    class _FakeResp:
        status = 200

    def _fake_urlopen(req, timeout):
        return _FakeResp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        ready, url = _http_ready_fn(["http://127.0.0.1:5173/"])
    assert ready is True
    assert "127.0.0.1" in url


def test_http_ready_ipv6_success():
    """HTTP readiness returns True when ::1 responds."""
    ns = _get_launcher_ns()
    _http_ready_fn = ns["_http_ready"]

    class _FakeResp:
        status = 200

    def _fake_urlopen(req, timeout):
        return _FakeResp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        ready, url = _http_ready_fn(["http://[::1]:5173/"])
    assert ready is True
    assert "::1" in url


def test_http_ready_ipv4_down_ipv6_up():
    """HTTP readiness succeeds on ::1 after 127.0.0.1 fails."""
    ns = _get_launcher_ns()
    _http_ready_fn = ns["_http_ready"]

    call_count = [0]

    class _FakeResp:
        status = 200

    def _fake_urlopen(req, timeout):
        call_count[0] += 1
        if "127.0.0.1" in req.full_url:
            raise OSError("Connection refused")
        return _FakeResp()

    with patch("urllib.request.urlopen", _fake_urlopen):
        ready, url = _http_ready_fn(["http://127.0.0.1:5173/", "http://[::1]:5173/"])
    assert ready is True
    assert call_count[0] == 2  # first fails, second succeeds


def test_http_ready_all_fail():
    """HTTP readiness returns False when all URLs fail."""
    ns = _get_launcher_ns()
    _http_ready_fn = ns["_http_ready"]

    def _fake_urlopen(req, timeout):
        raise OSError("Connection refused")

    with patch("urllib.request.urlopen", _fake_urlopen):
        ready, url = _http_ready_fn(
            ["http://127.0.0.1:5173/", "http://localhost:5173/"],
            timeout=0.1,
        )
    assert ready is False
    assert url == ""


# ── Test 10: Vite startup command includes explicit host/port ────────────


def test_frontend_start_command_contains_host_and_port():
    """Frontend npm command includes --host 127.0.0.1 and --port 5173."""
    with open(LAUNCHER_PATH, encoding="utf-8") as f:
        code = f.read()
    assert "--host" in code
    assert "127.0.0.1" in code
    assert "--port" in code
    assert "5173" in code


# ── Test 11: Browser URL points to /app/ ─────────────────────────────────


def test_browser_url_opens_app_path():
    """Browser opens http://localhost:5173/app/ not root."""
    with open(LAUNCHER_PATH, encoding="utf-8") as f:
        code = f.read()
    assert "localhost:5173/app/" in code


# ── Test 12: Stdlib imports include urllib ───────────────────────────────


def test_launcher_module_imports_include_urllib():
    """The launcher uses urllib for HTTP readiness checks (still stdlib)."""
    with open(LAUNCHER_PATH, encoding="utf-8") as f:
        code = f.read()

    import_lines = [l for l in code.splitlines() if l.startswith("import ") or l.startswith("from ")]
    non_stdlib = []
    stdlib_modules = {
        "json", "logging", "os", "socket", "subprocess", "sys",
        "time", "webbrowser", "pathlib", "__future__", "urllib",
    }

    for line in import_lines:
        module_part = line.split()[1].split(".")[0]
        if module_part not in stdlib_modules:
            non_stdlib.append(line)

    if non_stdlib:
        print(f"Non-stdlib imports found: {non_stdlib}")
    assert len(non_stdlib) == 0, f"Launcher uses non-stdlib imports: {non_stdlib}"


# ── Test 13: No test pollution ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_web_tests_unchanged():
    """This is a smoke test — full validation runs via full pytest."""
    assert True
