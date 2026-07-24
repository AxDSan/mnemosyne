"""Regression tests for the optional memory browser helpers."""

import sqlite3
from pathlib import Path

import pytest

from mnemosyne.core.banks import BankManager
from mnemosyne.integrations import memory_browser


def test_build_html_renders_css_template_without_format_key_error():
    html = memory_browser._build_html([])

    assert "<style>" in html
    assert ":root {" in html
    assert "No memories found." in html


def test_resolve_db_path_matches_bank_manager_for_default_and_named_banks(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path))
    manager = BankManager(data_dir=tmp_path)

    assert Path(memory_browser._resolve_db_path("default")) == manager.get_bank_db_path("default")
    assert Path(memory_browser._resolve_db_path("work")) == manager.get_bank_db_path("work")


@pytest.mark.parametrize("bank", ["", "../escape", "work/name"])
def test_resolve_db_path_rejects_invalid_bank_names(bank):
    with pytest.raises(ValueError):
        memory_browser._resolve_db_path(bank)


@pytest.mark.parametrize("statement", ["INSERT INTO items VALUES (1)", "CREATE TABLE extra (id INTEGER)"])
def test_existing_database_connection_rejects_writes(tmp_path, statement):
    db_path = tmp_path / "existing.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE items (id INTEGER)")

    conn = memory_browser._get_connection(str(db_path))
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(statement)
    finally:
        conn.close()


def test_missing_database_stats_does_not_create_a_database_file(tmp_path):
    missing_db = tmp_path / "missing.db"

    stats = memory_browser.get_memory_stats(str(missing_db))

    assert "error" in stats
    assert not missing_db.exists()
