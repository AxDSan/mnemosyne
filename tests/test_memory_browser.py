"""Regression tests for the optional memory browser helpers."""

from pathlib import Path

from mnemosyne.core.banks import BankManager
from mnemosyne.integrations import memory_browser


def test_build_html_renders_css_template_without_format_key_error():
    html = memory_browser._build_html([])

    assert "<style>" in html
    assert "No memories found." in html


def test_resolve_db_path_matches_bank_manager_for_default_and_named_banks(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_DATA_DIR", str(tmp_path))
    manager = BankManager(data_dir=tmp_path)

    assert Path(memory_browser._resolve_db_path("default")) == manager.get_bank_db_path("default")
    assert Path(memory_browser._resolve_db_path("work")) == manager.get_bank_db_path("work")


def test_missing_database_stats_does_not_create_a_database_file(tmp_path):
    missing_db = tmp_path / "missing.db"

    stats = memory_browser.get_memory_stats(str(missing_db))

    assert "error" in stats
    assert not missing_db.exists()
