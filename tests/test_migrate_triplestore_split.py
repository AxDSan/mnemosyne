"""
Tests for scripts/migrate_triplestore_split.py (E6).

Verifies:
- Annotation-flavored rows move from `triples` to `annotations`
- Temporal / non-annotation rows are left in `triples` untouched
- Idempotent re-run is a no-op
- Empty DB / fresh install is a no-op
- File-level backup is written by default
- Dry-run reports counts without writing
"""

import importlib.util
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Load the migration script as a module so we can call migrate() directly.
_MIGRATE_SCRIPT_PATH = (
    Path(__file__).parent.parent / "scripts" / "migrate_triplestore_split.py"
)
spec = importlib.util.spec_from_file_location(
    "migrate_triplestore_split", _MIGRATE_SCRIPT_PATH
)
migrate_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrate_module)

from mnemosyne.core.triples import TripleStore
from mnemosyne.core.annotations import AnnotationStore


class TestMigrateTripleStoreSplit(unittest.TestCase):
    def setUp(self):
        # Use a fresh temp DB per test
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        # Collected log lines from the migration's log_fn for assertions.
        self.logs: list[str] = []

    def tearDown(self):
        for suffix in ("", ".pre_e6_backup"):
            p = Path(str(self.tmp.name) + suffix)
            try:
                os.unlink(p)
            except OSError:
                pass

    def _log(self, line: str) -> None:
        self.logs.append(line)

    def _seed_legacy_triples(self, rows: list[tuple]) -> None:
        """Seed the triples table directly with annotation- and/or
        temporal-flavored rows.

        rows: list of (subject, predicate, object, source, confidence)
        """
        # Initialize the schema by instantiating TripleStore — it ensures
        # the triples table exists with the expected columns.
        store = TripleStore(db_path=self.db_path)
        cursor = store.conn.cursor()
        for subject, predicate, object_, source, confidence in rows:
            cursor.execute(
                """
                INSERT INTO triples
                    (subject, predicate, object, valid_from, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    subject,
                    predicate,
                    object_,
                    datetime.now().isoformat()[:10],
                    source,
                    confidence,
                ),
            )
        store.conn.commit()
        store.conn.close()

    def _count_annotations(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        try:
            cursor = conn.execute("SELECT COUNT(*) FROM annotations")
            return cursor.fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()

    def _annotations_rows(self) -> list[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM annotations ORDER BY id")
            return [dict(r) for r in cursor.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def _triples_rows(self) -> list[dict]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute("SELECT * FROM triples ORDER BY id")
            return [dict(r) for r in cursor.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Migration moves annotation-flavored rows
    # ------------------------------------------------------------------

    def test_migrates_mentions_and_facts(self):
        self._seed_legacy_triples(
            [
                ("mem-1", "mentions", "Alice", "extraction", 0.9),
                ("mem-1", "mentions", "Bob", "extraction", 0.9),
                ("mem-1", "fact", "The user enjoys coffee", "test", 0.7),
                ("mem-2", "occurred_on", "2026-01-15", "ingest", 1.0),
                ("mem-3", "has_source", "tool:cron", "ingest", 1.0),
            ]
        )

        written = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )

        self.assertEqual(written, 5)
        self.assertEqual(self._count_annotations(), 5)

        rows = self._annotations_rows()
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r["kind"], []).append(r)
        self.assertEqual(len(by_kind["mentions"]), 2)
        self.assertEqual(len(by_kind["fact"]), 1)
        self.assertEqual(len(by_kind["occurred_on"]), 1)
        self.assertEqual(len(by_kind["has_source"]), 1)

        # Both mentions for mem-1 preserved — the silent-destruction
        # bug is fixed for the migrated data.
        mention_values = {r["value"] for r in by_kind["mentions"]}
        self.assertEqual(mention_values, {"Alice", "Bob"})

    def test_leaves_non_annotation_triples_in_place(self):
        """Rows with predicates outside ANNOTATION_KINDS stay in `triples`."""
        self._seed_legacy_triples(
            [
                ("user", "prefers", "concise responses", "stated", 1.0),
                ("Maya", "assigned_to", "auth-migration", "stated", 1.0),
                ("mem-1", "mentions", "Alice", "extraction", 0.9),
            ]
        )

        written = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )

        self.assertEqual(written, 1)  # only the "mentions" row migrated

        # triples still has all 3 rows — we don't delete source rows.
        self.assertEqual(len(self._triples_rows()), 3)

        # annotations has only the mention.
        anns = self._annotations_rows()
        self.assertEqual(len(anns), 1)
        self.assertEqual(anns[0]["kind"], "mentions")

    def test_preserves_source_and_confidence(self):
        self._seed_legacy_triples(
            [("mem-1", "fact", "Some fact long enough", "extract-v1", 0.65)]
        )

        migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )

        rows = self._annotations_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "extract-v1")
        self.assertEqual(rows[0]["confidence"], 0.65)

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def test_rerun_is_noop_after_first_migration(self):
        self._seed_legacy_triples(
            [
                ("mem-1", "mentions", "Alice", "extraction", 0.9),
                ("mem-1", "mentions", "Bob", "extraction", 0.9),
            ]
        )

        first = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )
        self.assertEqual(first, 2)

        second = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )
        self.assertEqual(second, 0)

        # Count unchanged
        self.assertEqual(self._count_annotations(), 2)

    def test_rerun_after_new_annotation_added_post_migration(self):
        """If a new annotation-predicate row appears in triples after the
        first migration, a second run should pick it up but not re-migrate
        the original rows."""
        self._seed_legacy_triples(
            [("mem-1", "mentions", "Alice", "extraction", 0.9)]
        )
        migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )
        self.assertEqual(self._count_annotations(), 1)

        # Simulate a legacy caller writing a new annotation row to triples
        # after the migration ran (e.g., an external script using add_facts
        # during the deprecation period).
        conn = sqlite3.connect(str(self.db_path))
        conn.execute(
            """
            INSERT INTO triples
                (subject, predicate, object, valid_from, source, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("mem-1", "mentions", "Bob", "2026-05-10", "extraction", 0.9),
        )
        conn.commit()
        conn.close()

        # Re-run the migration; should pick up only the new row.
        written = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )
        self.assertEqual(written, 1)
        self.assertEqual(self._count_annotations(), 2)

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_empty_database_is_noop(self):
        """Fresh install — no triples table — should run cleanly."""
        # Create empty DB file (no schema yet)
        sqlite3.connect(str(self.db_path)).close()

        written = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )
        self.assertEqual(written, 0)

    def test_no_annotation_rows_is_noop(self):
        """triples table exists with only current-truth rows — nothing to migrate."""
        self._seed_legacy_triples(
            [("user", "prefers", "concise", "stated", 1.0)]
        )
        written = migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )
        self.assertEqual(written, 0)
        self.assertEqual(self._count_annotations(), 0)

    # ------------------------------------------------------------------
    # Dry-run and backup
    # ------------------------------------------------------------------

    def test_dry_run_reports_count_without_writing(self):
        self._seed_legacy_triples(
            [
                ("mem-1", "mentions", "Alice", "extraction", 0.9),
                ("mem-1", "fact", "Some fact long enough", "test", 0.7),
            ]
        )

        written = migrate_module.migrate(
            db_path=self.db_path, dry_run=True, backup=False, log_fn=self._log
        )

        self.assertEqual(written, 2)  # reports the count
        self.assertEqual(self._count_annotations(), 0)  # but doesn't write

    def test_backup_creates_pre_e6_file(self):
        self._seed_legacy_triples(
            [("mem-1", "mentions", "Alice", "extraction", 0.9)]
        )

        migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=True, log_fn=self._log
        )

        backup_path = Path(str(self.db_path) + ".pre_e6_backup")
        self.assertTrue(
            backup_path.exists(),
            "Backup file should be written with .pre_e6_backup suffix",
        )

    def test_backup_does_not_overwrite_existing(self):
        """If a backup already exists (e.g., earlier failed migration),
        don't overwrite it — the existing backup is likely closer to
        the user's original state."""
        self._seed_legacy_triples(
            [("mem-1", "mentions", "Alice", "extraction", 0.9)]
        )

        # Pre-create a backup file with sentinel content
        backup_path = Path(str(self.db_path) + ".pre_e6_backup")
        backup_path.write_bytes(b"existing backup content")

        migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=True, log_fn=self._log
        )

        # Backup contents unchanged
        self.assertEqual(backup_path.read_bytes(), b"existing backup content")

    def test_no_backup_flag_skips_file(self):
        self._seed_legacy_triples(
            [("mem-1", "mentions", "Alice", "extraction", 0.9)]
        )

        migrate_module.migrate(
            db_path=self.db_path, dry_run=False, backup=False, log_fn=self._log
        )

        backup_path = Path(str(self.db_path) + ".pre_e6_backup")
        self.assertFalse(backup_path.exists())


class TestMigrationViaCLI(unittest.TestCase):
    """Exercise the argparse / main() entry point."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

        # Seed
        store = TripleStore(db_path=self.db_path)
        store.conn.execute(
            """
            INSERT INTO triples
                (subject, predicate, object, valid_from, source, confidence)
            VALUES ('mem-1', 'mentions', 'Alice', '2026-05-10', 'test', 1.0)
            """
        )
        store.conn.commit()
        store.conn.close()

    def tearDown(self):
        for suffix in ("", ".pre_e6_backup"):
            try:
                os.unlink(str(self.tmp.name) + suffix)
            except OSError:
                pass

    def test_main_returns_0_on_success(self):
        rc = migrate_module.main(
            ["--db", str(self.db_path), "--no-backup"]
        )
        self.assertEqual(rc, 0)

    def test_main_returns_2_on_dry_run_with_pending_work(self):
        rc = migrate_module.main(
            ["--db", str(self.db_path), "--dry-run", "--no-backup"]
        )
        self.assertEqual(rc, 2)

    def test_main_returns_1_on_missing_db(self):
        rc = migrate_module.main(["--db", "/nonexistent/path.db"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
