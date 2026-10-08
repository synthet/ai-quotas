import os
import shutil
from pathlib import Path

from app.util.local_cli import resolve_cli


def test_claude_uses_known_install_before_path(tmp_path, monkeypatch):
    exe = tmp_path / "home" / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"x")
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: "from-path")
    assert resolve_cli("claude", home=tmp_path / "home", local_app_data=tmp_path) == str(exe)


def test_agy_falls_back_to_path(tmp_path, monkeypatch):
    def which(name: str) -> str | None:
        return "agy-on-path" if name == "agy" else None

    monkeypatch.setattr(shutil, "which", which)
    assert resolve_cli("agy", home=tmp_path, local_app_data=tmp_path / "missing") == "agy-on-path"


def test_codex_prefers_newest_versioned_binary(tmp_path):
    bin_root = tmp_path / "OpenAI" / "Codex" / "bin"
    older = bin_root / "aaa" / "codex.exe"
    newer = bin_root / "bbb" / "codex.exe"
    older.parent.mkdir(parents=True)
    newer.parent.mkdir()
    older.write_bytes(b"a")
    newer.write_bytes(b"b")
    os.utime(older, (1, 1_000))
    os.utime(newer, (1, 2_000))
    direct = bin_root / "codex.exe"
    direct.write_bytes(b"c")
    os.utime(direct, (1, 9_000))

    found = resolve_cli("codex", home=tmp_path / "home", local_app_data=tmp_path)
    assert Path(found) == newer
