import os
import socket
import subprocess
import sys
import uuid

import pytest

from app import tray


def test_port_in_use_detects_a_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        port = sock.getsockname()[1]
        assert tray.port_in_use("127.0.0.1", port) is True
    assert tray.port_in_use("127.0.0.1", port) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex")
def test_second_instance_is_refused():
    name = f"Local\\AIQuotasTest-{uuid.uuid4()}"
    assert tray.acquire_single_instance(name) is not None
    assert tray.acquire_single_instance(name) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPI awareness")
def test_enable_dpi_awareness_makes_the_process_per_monitor_aware():
    # A subprocess, because DPI awareness can be set only once per process.
    code = (
        "import ctypes; from app import tray; tray.enable_dpi_awareness(); "
        "u = ctypes.windll.user32; "
        "u.GetThreadDpiAwarenessContext.restype = ctypes.c_void_p; "
        "u.GetAwarenessFromDpiAwarenessContext.argtypes = [ctypes.c_void_p]; "
        "print(u.GetAwarenessFromDpiAwarenessContext(u.GetThreadDpiAwarenessContext()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tray.ROOT, capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "2"  # DPI_AWARENESS_PER_MONITOR_AWARE


def test_log_path_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert tray.log_path() == tmp_path / "AI Quotas" / "ai-quotas.log"
