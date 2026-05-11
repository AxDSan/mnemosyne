"""
Tests for Mnemosyne AnnotationStore (E6).

The AnnotationStore replaces the annotation-flavored usage of TripleStore.
The current TripleStore.add() auto-invalidates on (subject, predicate),
which is correct for current-truth temporal facts ("user prefers X" → "Y")
but silently invalidates sibling annotation rows when used for multi-valued
annotations like (memory_id, "mentions", entity).

These tests pin the new contract:
- Multi-value annotations for the same (memory_id, kind) are preserved.
- No auto-invalidation. AnnotationStore is append-only.
- Read paths return all rows for a memory, regardless of insertion order.

Background and motivation: see
  .hermes/plans/2026-05-10-e6-triplestore-split-sweep.md
  .hermes/ledger/memory-contract.md (E6 row)
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ImportError until AnnotationStore lands. That is the first signal the test
# is wired correctly: red against main, green after the implementation lands.
from mnemosyne.core.annotations import AnnotationStore, init_annotations
from mnemosyne.core.triples import TripleStore


class TestAnnotationStoreMultiValuePreservation(unittest.TestCase):
    """
    The regression guard for the silent-destruction bug.

    Pre-E6, writing multiple mentions for the same memory through TripleStore
    set valid_until on prior rows because the invalidation key was
    (subject, predicate) regardless of object. Post-E6, the AnnotationStore
    must preserve all values for the same (memory_id, kind) key.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.store = AnnotationStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_multiple_mentions_for_one_memory_preserved(self):
        """Three entities mentioned by the same memory survive all three writes."""
        self.store.add("mem-1", "mentions", "Alice")
        self.store.add("mem-1", "mentions", "Bob")
        self.store.add("mem-1", "mentions", "Charlie")

        results = self.store.query_by_memory(memory_id="mem-1", kind="mentions")
        values = {r["value"] for r in results}
        self.assertEqual(values, {"Alice", "Bob", "Charlie"})

    def test_multiple_facts_for_one_memory_preserved(self):
        """Three extracted facts for the same memory survive all three writes."""
        self.store.add("mem-1", "fact", "The user prefers concise responses")
        self.store.add("mem-1", "fact", "The user is a senior engineer")
        self.store.add("mem-1", "fact", "The user works in Python")

        results = self.store.query_by_memory(memory_id="mem-1", kind="fact")
        values = {r["value"] for r in results}
        self.assertEqual(len(values), 3)

    def test_add_returns_row_id(self):
        """add() returns the lastrowid for the inserted annotation."""
        row_id_1 = self.store.add("mem-1", "mentions", "Alice")
        row_id_2 = self.store.add("mem-1", "mentions", "Bob")
        self.assertIsNotNone(row_id_1)
        self.assertIsNotNone(row_id_2)
        self.assertNotEqual(row_id_1, row_id_2)

    def test_no_auto_invalidation_columns(self):
        """AnnotationStore has no valid_from / valid_until columns — append-only."""
        self.store.add("mem-1", "mentions", "Alice")
        all_rows = self.store.export_all()
        self.assertEqual(len(all_rows), 1)
        self.assertNotIn("valid_until", all_rows[0])
        self.assertNotIn("valid_from", all_rows[0])


class TestAnnotationStoreQueries(unittest.TestCase):
    """Query methods mirror the TripleStore surface but on the annotations table."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.store = AnnotationStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_query_by_memory_filters_by_memory_id(self):
        self.store.add("mem-1", "mentions", "Alice")
        self.store.add("mem-2", "mentions", "Bob")
        self.store.add("mem-1", "fact", "Some fact about mem-1")

        results = self.store.query_by_memory(memory_id="mem-1")
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["memory_id"] == "mem-1" for r in results))

    def test_query_by_memory_with_kind_filter(self):
        self.store.add("mem-1", "mentions", "Alice")
        self.store.add("mem-1", "mentions", "Bob")
        self.store.add("mem-1", "fact", "Some fact")

        mentions = self.store.query_by_memory(memory_id="mem-1", kind="mentions")
        self.assertEqual(len(mentions), 2)
        self.assertTrue(all(r["kind"] == "mentions" for r in mentions))

    def test_query_by_kind_returns_all_matching_kind(self):
        self.store.add("mem-1", "mentions", "Alice")
        self.store.add("mem-2", "mentions", "Alice")
        self.store.add("mem-1", "fact", "Something")

        alice_mentions = self.store.query_by_kind("mentions", value="Alice")
        memory_ids = {r["memory_id"] for r in alice_mentions}
        self.assertEqual(memory_ids, {"mem-1", "mem-2"})

    def test_query_by_kind_with_memory_id_filter(self):
        self.store.add("mem-1", "mentions", "Alice")
        self.store.add("mem-1", "mentions", "Bob")
        self.store.add("mem-2", "mentions", "Charlie")

        mem1_mentions = self.store.query_by_kind("mentions", memory_id="mem-1")
        values = {r["value"] for r in mem1_mentions}
        self.assertEqual(values, {"Alice", "Bob"})

    def test_get_distinct_values_for_kind(self):
        self.store.add("mem-1", "mentions", "Alice")
        self.store.add("mem-2", "mentions", "Alice")  # duplicate value, different memory
        self.store.add("mem-3", "mentions", "Bob")

        distinct = self.store.get_distinct_values("mentions")
        self.assertEqual(set(distinct), {"Alice", "Bob"})


class TestAnnotationStoreExportImport(unittest.TestCase):
    """Round-trip via export_all / import_all preserves data."""

    def setUp(self):
        self.tmp_src = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_src.close()
        self.tmp_dst = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_dst.close()
        self.src = AnnotationStore(db_path=Path(self.tmp_src.name))
        self.dst = AnnotationStore(db_path=Path(self.tmp_dst.name))

    def tearDown(self):
        for store in (self.src, self.dst):
            try:
                store.conn.close()
            except Exception:
                pass
        for path in (self.tmp_src.name, self.tmp_dst.name):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_export_import_round_trip(self):
        self.src.add("mem-1", "mentions", "Alice", source="extraction", confidence=0.8)
        self.src.add("mem-1", "mentions", "Bob")
        self.src.add("mem-2", "fact", "Something interesting")

        exported = self.src.export_all()
        self.assertEqual(len(exported), 3)

        stats = self.dst.import_all(exported)
        self.assertEqual(stats["inserted"], 3)
        self.assertEqual(stats["skipped"], 0)

        imported = self.dst.export_all()
        self.assertEqual(len(imported), 3)

    def test_import_idempotent_on_existing_ids(self):
        self.src.add("mem-1", "mentions", "Alice")
        exported = self.src.export_all()

        self.dst.import_all(exported)
        stats = self.dst.import_all(exported)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["inserted"], 0)


class TestTripleStoreAddFactsDeprecation(unittest.TestCase):
    """
    Post-E6, TripleStore.add_facts emits DeprecationWarning and routes writes
    to AnnotationStore — preserving multi-fact data that the legacy
    implementation would have silently invalidated.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.store = TripleStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_add_facts_emits_deprecation_warning(self):
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.store.add_facts(
                "mem-1",
                ["The user prefers concise responses (a long enough fact)"],
            )
            deprecation_msgs = [
                w for w in caught if issubclass(w.category, DeprecationWarning)
            ]
            self.assertTrue(
                len(deprecation_msgs) >= 1,
                "add_facts should emit DeprecationWarning post-E6",
            )

    def test_add_facts_preserves_legacy_write_count(self):
        """Shim still returns the same count as the legacy implementation —
        the filtering of empty/too-short facts is preserved so external
        callers' assumptions hold during the deprecation period.

        Note: the actual silent-invalidation bug remains in the legacy
        write path (writes hit the triples table with auto-invalidation).
        Production callers are migrated to AnnotationStore directly in
        a sibling commit. External callers see the DeprecationWarning
        and are expected to migrate.
        """
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            stored = self.store.add_facts(
                "mem-1",
                [
                    "First fact about mem-1 that is long enough",
                    "Second fact about mem-1 that is long enough",
                    "x",  # too short, filtered
                ],
            )
        # Legacy filtering: returns count of kept facts.
        self.assertEqual(stored, 2)


class TestTripleStoreSilentInvalidationBehavior(unittest.TestCase):
    """
    Characterization test documenting the bug E6 fixes.

    These tests pin the pre-E6 silent-invalidation behavior so future readers
    understand why the split was necessary. The TripleStore retains its
    auto-invalidation semantics post-E6 — that is correct for current-truth
    facts — but the annotation-flavored usage has moved to AnnotationStore.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)
        self.store = TripleStore(db_path=self.db_path)

    def tearDown(self):
        try:
            self.store.conn.close()
        except Exception:
            pass
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass

    def test_tripstore_silently_invalidates_sibling_annotations(self):
        """
        Documents the pre-E6 bug: adding a second annotation with the same
        (subject, predicate) key silently sets valid_until on the first row,
        even though the two are sibling annotations rather than supersession.

        This is correct semantics for current-truth facts (TripleStore's
        intended use post-E6) but was wrong for the annotation use case
        that E6 moves to AnnotationStore.
        """
        self.store.add("mem-1", "mentions", "Alice", valid_from="2026-01-01")
        self.store.add("mem-1", "mentions", "Bob", valid_from="2026-01-02")

        all_rows = self.store.export_all()
        by_object = {r["object"]: r for r in all_rows}

        # Both rows still present in the table
        self.assertEqual(set(by_object.keys()), {"Alice", "Bob"})
        # But Alice was silently marked invalidated when Bob was added
        self.assertIsNotNone(by_object["Alice"]["valid_until"])
        self.assertIsNone(by_object["Bob"]["valid_until"])

    def test_tripstore_query_with_as_of_drops_silently_invalidated(self):
        """
        Documents the downstream consequence: TripleStore.query() with an
        as_of date later than the second add drops the invalidated row
        entirely. Any code path filtering by valid_until IS NULL loses
        the data. Production read paths (query_by_predicate) do not filter
        by valid_until, so the bug is latent rather than active for current
        callers — but the data semantics are still wrong.
        """
        self.store.add("mem-1", "mentions", "Alice", valid_from="2026-01-01")
        self.store.add("mem-1", "mentions", "Bob", valid_from="2026-01-02")

        # Query at a date after both adds
        current = self.store.query(subject="mem-1", predicate="mentions", as_of="2026-01-03")
        objects = [r["object"] for r in current]
        self.assertIn("Bob", objects)
        self.assertNotIn("Alice", objects)  # silently dropped by as_of filter


if __name__ == "__main__":
    unittest.main()
