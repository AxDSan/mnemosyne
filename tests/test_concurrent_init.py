"""Regression coverage for concurrent first-open schema migrations (#663)."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from mnemosyne.core.beam import _add_column_if_missing
from mnemosyne.core.memory import init_db


class RacingCursor:
    """Simulate another connection winning ALTER after our schema read."""

    def __init__(self, *, winner_adds_column=True):
        self.schema_reads = 0
        self.winner_adds_column = winner_adds_column

    def execute(self, sql):
        if sql.startswith("PRAGMA table_info"):
            self.schema_reads += 1
            return self
        if sql.startswith("ALTER TABLE"):
            raise sqlite3.OperationalError("duplicate column name: author_id")
        raise AssertionError(sql)

    def fetchall(self):
        if self.schema_reads == 1 or not self.winner_adds_column:
            return [(0, "id")]
        return [(0, "id"), (1, "author_id")]


class RacingConnection:
    def __init__(self, *, winner_adds_column=True):
        self._cursor = RacingCursor(winner_adds_column=winner_adds_column)

    def cursor(self):
        return self._cursor

    def commit(self):
        raise AssertionError("failed ALTER must not commit")


def test_add_column_tolerates_concurrent_winner():
    conn = RacingConnection()

    _add_column_if_missing(conn, "working_memory", "author_id", "TEXT")

    assert conn._cursor.schema_reads == 2


def test_add_column_reraises_when_column_is_still_missing():
    conn = RacingConnection(winner_adds_column=False)

    with pytest.raises(sqlite3.OperationalError, match="duplicate column"):
        _add_column_if_missing(conn, "working_memory", "author_id", "TEXT")


def test_concurrent_first_open_completes_for_all_callers(tmp_path):
    db_path = tmp_path / "concurrent-first-open.db"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: init_db(db_path), range(8)))

    assert results == [None] * 8
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(working_memory)")}
    conn.close()
    assert {"author_id", "author_type", "channel_id"} <= columns
