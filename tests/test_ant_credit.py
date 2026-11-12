import asyncio
import os
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.models.quota import ProviderStatus
from app.providers.ant_credit_adapter import (
    AntCreditAdapter,
    classify_credit_probe,
    public_probe_error,
    run_credit_probe,
)


def test_classify_available_message():
    stdout = '{"type":"message","content":[{"type":"text","text":"p"}]}'
    assert classify_credit_probe(0, stdout, "") == "available"


def test_classify_unavailable_credit_error():
    assert classify_credit_probe(1, "", "credit balance is too low") == "unavailable"


def test_classify_unauthorized():
    assert classify_credit_probe(1, "", "authentication_error: x-api-key header is required") == "unauthorized"


def test_public_probe_error_redacts_keys():
    text = public_probe_error("failed sk-ant-api03-secretvalue")
    assert "secretvalue" not in text
    assert "sk-ant-…" in text


def test_skipped_when_ant_is_missing():
    adapter = AntCreditAdapter()
    with patch("app.providers.ant_credit_adapter.resolve_cli", return_value=None):
        snap = asyncio.run(adapter.fetch(None, Settings(quota_scope="cli")))
    assert snap.status == ProviderStatus.SKIPPED


def test_probe_unsets_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    captured: dict = {}

    async def fake_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["args"] = args
        proc = AsyncMock()
        proc.communicate = AsyncMock(
            return_value=(
                b'{"type":"message","content":[{"type":"text","text":"p"}]}',
                b"",
            )
        )
        proc.returncode = 0
        proc.kill = lambda: None
        proc.wait = AsyncMock()
        return proc

    with patch("app.providers.ant_credit_adapter.asyncio.create_subprocess_exec", fake_exec):
        code, stdout, _stderr = asyncio.run(run_credit_probe("ant", "claude-haiku-5-5", 5))

    assert code == 0
    assert "message" in stdout
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "sk-ant-secret" not in captured["args"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-secret"
