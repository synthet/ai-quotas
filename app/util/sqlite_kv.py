import sqlite3
from pathlib import Path


def read_item_table_value(db_path: Path, key: str) -> str | None:
    if not db_path.exists():
        return None
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        value = row[0]
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)
    finally:
        conn.close()
