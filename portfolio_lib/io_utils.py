"""Atomic file-write helpers."""
import os
from pathlib import Path


def atomic_write_text(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically: write to .tmp then os.replace.

    Mirrors the pattern already used in loader.py:persist_watchlist_additions.
    Prevents partial-write corruption on process crash or concurrent access.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)
