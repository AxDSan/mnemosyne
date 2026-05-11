"""
Regression tests for E6.a follow-up fixes:

- E6.a.1: `mnemosyne_triple_add` MCP tool routes annotation-flavored
  predicates to `AnnotationStore` instead of writing them into the
  legacy `triples` table (which would silently invalidate sibling
  annotation rows via the same bug E6 fixed in the extraction path).

- E6.a.2: `BeamMemory.forget_working` (called from `Mnemosyne.forget`)
  cascade-deletes annotation rows tagged with the same memory_id —
  pre-fix, mentions / fact / occurred_on / has_source rows leaked
  through export, recall, and entity-aware queries even after forget.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mnemosyne.core.annotations import AnnotationStore, ANNOTATION_KINDS
from mnemosyne.core.beam import BeamMemory
from mnemosyne.core.memory import Mnemosyne
from mnemosyne.core.triples import TripleStore


class TestForgetCascadeToAnnotations(unittest.TestCase):
    """E6.a.2: `forget()` removes annotations tagged with the memory_id."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

    def tearDown(self):
        import os
        for suffix in ("", ".pre_e6_backup"):
            try:
                os.unlink(str(self.tmp.name) + suffix)
            except OSError:
                pass

    def test_forget_deletes_annotations_for_memory_id(self):
        """The cascade: store a memory with multiple annotations, forget
        it, verify the annotations are gone."""
        mem = Mnemosyne(session_id="s1", db_path=self.db_path)
        memory_id = mem.remember(
            "Alice met Bob in San Francisco.",
            source="test",
            importance=0.5,
            extract_entities=True,
        )

        # Pre-forget: annotations exist for this memory_id.
        ann_store = AnnotationStore(db_path=self.db_path)
        pre_count = len(ann_store.query_by_memory(memory_id=memory_id))
        self.assertGreater(pre_count, 0, "test setup failure: no annotations to forget")

        # Forget.
        result = mem.forget(memory_id)
        self.assertTrue(result, "forget() returned False — memory wasn't found")

        # Post-forget: annotations for this memory_id should be empty.
        post_rows = ann_store.query_by_memory(memory_id=memory_id)
        self.assertEqual(
            post_rows, [],
            f"annotations for forgotten memory_id={memory_id} still present: {post_rows}",
        )

    def test_forget_doesnt_touch_other_memories_annotations(self):
        """Forgetting one memory doesn't affect another memory's annotations."""
        mem = Mnemosyne(session_id="s1", db_path=self.db_path)
        ann_store = AnnotationStore(db_path=self.db_path)

        id_to_forget = mem.remember(
            "Alice met Bob.",
            source="test",
            extract_entities=True,
        )
        id_to_keep = mem.remember(
            "Charlie met Dana.",
            source="test",
            extract_entities=True,
        )

        keep_count_before = len(ann_store.query_by_memory(memory_id=id_to_keep))
        self.assertGreater(keep_count_before, 0)

        mem.forget(id_to_forget)

        self.assertEqual(
            ann_store.query_by_memory(memory_id=id_to_forget), [],
            "Forgotten memory's annotations should be empty",
        )
        keep_count_after = len(ann_store.query_by_memory(memory_id=id_to_keep))
        self.assertEqual(
            keep_count_after, keep_count_before,
            f"Sibling memory's annotations changed: {keep_count_before} → {keep_count_after}",
        )

    def test_beam_forget_working_directly_cascades(self):
        """The cascade is in BeamMemory.forget_working, not just the
        Mnemosyne wrapper — so direct BeamMemory callers also benefit.
        """
        beam = BeamMemory(session_id="s1", db_path=self.db_path)
        memory_id = beam.remember(
            "Alice met Bob in Paris.",
            source="test",
            importance=0.5,
            extract_entities=True,
        )

        ann_store = AnnotationStore(db_path=self.db_path)
        self.assertGreater(
            len(ann_store.query_by_memory(memory_id=memory_id)), 0,
            "test setup: expected annotations to be created",
        )

        beam.forget_working(memory_id)
        self.assertEqual(
            ann_store.query_by_memory(memory_id=memory_id), [],
            "BeamMemory.forget_working should cascade-delete annotations",
        )

    def test_forget_after_export_leaves_no_leaked_annotations(self):
        """Privacy regression: forget then export — the forgotten memory's
        annotations must not appear in the export. Pre-fix, the cascade
        gap meant annotations remained in the DB and exported normally.
        """
        mem = Mnemosyne(session_id="s1", db_path=self.db_path)
        memory_id = mem.remember(
            "Confidential: user's home address is 123 Main St.",
            source="test",
            extract_entities=True,
        )
        mem.forget(memory_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "export.json"
            mem.export_to_file(str(export_path))
            with open(export_path) as f:
                payload = json.load(f)

        forgotten_annotations = [
            r for r in payload.get("annotations", [])
            if r.get("memory_id") == memory_id
        ]
        self.assertEqual(
            forgotten_annotations, [],
            f"export leaked {len(forgotten_annotations)} annotations from forgotten memory",
        )


class TestMcpTripleAddAnnotationRouting(unittest.TestCase):
    """E6.a.1: `mnemosyne_triple_add` routes annotation predicates to AnnotationStore."""

    def setUp(self):
        # The MCP plugin caches global memory + triple-store handles;
        # reset between tests so each gets a fresh DB.
        import hermes_plugin
        hermes_plugin._memory_instance = None
        hermes_plugin._current_session_id = None
        hermes_plugin._triple_store = None

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

        # Pre-init the canonical DB the plugin will use. Easiest path: a
        # fresh BeamMemory init that creates both tables + the singleton.
        # The plugin's _get_memory() reads MNEMOSYNE_DATA_DIR or default.
        import os
        self._saved_env = os.environ.get("MNEMOSYNE_DATA_DIR")
        os.environ["MNEMOSYNE_DATA_DIR"] = str(self.db_path.parent)
        # Use a deterministic db filename inside the env-pointed dir.
        self.canon_db = self.db_path.parent / "mnemosyne.db"

    def tearDown(self):
        import os
        if self._saved_env is None:
            os.environ.pop("MNEMOSYNE_DATA_DIR", None)
        else:
            os.environ["MNEMOSYNE_DATA_DIR"] = self._saved_env

        for path in (self.tmp.name, str(self.canon_db), str(self.canon_db) + ".pre_e6_backup"):
            try:
                os.unlink(path)
            except OSError:
                pass

        import hermes_plugin
        hermes_plugin._memory_instance = None
        hermes_plugin._current_session_id = None
        hermes_plugin._triple_store = None

    def _invoke(self, args: dict) -> dict:
        """Call the tool handler and decode the JSON response."""
        from hermes_plugin.tools import mnemosyne_triple_add
        return json.loads(mnemosyne_triple_add(args))

    def test_annotation_predicate_routes_to_annotation_store(self):
        """A `mentions` predicate must land in `annotations`, not `triples`."""
        result = self._invoke({
            "subject": "mem-test-1",
            "predicate": "mentions",
            "object": "Alice",
        })
        self.assertEqual(result.get("status"), "added")
        self.assertEqual(result.get("store"), "annotations")
        self.assertNotIn("triple_id", result)
        self.assertIn("annotation_id", result)

        # Verify the data landed in annotations.
        ann_store = AnnotationStore(db_path=self.canon_db)
        rows = ann_store.query_by_memory(memory_id="mem-test-1", kind="mentions")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], "Alice")

        # And NOT in triples.
        triple_store = TripleStore(db_path=self.canon_db)
        triple_rows = triple_store.query_by_predicate("mentions")
        self.assertEqual(len(triple_rows), 0)

    def test_multiple_annotation_writes_for_one_subject_all_preserved(self):
        """The whole point of E6: writing two `mentions` for the same
        subject through the MCP tool no longer silently invalidates the
        first one. Both must coexist in `annotations`.
        """
        self._invoke({"subject": "mem-test-2", "predicate": "mentions", "object": "Alice"})
        self._invoke({"subject": "mem-test-2", "predicate": "mentions", "object": "Bob"})

        ann_store = AnnotationStore(db_path=self.canon_db)
        rows = ann_store.query_by_memory(memory_id="mem-test-2", kind="mentions")
        values = {r["value"] for r in rows}
        self.assertEqual(values, {"Alice", "Bob"})

    def test_current_truth_predicate_still_routes_to_triplestore(self):
        """Non-annotation predicates retain the current-truth semantics:
        TripleStore.add with auto-invalidation. Backward compatibility
        for legitimate agentic uses of the tool.
        """
        result = self._invoke({
            "subject": "Maya",
            "predicate": "assigned_to",
            "object": "auth-migration",
            "valid_from": "2026-01-15",
        })
        self.assertEqual(result.get("status"), "added")
        self.assertEqual(result.get("store"), "triples")
        self.assertIn("triple_id", result)
        self.assertNotIn("annotation_id", result)

        # Verify it landed in triples.
        triple_store = TripleStore(db_path=self.canon_db)
        rows = triple_store.query(subject="Maya", predicate="assigned_to")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["object"], "auth-migration")

    def test_all_annotation_kinds_route_correctly(self):
        """Every kind in `ANNOTATION_KINDS` should route to AnnotationStore.
        If a new kind is added to the set, this test ensures the routing
        catches it automatically."""
        kinds = sorted(ANNOTATION_KINDS)
        for i, kind in enumerate(kinds):
            result = self._invoke({
                "subject": f"mem-kind-{i}",
                "predicate": kind,
                "object": f"value-{i}",
            })
            self.assertEqual(
                result.get("store"), "annotations",
                f"predicate={kind} did not route to annotations (got store={result.get('store')!r})",
            )


class TestForgetCrossSessionDoesNotLeakAnnotations(unittest.TestCase):
    """E6.a /review finding F1: an attacker / hostile agent in session B
    must not be able to wipe session A's annotations by calling forget()
    with a memory_id from session A.

    Pre-fix, the cascade `DELETE FROM annotations WHERE memory_id = ?`
    ran unconditionally — the session_id check on the working_memory
    DELETE was the only authorization, and a wrong-session call matched
    zero working_memory rows but silently nuked all annotations for that
    memory_id. forget() returned False so the operator saw 'not found'
    while the damage was already done.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

    def tearDown(self):
        import os
        for suffix in ("", ".pre_e6_backup"):
            try:
                os.unlink(str(self.tmp.name) + suffix)
            except OSError:
                pass

    def test_wrong_session_forget_does_not_touch_annotations(self):
        # Session A stores a memory and extracts annotations.
        mem_a = Mnemosyne(session_id="session-a", db_path=self.db_path)
        memory_id = mem_a.remember(
            "Alice met Bob in Paris.",
            source="test",
            extract_entities=True,
        )

        ann_store = AnnotationStore(db_path=self.db_path)
        pre_count = len(ann_store.query_by_memory(memory_id=memory_id))
        self.assertGreater(pre_count, 0, "test setup: expected annotations")

        # Session B tries to forget session A's memory_id. This used to
        # silently destroy session A's annotations.
        mem_b = Mnemosyne(session_id="session-b", db_path=self.db_path)
        result = mem_b.forget(memory_id)

        self.assertFalse(result, "forget() should return False — cross-session attempt")

        # The critical assertion: session A's annotations must still exist.
        post_rows = ann_store.query_by_memory(memory_id=memory_id)
        self.assertEqual(
            len(post_rows), pre_count,
            f"cross-session forget silently destroyed annotations: {pre_count} → {len(post_rows)}",
        )

    def test_correct_session_forget_still_works(self):
        """Same-session forget must still cascade correctly — the fix
        is gating the cascade on authorization, not disabling it."""
        mem = Mnemosyne(session_id="session-a", db_path=self.db_path)
        memory_id = mem.remember(
            "Test memory with entities Alice and Bob.",
            source="test",
            extract_entities=True,
        )
        ann_store = AnnotationStore(db_path=self.db_path)
        self.assertGreater(len(ann_store.query_by_memory(memory_id=memory_id)), 0)

        result = mem.forget(memory_id)
        self.assertTrue(result, "same-session forget should match the row")
        self.assertEqual(
            ann_store.query_by_memory(memory_id=memory_id), [],
            "same-session forget should still cascade-delete annotations",
        )


class TestForgetCascadeIsAtomic(unittest.TestCase):
    """E6.a /review finding F3: the working_memory DELETE and annotations
    DELETE must be atomic — if the cascade raises, the working_memory
    delete should roll back, not sit uncommitted on the connection
    waiting to be silently committed by an unrelated later call.
    """

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

    def tearDown(self):
        import os
        for suffix in ("", ".pre_e6_backup"):
            try:
                os.unlink(str(self.tmp.name) + suffix)
            except OSError:
                pass

    def test_failed_cascade_rolls_back_working_memory_delete(self):
        """Simulate cascade failure by dropping the annotations table after
        BeamMemory.__init__ has finished its schema setup but before
        forget_working() runs. The wm DELETE succeeds, the annotations
        DELETE raises `sqlite3.OperationalError: no such table`, and the
        try/except/rollback in forget_working() must put the wm row back.
        """
        import sqlite3

        beam = BeamMemory(session_id="s1", db_path=self.db_path)
        memory_id = beam.remember(
            "Atomic cascade test.", source="test", importance=0.5,
            extract_entities=True,
        )

        # Sanity: row is present.
        row = beam.conn.execute(
            "SELECT id FROM working_memory WHERE id = ?", (memory_id,)
        ).fetchone()
        self.assertIsNotNone(row)

        # Force the annotations DELETE to fail by dropping the table out
        # from under forget_working() between its schema-setup and the
        # DELETE itself. We do this on a separate raw connection so
        # beam.conn's own state isn't disturbed.
        raw = sqlite3.connect(str(self.db_path))
        raw.execute("DROP TABLE annotations")
        raw.commit()
        raw.close()

        # forget_working should raise (sqlite OperationalError) and roll back.
        with self.assertRaises(sqlite3.OperationalError):
            beam.forget_working(memory_id)

        # The critical assertion: the wm row must still be present
        # because the cascade rolled back atomically.
        row = beam.conn.execute(
            "SELECT id FROM working_memory WHERE id = ?", (memory_id,)
        ).fetchone()
        self.assertIsNotNone(
            row,
            "working_memory row was not rolled back after cascade failure",
        )


class TestMcpTripleQueryAnnotationRouting(unittest.TestCase):
    """E6.a /review finding F5: mnemosyne_triple_query must mirror the
    write-side routing. Pre-fix, agents would write a `mentions` via
    triple_add (routed to annotations) and then query via triple_query
    (still hit triples only) and get empty results — silent split-brain.
    """

    def setUp(self):
        import hermes_plugin
        hermes_plugin._memory_instance = None
        hermes_plugin._current_session_id = None
        hermes_plugin._triple_store = None

        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = Path(self.tmp.name)

        import os
        self._saved_env = os.environ.get("MNEMOSYNE_DATA_DIR")
        os.environ["MNEMOSYNE_DATA_DIR"] = str(self.db_path.parent)
        self.canon_db = self.db_path.parent / "mnemosyne.db"

    def tearDown(self):
        import os
        if self._saved_env is None:
            os.environ.pop("MNEMOSYNE_DATA_DIR", None)
        else:
            os.environ["MNEMOSYNE_DATA_DIR"] = self._saved_env

        for path in (self.tmp.name, str(self.canon_db), str(self.canon_db) + ".pre_e6_backup"):
            try:
                os.unlink(path)
            except OSError:
                pass

        import hermes_plugin
        hermes_plugin._memory_instance = None
        hermes_plugin._current_session_id = None
        hermes_plugin._triple_store = None

    def test_query_for_annotation_predicate_routes_to_annotation_store(self):
        from hermes_plugin.tools import mnemosyne_triple_add, mnemosyne_triple_query

        # Write via triple_add — lands in annotations.
        add_result = json.loads(mnemosyne_triple_add({
            "subject": "mem-rq-1",
            "predicate": "mentions",
            "object": "Alice",
        }))
        self.assertEqual(add_result.get("store"), "annotations")

        # Query via triple_query — must mirror the routing and read from annotations.
        query_result = json.loads(mnemosyne_triple_query({
            "subject": "mem-rq-1",
            "predicate": "mentions",
        }))
        self.assertEqual(query_result.get("store"), "annotations")
        self.assertEqual(query_result.get("results_count"), 1)
        results = query_result.get("results", [])
        self.assertEqual(results[0]["value"], "Alice")
        self.assertEqual(results[0]["kind"], "mentions")

    def test_query_for_current_truth_predicate_still_uses_triplestore(self):
        from hermes_plugin.tools import mnemosyne_triple_add, mnemosyne_triple_query

        json.loads(mnemosyne_triple_add({
            "subject": "Maya",
            "predicate": "assigned_to",
            "object": "auth-migration",
            "valid_from": "2026-01-15",
        }))

        query_result = json.loads(mnemosyne_triple_query({
            "subject": "Maya",
            "predicate": "assigned_to",
        }))
        self.assertEqual(query_result.get("store"), "triples")
        self.assertEqual(query_result.get("results_count"), 1)

    def test_non_string_predicate_does_not_raise(self):
        """E6.a /review F9: hostile / malformed predicate types must not
        crash the handler. None / lists / dicts get a False membership
        check (via the isinstance guard) and fall through to TripleStore
        which handles the error path itself."""
        from hermes_plugin.tools import mnemosyne_triple_query

        for bad_predicate in (None, [], {}, 123):
            result = json.loads(mnemosyne_triple_query({
                "predicate": bad_predicate,
            }))
            # Either a successful empty-result response or an error
            # response — but not a TypeError exception escaping.
            self.assertTrue(
                "results" in result or "error" in result,
                f"unexpected response for predicate={bad_predicate!r}: {result}",
            )


if __name__ == "__main__":
    unittest.main()
