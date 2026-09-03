"""Regression coverage for English day-first named dates (#907)."""

import sqlite3

import pytest

from mnemosyne.core.memory import Mnemosyne


@pytest.mark.parametrize(
    ("date_text", "expected_value"),
    [
        ("March 12, 1985", "March 12, 1985"),
        ("12 March 1985", "12 March 1985"),
        ("21st February 1999", "21st February 1999"),
        ("30 Dec 2018", "30 Dec 2018"),
    ],
)
def test_remember_extracts_complete_english_named_date(
    tmp_path, date_text, expected_value
):
    """The public remember path preserves either English named-date order."""
    db_path = tmp_path / "mnemosyne.db"
    memory = Mnemosyne(session_id="issue-907", db_path=db_path)
    try:
        memory.remember(
            f"The event happened on {date_text}.", source="user", extract=True
        )
    finally:
        memory.conn.close()

    with sqlite3.connect(db_path) as conn:
        values = [
            row[0]
            for row in conn.execute(
                "SELECT value FROM memoria_facts WHERE fact_type = 'date' "
                "AND key = 'named_date'"
            )
        ]

    assert values == [expected_value]
