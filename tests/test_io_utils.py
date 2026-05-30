"""Unit tests for portfolio_lib.io_utils."""
import os
from pathlib import Path

import pytest

from portfolio_lib.io_utils import atomic_write_text


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "output.json"
    atomic_write_text(target, '{"ok": true}')
    assert target.exists()
    assert target.read_text(encoding="utf-8") == '{"ok": true}'


def test_atomic_write_no_tmp_remnant(tmp_path: Path):
    target = tmp_path / "output.json"
    atomic_write_text(target, "data")
    tmp = target.with_suffix(target.suffix + ".tmp")
    assert not tmp.exists(), ".tmp file must not remain after a successful write"


def test_atomic_write_overwrites_existing(tmp_path: Path):
    target = tmp_path / "output.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
