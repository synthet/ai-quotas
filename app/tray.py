"""Windows tray host: serves the dashboard with no console and no main window.

Run with `pythonw -m app.tray`. uvicorn runs on a background thread because the
tray icon needs the main thread for its message loop.
"""

from __future__ import annotations

import ctypes
import logging
import os
import socket
import sys
import tempfile
import threading
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = "127.0.0.1"
PORT = 8787
URL = f"http://{HOST}:{PORT}"
APP_NAME = "AI Quotas"

_MUTEX_NAME = "Local\\AIQuotasTray"
_ERROR_ALREADY_EXISTS = 183
_MB_ICONERROR = 0x10
_SHUTDOWN_TIMEOUT_SEC = 10

log = logging.getLogger("ai_quotas.tray")


def log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / APP_NAME / "ai-quotas.log"


def _configure_logging() -> None:
    """Log to a file: pythonw has no stdout, and uvicorn's default config needs one."""
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def acquire_single_instance(name: str = _MUTEX_NAME) -> int | None:
    """Return a mutex handle to hold for the process lifetime, or None if already running."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    handle = kernel32.CreateMutexW(None, 0, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        return None
    return handle


def port_in_use(host: str = HOST, port: int = PORT) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return True
    return False


def _show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, APP_NAME, _MB_ICONERROR)


def _run_tray() -> int:
    # Imported here so the failure dialog in main() also covers missing packages.
    import pystray
    import uvicorn
    from PIL import Image

    from app.main import app

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=HOST,
            port=PORT,
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=5,
        )
    )
    quit_requested = threading.Event()

    def open_dashboard(_icon=None, _item=None) -> None:
        webbrowser.open(URL)

    def quit_app(icon, _item=None) -> None:
        quit_requested.set()
        server.should_exit = True
        icon.stop()

    # copy() reads the file fully so the handle closes; a held .ico blocks git on Windows.
    with Image.open(ROOT / "assets" / "ai-quotas.ico") as source:
        image = source.copy()

    icon = pystray.Icon(
        "ai-quotas",
        image,
        APP_NAME,
        pystray.Menu(
            pystray.MenuItem("Open dashboard", open_dashboard, default=True),
            pystray.MenuItem("Quit", quit_app),
        ),
    )

    def serve() -> None:
        try:
            server.run()
        except BaseException:  # uvicorn raises SystemExit when startup fails
            log.exception("Server stopped")
        finally:
            icon.stop()

    # Daemon: a collector blocking the event loop must not hold up Quit.
    thread = threading.Thread(target=serve, name="uvicorn", daemon=True)

    def setup(tray_icon) -> None:
        tray_icon.visible = True
        thread.start()

    icon.run(setup=setup)
    thread.join(_SHUTDOWN_TIMEOUT_SEC)

    if not quit_requested.is_set():
        _show_error(f"The dashboard stopped unexpectedly.\n\nSee {log_path()}")
        return 1
    return 0


def main() -> int:
    # Settings read .env relative to the working directory.
    os.chdir(ROOT)
    _configure_logging()
    try:
        mutex = acquire_single_instance()
        if mutex is None:
            webbrowser.open(URL)
            return 0
        if port_in_use():
            _show_error(f"Port {PORT} is already in use, so the dashboard cannot start.")
            return 1
        return _run_tray()
    except Exception:
        log.exception("Tray failed")
        _show_error(f"AI Quotas could not start.\n\nSee {log_path()}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
