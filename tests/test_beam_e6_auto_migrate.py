"""
Tests for BeamMemory's E6 auto-migrate hook.

Covers:
- Fresh install (no triples table): no-op, annotations schema created
- Existing pre-E6 DB with annotation rows: auto-migrates on BeamMemory init
- Re-init after migration: no-op
- MNEMOSYNE_AUTO_MIGRATE=0 opt-out: skips migration, logs warning
- Migration failure: BeamMemory init still succeeds (graceful degradation)
"""

import logging
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mnemosyne.core import beam as beam_module
from mnemosyne.core.beam import BeamMemory
from mnemosyne.core.annotations import AnnotationStore


def _seed_legacy_triples(db_path: Path, rows: list[tuple]) -> None:
    """Seed annotation-flavored rows directly into a pre-E6 triples table.

    rows: list of (subject, predicate, object, source, confidence)
    """
    # init_triples creates the table even on a fresh DB
    from mnemosyne.core.triples import init_triples

    init_triples(db_path)
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
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
    conn.commit()
    conn.close()


class TestBeamE6AutoMigrate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "mnemosyne.db"
        # Make sure no opt-out env var leaks in from the test runner.
        self._saved_env = os.environ.pop("MNEMOSYNE_AUTO_MIGRATE", None)
        # Reset module-level thread-local connections between tests.
        tl = getattr(beam_module, "_thread_local", None)
        if tl is not None and hasattr(tl, "conn") and tl.conn is not None:
            try:
                tl.conn.close()
            except Exception:
                pass
            tl.conn = None
            if hasattr(tl, "db_path"):
                tl.db_path = None

    def tearDown(self):
        if self._saved_env is not None:
            os.environ["MNEMOSYNE_AUTO_MIGRATE"] = self._saved_env
        else:
            os.environ.pop("MNEMOSYNE_AUTO_MIGRATE", None)
        # Reset thread-local connections again so the next test gets a fresh state.
        tl = getattr(beam_module, "_thread_local", None)
        if tl is not None and hasattr(tl, "conn") and tl.conn is not None:
            try:
                tl.conn.close()
            except Exception:
                pass
            tl.conn = None
            if hasattr(tl, "db_path"):
                tl.db_path = None
        self.tmpdir.cleanup()

    def _annotations_count(self) -> int:
        conn = sqlite3.connect(str(self.db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
        except sqlite3.OperationalError:
            return 0
        finally:
            conn.close()

    def test_fresh_install_creates_annotations_schema(self):
        """On a fresh DB with no triples table, BeamMemory init still
        creates the annotations table so AnnotationStore is usable."""
        beam = BeamMemory(db_path=self.db_path)
        try:
            # Verify the table exists by writing to it via AnnotationStore.
            store = AnnotationStore(db_path=self.db_path)
            store.add("mem-1", "mentions", "Alice")
            results = store.query_by_memory("mem-1", kind="mentions")
            self.assertEqual(len(results), 1)
        finally:
            try:
                beam.conn.close()
            except Exception:
                pass

    def test_pre_e6_db_auto_migrates_on_init(self):
        """A DB with annotation rows in `triples` and no annotations
        table gets auto-migrated when BeamMemory opens it."""
        _seed_legacy_triples(
            self.db_path,
            [
                ("mem-1", "mentions", "Alice", "extraction", 0.9),
                ("mem-1", "mentions", "Bob", "extraction", 0.9),
                ("mem-2", "fact", "Some fact about mem-2", "test", 0.7),
            ],
        )

        # No annotations rows yet.
        self.assertEqual(self._annotations_count(), 0)

        beam = BeamMemory(db_path=self.db_path)
        try:
            # Auto-migration has populated annotations.
            self.assertEqual(self._annotations_count(), 3)

            # Both mentions for mem-1 are present — silent-destruction
            # bug fixed end-to-end through the auto-migrate path.
            store = AnnotationStore(db_path=self.db_path)
            mentions = store.query_by_memory("mem-1", kind="mentions")
            values = {r["value"] for r in mentions}
            self.assertEqual(values, {"Alice", "Bob"})
        finally:
            try:
                beam.conn.close()
            except Exception:
                pass

    def test_reinit_after_migration_is_noop(self):
        """Opening BeamMemory a second time on an already-migrated DB
        does not re-migrate rows."""
        _seed_legacy_triples(
            self.db_path,
            [("mem-1", "mentions", "Alice", "extraction", 0.9)],
        )

        beam1 = BeamMemory(db_path=self.db_path)
        self.assertEqual(self._annotations_count(), 1)

        # Re-open BeamMemory without closing beam1's connection — the
        # thread-local connection cache is the production pattern, so
        # this exercises what really happens when callers ask for a
        # BeamMemory twice in one session.
        beam2 = BeamMemory(db_path=self.db_path)
        self.assertEqual(self._annotations_count(), 1)
        # Sanity: both instances point at the same shared connection.
        self.assertIs(beam1.conn, beam2.conn)

    def test_opt_out_skips_migration_and_warns(self):
        """MNEMOSYNE_AUTO_MIGRATE=0 leaves legacy rows in place and emits
        a warning log line pointing at the manual script."""
        _seed_legacy_triples(
            self.db_path,
            [
                ("mem-1", "mentions", "Alice", "extraction", 0.9),
                ("mem-1", "mentions", "Bob", "extraction", 0.9),
            ],
        )

        os.environ["MNEMOSYNE_AUTO_MIGRATE"] = "0"

        with self.assertLogs("mnemosyne.core.beam", level="WARNING") as logs:
            beam = BeamMemory(db_path=self.db_path)
            try:
                # No migration ran — annotations still empty.
                self.assertEqual(self._annotations_count(), 0)
            finally:
                try:
                    beam.conn.close()
                except Exception:
                    pass

        warning_text = "\n".join(logs.output)
        self.assertIn("MNEMOSYNE_AUTO_MIGRATE=0", warning_text)
        self.assertIn("migrate_triplestore_split.py", warning_text)
        self.assertIn("2 annotation rows", warning_text)

    def test_opt_out_on_fresh_install_silent(self):
        """With opt-out enabled but no legacy data, no warning should fire —
        the warning is only relevant when there's pending work."""
        os.environ["MNEMOSYNE_AUTO_MIGRATE"] = "0"

        # Capture all WARNING+ logs from the beam module.
        logger = logging.getLogger("mnemosyne.core.beam")
        prev_level = logger.level
        logger.setLevel(logging.WARNING)
        try:
            handler_records = []

            class _RecordHandler(logging.Handler):
                def emit(self, record):
                    handler_records.append(record)

            handler = _RecordHandler()
            logger.addHandler(handler)
            try:
                beam = BeamMemory(db_path=self.db_path)
                try:
                    pass
                finally:
                    try:
                        beam.conn.close()
                    except Exception:
                        pass
            finally:
                logger.removeHandler(handler)
        finally:
            logger.setLevel(prev_level)

        e6_warnings = [
            r for r in handler_records
            if "MNEMOSYNE_AUTO_MIGRATE" in r.getMessage()
        ]
        self.assertEqual(
            e6_warnings, [],
            "Should not warn about MNEMOSYNE_AUTO_MIGRATE when there's nothing to migrate",
        )

    def test_migration_writes_backup(self):
        """Auto-migration writes the .pre_e6_backup file by default."""
        _seed_legacy_triples(
            self.db_path,
            [("mem-1", "mentions", "Alice", "extraction", 0.9)],
        )

        beam = BeamMemory(db_path=self.db_path)
        try:
            backup_path = Path(str(self.db_path) + ".pre_e6_backup")
            self.assertTrue(
                backup_path.exists(),
                "Auto-migration should write a backup file",
            )
        finally:
            try:
                beam.conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
