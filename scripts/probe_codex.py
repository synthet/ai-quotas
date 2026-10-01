import json
import shutil
import subprocess
import time

exe = shutil.which("codex") or shutil.which("codex.cmd")
if not exe:
    raise SystemExit("codex not found")
p = subprocess.Popen(
    [exe, "app-server", "--stdio"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    bufsize=1,
)


def send(obj: dict) -> None:
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


send(
    {
        "jsonrpc": "2.0",
        "method": "initialize",
        "id": 1,
        "params": {
            "clientInfo": {"name": "ai-quotas", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        },
    }
)
time.sleep(1)
while True:
    line = p.stdout.readline()
    if not line:
        break
    print("LINE", line[:500])
    if '"id":1' in line.replace(" ", ""):
        break

send(
    {
        "jsonrpc": "2.0",
        "method": "account/rateLimits/read",
        "id": 2,
        "params": {"supportsLunaReserve": True},
    }
)
deadline = time.time() + 15
while time.time() < deadline:
    line = p.stdout.readline()
    if not line:
        time.sleep(0.1)
        continue
    if '"id":2' in line.replace(" ", "") or '"id": 2' in line:
        print("LIMITS", line[:4000])
        break
    if line.strip().startswith("{"):
        print("OTHER", line[:200])
p.terminate()
