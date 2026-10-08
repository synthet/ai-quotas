import json
import os
import subprocess
import time
from typing import Any

from app.util.local_cli import resolve_cli, subprocess_kwargs


def read_rate_limits(timeout_sec: float = 20.0) -> dict[str, Any]:
    """Call codex app-server account/rateLimits/read (official local CLI)."""
    exe = resolve_cli("codex")
    if not exe:
        raise FileNotFoundError("codex CLI not found")

    command = [exe, "app-server", "--stdio"]
    if os.name == "nt":
        command = [exe, "--no-daemon", "app-server", "--stdio"]

    proc = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        **subprocess_kwargs(),
    )
    assert proc.stdin and proc.stdout

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg, separators=(",", ":")) + "\n")
        proc.stdin.flush()

    send(
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "ai-quotas", "title": "AI Quotas", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True},
            },
        }
    )

    deadline = time.time() + timeout_sec
    initialized = False
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                err = ""
                if proc.stderr is not None:
                    err = (proc.stderr.read() or "").strip()[:300]
                raise RuntimeError(err or "codex app-server exited")
            time.sleep(0.05)
            continue
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 1 and "result" in msg:
            send({"method": "initialized", "params": {}})
            initialized = True
            send({"id": 2, "method": "account/rateLimits/read", "params": {}})
            continue
        if initialized and msg.get("id") == 2:
            proc.terminate()
            if "error" in msg:
                raise RuntimeError(str(msg["error"]))
            return msg.get("result") or msg

    proc.terminate()
    raise TimeoutError("codex app-server did not return rate limits")
