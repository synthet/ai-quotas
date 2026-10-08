"""Find the Claude, Codex, and Antigravity CLIs installed on this PC."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_CREATE_NO_WINDOW = 0x08000000


def subprocess_kwargs() -> dict:
    """Hide the console window when a collector launches a CLI on Windows."""
    if os.name == "nt":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def resolve_cli(
    name: str,
    *,
    home: Path | None = None,
    local_app_data: Path | None = None,
) -> str | None:
    """Return the executable for `claude`, `agy`, or `codex`.

    Known install folders win over PATH so a desktop shortcut, which often has
    a short PATH, still reaches the signed-in CLIs.
    """
    home = Path.home() if home is None else home
    if local_app_data is None:
        raw = os.environ.get("LOCALAPPDATA", "")
        local_app_data = Path(raw) if raw else None

    found = _known_path(name, home, local_app_data)
    if found is not None:
        return str(found)
    for candidate in (name, f"{name}.exe", f"{name}.cmd"):
        on_path = shutil.which(candidate)
        if on_path:
            return on_path
    return None


def _known_path(name: str, home: Path, local_app_data: Path | None) -> Path | None:
    if name == "claude":
        candidate = home / ".local" / "bin" / "claude.exe"
        return candidate if candidate.is_file() else None
    if name == "agy":
        if local_app_data is None:
            return None
        candidate = local_app_data / "agy" / "bin" / "agy.exe"
        return candidate if candidate.is_file() else None
    if name == "codex":
        return _latest_codex(local_app_data)
    return None


def _latest_codex(local_app_data: Path | None) -> Path | None:
    if local_app_data is None:
        return None
    root = local_app_data / "OpenAI" / "Codex" / "bin"
    if not root.is_dir():
        return None
    found = [path for path in root.glob("*/codex.exe") if path.is_file()]
    if not found:
        direct = root / "codex.exe"
        if direct.is_file():
            found.append(direct)
    if not found:
        return None
    return max(found, key=lambda path: path.stat().st_mtime)
