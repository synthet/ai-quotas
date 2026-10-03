"""Allow the Dockerized dashboard to call the Windows Claude, Codex, and Antigravity CLIs.

Binds to 127.0.0.1 only. The exec endpoint accepts the quota commands this app
already runs, and nothing else. Codex app-server speaks over a separate TCP
port because that session is bidirectional stdio.
"""

from __future__ import annotations

import json
import os
import socket
import socketserver
import sqlite3
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HTTP_PORT = int(os.environ.get("HOST_CLI_BRIDGE_HTTP_PORT", "18788"))
CODEX_PORT = int(os.environ.get("HOST_CLI_BRIDGE_CODEX_PORT", "18789"))
ROOT = Path(__file__).resolve().parent.parent
CURSOR_AUTH = ROOT / ".docker" / "cursor-auth.json"
EXEC_TIMEOUT_SEC = 115
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Keep these in sync with the adapter command lines.
ALLOWED: dict[str, list[list[str]]] = {
    "claude": [["-p", "/usage", "--output-format", "json"]],
    "agy": [["-p", "/usage", "--output-format", "json"]],
}

_codex_lock = threading.Lock()


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _popen_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _latest_codex() -> Path | None:
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    if not root.is_dir():
        return None
    found = [path for path in root.glob("*/codex.exe") if path.is_file()]
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)


def resolve_bin(name: str) -> Path | None:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    home = Path.home()
    if name == "claude":
        candidate = home / ".local" / "bin" / "claude.exe"
        return candidate if candidate.is_file() else None
    if name == "agy":
        candidate = local / "agy" / "bin" / "agy.exe"
        return candidate if candidate.is_file() else None
    if name == "codex":
        return _latest_codex()
    return None


def _clip(text: str, limit: int = 200_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def run_allowed(bin_name: str, args: list[str]) -> tuple[int, str, str]:
    allowed = ALLOWED.get(bin_name)
    if allowed is None or args not in allowed:
        return 127, "", "command not allowed\n"
    exe = resolve_bin(bin_name)
    if exe is None:
        return 127, "", f"{bin_name} was not found on the Windows host\n"
    try:
        proc = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            timeout=EXEC_TIMEOUT_SEC,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            **_popen_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + "\ntimed out\n"
        if not isinstance(stdout, str):
            stdout = stdout.decode("utf-8", errors="replace")
        if not isinstance(stderr, str):
            stderr = stderr.decode("utf-8", errors="replace")
        return 124, _clip(stdout), _clip(stderr)
    return proc.returncode, _clip(proc.stdout or ""), _clip(proc.stderr or "")


def read_cursor_token() -> str | None:
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return None
    db_path = Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    if not db_path.is_file():
        return None
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    except sqlite3.Error as exc:
        log(f"cursor session database unavailable: {exc}")
        return None
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            ("cursorAuth/accessToken",),
        ).fetchone()
    except sqlite3.Error as exc:
        log(f"cursor session read failed: {exc}")
        return None
    finally:
        conn.close()
    if row is None or row[0] is None:
        return None
    value = row[0]
    if isinstance(value, bytes):
        token = value.decode("utf-8", errors="replace")
    else:
        token = str(value)
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] == '"':
        token = token[1:-1]
    return token or None


def write_in_place(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def refresh_cursor_auth() -> None:
    token = read_cursor_token()
    if token:
        write_in_place(CURSOR_AUTH, json.dumps({"accessToken": token}) + "\n")
        log("cursor session file updated")
        return
    if not CURSOR_AUTH.exists():
        write_in_place(CURSOR_AUTH, "{}\n")
    log("cursor session not read; keeping the previous file")


def _cursor_loop(stop: threading.Event) -> None:
    while not stop.wait(60):
        try:
            refresh_cursor_auth()
        except OSError as exc:
            log(f"cursor session file update failed: {exc}")


class ExecHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"ok": False})
            return
        self._send(200, {"ok": True})

    def do_POST(self) -> None:
        if self.path != "/v1/exec":
            self._send(404, {"code": 127, "stdout": "", "stderr": "not found\n"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 8192:
            self._send(400, {"code": 2, "stdout": "", "stderr": "invalid body\n"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode())
        except json.JSONDecodeError:
            self._send(400, {"code": 2, "stdout": "", "stderr": "invalid JSON\n"})
            return
        bin_name = data.get("bin")
        args = data.get("args")
        if not isinstance(bin_name, str) or not isinstance(args, list):
            self._send(400, {"code": 2, "stdout": "", "stderr": "invalid request\n"})
            return
        if not all(isinstance(item, str) for item in args):
            self._send(400, {"code": 2, "stdout": "", "stderr": "invalid args\n"})
            return
        code, stdout, stderr = run_allowed(bin_name, args)
        log(f"exec {bin_name} -> {code}")
        self._send(200, {"code": code, "stdout": stdout, "stderr": stderr})


class CodexHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        if not _codex_lock.acquire(blocking=False):
            log("codex bridge busy")
            return
        proc: subprocess.Popen | None = None
        try:
            exe = resolve_bin("codex")
            if exe is None:
                log("codex.exe was not found on the Windows host")
                return
            self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            proc = subprocess.Popen(
                [str(exe), "--no-daemon", "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **_popen_kwargs(),
            )
            assert proc.stdin and proc.stdout and proc.stderr

            def socket_to_proc() -> None:
                assert proc and proc.stdin
                try:
                    while True:
                        chunk = self.request.recv(65536)
                        if not chunk:
                            break
                        proc.stdin.write(chunk)
                        proc.stdin.flush()
                except Exception:
                    pass
                finally:
                    try:
                        proc.stdin.close()
                    except Exception:
                        pass

            def proc_to_socket() -> None:
                assert proc and proc.stdout
                try:
                    while True:
                        chunk = proc.stdout.read(65536)
                        if not chunk:
                            break
                        self.request.sendall(chunk)
                except Exception:
                    pass

            def drain_stderr() -> None:
                assert proc and proc.stderr
                logged = False
                try:
                    while True:
                        data = proc.stderr.read(65536)
                        if not data:
                            break
                        if not logged:
                            log("codex stderr: " + data.decode("utf-8", errors="replace")[:1000])
                            logged = True
                except Exception:
                    return

            threads = [
                threading.Thread(target=socket_to_proc, daemon=True),
                threading.Thread(target=proc_to_socket, daemon=True),
                threading.Thread(target=drain_stderr, daemon=True),
            ]
            for thread in threads:
                thread.start()
            threads[1].join()
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            try:
                self.request.close()
            except Exception:
                pass
            _codex_lock.release()


class CodexServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class BridgeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    CURSOR_AUTH.parent.mkdir(parents=True, exist_ok=True)
    log("reading Cursor session")
    try:
        refresh_cursor_auth()
    except OSError as exc:
        log(f"cursor session file update failed: {exc}")
        if not CURSOR_AUTH.exists():
            CURSOR_AUTH.write_text("{}\n", encoding="utf-8")

    try:
        codex_server = CodexServer(("127.0.0.1", CODEX_PORT), CodexHandler)
    except OSError as exc:
        log(f"failed to bind codex port {CODEX_PORT}: {exc}")
        sys.exit(1)
    try:
        httpd = BridgeHTTPServer(("127.0.0.1", HTTP_PORT), ExecHandler)
    except OSError as exc:
        log(f"failed to bind http port {HTTP_PORT}: {exc}")
        codex_server.server_close()
        sys.exit(1)

    stop = threading.Event()
    threading.Thread(target=_cursor_loop, args=(stop,), daemon=True).start()
    threading.Thread(target=codex_server.serve_forever, daemon=True).start()
    log(f"bridge listening on 127.0.0.1:{HTTP_PORT} and 127.0.0.1:{CODEX_PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.shutdown()
        codex_server.shutdown()


if __name__ == "__main__":
    main()
