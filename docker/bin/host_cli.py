#!/usr/bin/env python3
"""Stand-in for host CLIs. Forwards fixed quota commands to the Windows bridge."""

import json
import os
import select
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

NAME = Path(sys.argv[0]).name.lower()
if NAME.endswith(".exe"):
    NAME = NAME[:-4]

HOST = os.environ.get("HOST_CLI_BRIDGE_HOST", "host.docker.internal")
HTTP_PORT = os.environ.get("HOST_CLI_BRIDGE_HTTP_PORT", "8788")
CODEX_PORT = int(os.environ.get("HOST_CLI_BRIDGE_CODEX_PORT", "8789"))


def bridge_host() -> str:
    """Use IPv4 only; Docker Desktop may list unreachable IPv6 first."""
    try:
        infos = socket.getaddrinfo(
            HOST,
            int(HTTP_PORT),
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
        if infos:
            return infos[0][4][0]
    except OSError:
        pass
    return HOST


def proxy_exec() -> int:
    body = json.dumps({"bin": NAME, "args": sys.argv[1:]}).encode()
    target = bridge_host()
    req = urllib.request.Request(
        f"http://{target}:{HTTP_PORT}/v1/exec",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=150) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        sys.stderr.write(f"host CLI bridge rejected {NAME}: {detail}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"host CLI bridge error: {exc}\n")
        return 1

    sys.stdout.write(payload.get("stdout") or "")
    sys.stderr.write(payload.get("stderr") or "")
    return int(payload.get("code") or 0)


def proxy_codex() -> int:
    if sys.argv[1:] != ["app-server", "--stdio"]:
        sys.stderr.write("codex proxy only supports: codex app-server --stdio\n")
        return 2

    try:
        sock = socket.create_connection((bridge_host(), CODEX_PORT), timeout=30)
    except OSError as exc:
        sys.stderr.write(f"codex bridge unavailable: {exc}\n")
        return 1

    sock.settimeout(None)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    stdin_fd = sys.stdin.fileno()
    stdout = sys.stdout.buffer
    sock_fd = sock.fileno()
    stdin_open = True
    received = False
    try:
        # Multiplex on the main thread. Reading stdin from another thread
        # aborts the proxy before Codex can answer.
        while True:
            watch = [sock_fd]
            if stdin_open:
                watch.append(stdin_fd)
            readable, _, _ = select.select(watch, [], [])
            if sock_fd in readable:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received = True
                stdout.write(chunk)
                stdout.flush()
            if stdin_open and stdin_fd in readable:
                chunk = os.read(stdin_fd, 4096)
                if not chunk:
                    stdin_open = False
                    try:
                        sock.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                else:
                    sock.sendall(chunk)
    finally:
        try:
            sock.close()
        except OSError:
            pass
    if not received:
        sys.stderr.write("codex bridge closed without data\n")
        return 1
    return 0


def main() -> int:
    if NAME == "codex":
        return proxy_codex()
    if NAME in {"claude", "agy"}:
        return proxy_exec()
    sys.stderr.write(f"unknown CLI proxy name: {NAME}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
