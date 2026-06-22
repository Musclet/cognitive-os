"""Cognitive OS — Windows one-click cloud UI launcher.

Usage:
    pythonw scripts/launch_web_ui.pyw

By default the shortcut opens the shared cloud UI. Set
``COGNITIVE_OS_LAUNCH_MODE=local`` for local development.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error
import webbrowser
from pathlib import Path

# ── Project root detection ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WEB_DIR = PROJECT_ROOT / "web"
LOGS_DIR = PROJECT_ROOT / "logs" / "launcher"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
PIDS_FILE = RUNTIME_DIR / "launcher-pids.json"
DEFAULT_CLOUD_URL = "https://cognitive-os.onrender.com/app/"

# ── Logging ────────────────────────────────────────────────────────────

LOGS_DIR.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "launcher.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("launcher")


# ── Helpers ────────────────────────────────────────────────────────────


def _port_in_use(port: int) -> bool:
    """Check if a TCP port is already listening (tries IPv4 and IPv6).

    Logs each attempt so the user can see which address succeeded.
    """
    for family, addr, label in (
        (socket.AF_INET, "127.0.0.1", "IPv4"),
        (socket.AF_INET6, "::1", "IPv6"),
    ):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(2)
                if s.connect_ex((addr, port)) == 0:
                    logger.info("TCP %s %s:%d — CONNECTED", label, addr, port)
                    return True
                else:
                    logger.debug("TCP %s %s:%d — refused", label, addr, port)
        except OSError as exc:
            logger.debug("TCP %s %s:%d — %s", label, addr, port, exc)
            continue
    logger.debug("TCP check :%d — no listener on 127.0.0.1 or ::1", port)
    return False


_FRONTEND_READY_URLS = [
    "http://127.0.0.1:5173/",
    "http://localhost:5173/",
    "http://[::1]:5173/",
    "http://127.0.0.1:5173/app/",
    "http://localhost:5173/app/",
    "http://[::1]:5173/app/",
]


def _http_ready(urls: list[str] | None = None, timeout: float = 2.0) -> tuple[bool, str]:
    """Try HTTP GET on each URL; return (True, successful_url) on first response.

    HTTP status codes 200, 301, 302, 404 are treated as "server is up".
    Connection refused / timeout are treated as "not ready".
    """
    candidates = urls if urls is not None else _FRONTEND_READY_URLS
    last_error = ""
    for url in candidates:
        logger.debug("HTTP check: %s", url)
        try:
            req = urllib.request.Request(url, method="GET")
            resp = urllib.request.urlopen(req, timeout=timeout)
            logger.info("HTTP %d from %s — READY", resp.status, url)
            return True, url
        except urllib.error.HTTPError as e:
            logger.info("HTTP %d from %s — READY (error page is ok)", e.code, url)
            return True, url
        except OSError as exc:
            last_error = f"{url}: {exc}"
            logger.debug("HTTP %s — %s", url, exc)
            continue
        except Exception as exc:
            last_error = f"{url}: {exc}"
            logger.debug("HTTP %s — %s", url, exc)
            continue
    logger.error("HTTP readiness check failed for all %d URLs: %s", len(candidates), last_error)
    return False, ""


def _check_python() -> str:
    """Return the python executable path or raise."""
    current_executable = Path(sys.executable)
    if current_executable.name.lower() == "pythonw.exe":
        console_executable = current_executable.with_name("python.exe")
        if console_executable.is_file():
            return str(console_executable)

    for name in ("python", "python3"):
        exe = shutil_which(name)
        if exe:
            return exe
    raise RuntimeError("Python not found. Please install Python and ensure it is on your PATH.")


def _check_node() -> str:
    """Return the node executable path or raise."""
    exe = shutil_which("node")
    if not exe:
        raise RuntimeError("Node.js not found. Please install Node.js and ensure it is on your PATH.")
    return exe


def _check_npm() -> str:
    """Return the npm executable path or raise."""
    exe = shutil_which("npm")
    if not exe:
        raise RuntimeError("npm not found. Please install Node.js (includes npm).")
    return exe


def shutil_which(name: str) -> str | None:
    """Minimal which() without requiring shutil."""
    path = os.environ.get("PATH", "")
    pathext = os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM")
    for directory in path.split(os.pathsep):
        for ext in pathext.split(";"):
            full = Path(directory) / (name + ext.lower())
            if full.exists():
                return str(full)
            full2 = Path(directory) / name
            if full2.exists() and full2.suffix.lower() in {".exe", ".cmd", ".bat", ".com"}:
                return str(full2)
    if name == "python" and sys.executable:
        return sys.executable
    return None


def _ensure_npm_installed() -> None:
    """Check web/node_modules exists; if not, guide the user."""
    node_modules = WEB_DIR / "node_modules"
    if node_modules.is_dir():
        return
    logger.warning("web/node_modules not found.")
    msg = (
        "Frontend dependencies not installed.\n\n"
        f"Please run:\n\n"
        f"  cd {WEB_DIR}\n"
        f"  npm install\n\n"
        "Then launch Cognitive OS again."
    )
    logger.error(msg)
    _show_message_box("依赖未安装 - Cognitive OS", msg)
    sys.exit(1)


def _show_message_box(title: str, message: str) -> None:
    """Show a Windows message box via PowerShell."""
    encoded = message.replace('"', '\\"').replace("'", "''")
    ps_cmd = f'Add-Type -AssemblyName PresentationFramework;[System.Windows.MessageBox]::Show("{encoded}","{title}","OK","Information")'
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass


def _launcher_setting(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return value.strip()
    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, raw_value = stripped.split("=", 1)
                if key.strip() == name:
                    return raw_value.strip().strip("\"'")
        except OSError:
            pass
    return default


def _read_pids() -> dict[str, int]:
    """Read previously launched PIDs from the runtime file."""
    if PIDS_FILE.exists():
        try:
            return json.loads(PIDS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_pids(pids: dict[str, int]) -> None:
    """Write PIDs to the runtime file."""
    PIDS_FILE.write_text(json.dumps(pids, indent=2), encoding="utf-8")


def _start_backend(python_exe: str) -> subprocess.Popen | None:
    """Start the backend if not already running on port 8081."""
    if _port_in_use(8081):
        logger.info("Backend already running on :8081 (reusing)")
        return None

    log_file = LOGS_DIR / "backend.log"
    logger.info("Starting backend: %s scripts/run.py", python_exe)
    proc = subprocess.Popen(
        [python_exe, str(SCRIPTS_DIR / "run.py")],
        cwd=PROJECT_ROOT,
        stdout=open(log_file, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    logger.info("Backend started (PID=%d)", proc.pid)
    return proc


def _start_frontend(npm_exe: str) -> subprocess.Popen | None:
    """Start the frontend dev server if not already running on port 5173."""
    if _port_in_use(5173):
        logger.info("Frontend already running on :5173 (reusing)")
        return None

    log_file = LOGS_DIR / "frontend.log"
    # Clear the frontend log for a fresh start
    log_file.write_text("", encoding="utf-8")
    # Force Vite to bind IPv4 (127.0.0.1) to avoid ::1-only mismatch
    cmd = [npm_exe, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"]
    logger.info("Starting frontend: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=WEB_DIR,
        stdout=open(log_file, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=True,
    )
    logger.info("Frontend started (PID=%d), binding 127.0.0.1:5173", proc.pid)
    return proc


def _wait_for_service(name: str, port: int, timeout: int = 60, http_urls: list[str] | None = None) -> bool:
    """Wait up to `timeout` seconds for a service to start listening.

    For the frontend, also performs HTTP readiness checks on multiple URLs
    to confirm the Vite dev server is actually serving content.
    """
    logger.info("Waiting for %s on :%d (timeout=%ds) ...", name, port, timeout)
    deadline = time.monotonic() + timeout
    http_checked = False
    while time.monotonic() < deadline:
        if not _port_in_use(port):
            time.sleep(1)
            continue
        # Port is open — for frontend, also verify HTTP readiness
        if http_urls and not http_checked:
            ready, url = _http_ready(http_urls, timeout=3)
            if ready:
                logger.info("%s is ready — HTTP response from %s", name, url)
                return True
            else:
                logger.warning(
                    "Port :%d is listening but HTTP not ready yet; retrying ...", port
                )
                time.sleep(2)
                http_checked = False
                continue
        logger.info("%s is ready on :%d", name, port)
        return True
    logger.error("%s did not start within %d seconds", name, timeout)
    return False


# ── Main ───────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("=" * 50)
    logger.info("Cognitive OS Launcher")
    logger.info("Project root: %s", PROJECT_ROOT)
    logger.info("=" * 50)

    launch_mode = _launcher_setting("COGNITIVE_OS_LAUNCH_MODE", "cloud").lower()
    if launch_mode != "local":
        url = _launcher_setting("COGNITIVE_OS_CLOUD_URL", DEFAULT_CLOUD_URL)
        logger.info("Opening shared cloud UI: %s", url)
        webbrowser.open(url)
        return

    # 1. Check prerequisites
    python_exe = _check_python()
    npm_exe = _check_npm()
    _ensure_npm_installed()

    # 2. Clean up stale processes from previous launches
    stale_pids = _read_pids()
    for key in ("frontend_pid", "backend_pid"):
        pid = stale_pids.get(key)
        if pid:
            try:
                os.kill(pid, 9)
                logger.info("Cleaned up stale %s (PID=%d)", key, pid)
            except OSError:
                pass  # process already gone

    # 3. Check ports — if both services are running, just open browser
    backend_alive = _port_in_use(8081)
    frontend_alive = _port_in_use(5173)

    # 4. Start backend
    backend_proc = _start_backend(python_exe)
    if backend_proc:
        _write_pids({"backend_pid": backend_proc.pid})
        if not _wait_for_service("backend", 8081, timeout=90):
            logger.error("Backend failed to start. Check logs/launcher/backend.log")
            _show_message_box(
                "启动失败 - Cognitive OS",
                "后端服务启动超时。\n\n请检查 logs/launcher/backend.log 获取详细信息。",
            )
            sys.exit(1)

    # 4. Start frontend
    frontend_proc = _start_frontend(npm_exe)
    if frontend_proc:
        pids = _read_pids()
        pids["frontend_pid"] = frontend_proc.pid
        _write_pids(pids)
        if not _wait_for_service("frontend", 5173, timeout=60, http_urls=_FRONTEND_READY_URLS):
            logger.error("Frontend failed to start. Check logs/launcher/frontend.log")
            _show_message_box(
                "启动失败 - Cognitive OS",
                "前端服务启动超时。\n\n请检查 logs/launcher/frontend.log 获取详细信息。",
            )
            sys.exit(1)

    # 5. Open browser
    url = "http://localhost:5173/app/"
    logger.info("Opening browser: %s", url)
    webbrowser.open(url)

    logger.info("Launch complete.")
    logger.info("Web UI: %s", url)
    logger.info("Default PIN: 123456")
    logger.info("To stop: powershell -ExecutionPolicy Bypass -File scripts/stop_web_ui.ps1")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger.exception("Launcher failed: %s", exc)
        _show_message_box("启动失败 - Cognitive OS", f"启动器出错:\n\n{exc}")
        sys.exit(1)
