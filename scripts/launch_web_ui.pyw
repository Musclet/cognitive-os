"""Cognitive OS — Windows one-click Web UI launcher.

Usage:
    pythonw scripts/launch_web_ui.pyw

Double-click the .pyw file (or a shortcut pointing to it) to start
the backend, frontend, and open the browser automatically.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

# ── Project root detection ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
WEB_DIR = PROJECT_ROOT / "web"
LOGS_DIR = PROJECT_ROOT / "logs" / "launcher"
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
PIDS_FILE = RUNTIME_DIR / "launcher-pids.json"

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
    """Check if a TCP port is already listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _check_python() -> str:
    """Return the python executable path or raise."""
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
    logger.info("Starting frontend: %s run dev", npm_exe)
    proc = subprocess.Popen(
        [npm_exe, "run", "dev"],
        cwd=WEB_DIR,
        stdout=open(log_file, "a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
        shell=True,
    )
    logger.info("Frontend started (PID=%d)", proc.pid)
    return proc


def _wait_for_service(name: str, port: int, timeout: int = 60) -> bool:
    """Wait up to `timeout` seconds for a service to start listening."""
    logger.info("Waiting for %s on :%d ...", name, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_in_use(port):
            logger.info("%s is ready on :%d", name, port)
            return True
        time.sleep(1)
    logger.error("%s did not start within %d seconds", name, timeout)
    return False


# ── Main ───────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("=" * 50)
    logger.info("Cognitive OS Launcher")
    logger.info("Project root: %s", PROJECT_ROOT)
    logger.info("=" * 50)

    # 1. Check prerequisites
    python_exe = _check_python()
    npm_exe = _check_npm()
    _ensure_npm_installed()

    # 2. Check ports — if both services are running, just open browser
    backend_alive = _port_in_use(8081)
    frontend_alive = _port_in_use(5173)

    # 3. Start backend
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
        if not _wait_for_service("frontend", 5173, timeout=60):
            logger.error("Frontend failed to start. Check logs/launcher/frontend.log")
            _show_message_box(
                "启动失败 - Cognitive OS",
                "前端服务启动超时。\n\n请检查 logs/launcher/frontend.log 获取详细信息。",
            )
            sys.exit(1)

    # 5. Open browser
    url = "http://localhost:5173/"
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
