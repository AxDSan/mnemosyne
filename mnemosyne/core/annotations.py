"""
Mnemosyne AnnotationStore (E6)
==============================
Append-only multi-valued annotations on memories.

Replaces the annotation-flavored usage of TripleStore. The TripleStore
auto-invalidates on (subject, predicate) on every add — correct for
current-truth temporal facts ("user prefers X" → later "user prefers Y"),
wrong for sibling annotations like (memory_id, "mentions", entity_name)
where multiple values for the same key are the expected shape.

This module owns the annotations table:
    (id, memory_id, kind, value, source, confidence, created_at)

Common kinds in production:
- "mentions"    — entity name extracted from the memory text
- "fact"        — structured fact extracted from the memory text
- "occurred_on" — anchor date for when the memory's content occurred
- "has_source"  — origin of the memory content (URL, channel, tool name)

No invalidation. Append-only. Multi-valued by design.

See:
- `.hermes/plans/2026-05-10-e6-triplestore-split-sweep.md` — call-site sweep
- `.hermes/ledger/memory-contract.md` (E6) — ledger row + audit trail
"""

import sqlite3
from pathlib import Path
from typing import List, Dict, Optional


DEFAULT_DB = Path.home() / ".hermes" / "mnemosyne" / "data" / "triples.db"


def _get_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_annotations(db_path: Optional[Path] = None) -> None:
    """Create the annotations table and supporting indexes if absent.

    Idempotent. Safe to call on databases that already have the table.
    """
    conn = _get_conn(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Read patterns we need to be fast at:
    #   - "all annotations for a memory"           → idx_annot_memory_kind
    #   - "all memories that mention X"            → idx_annot_kind_value
    #   - "distinct entities mentioned across all" → idx_annot_kind (prefix of above)
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_annot_memory_kind "
        "ON annotations(memory_id, kind)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_annot_kind_value "
        "ON annotations(kind, value)"
    )

    conn.commit()


class AnnotationStore:
    """
    Append-only multi-valued annotations on memories.

    Example:
        >>> store = AnnotationStore()
        >>> store.add("mem-42", "mentions", "Alice")
        >>> store.add("mem-42", "mentions", "Bob")
        >>> store.query_by_memory("mem-42", kind="mentions")
        [{'memory_id': 'mem-42', 'kind': 'mentions', 'value': 'Alice', ...},
         {'memory_id': 'mem-42', 'kind': 'mentions', 'value': 'Bob', ...}]
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB
        init_annotations(self.db_path)
        self.conn = _get_conn(self.db_path)

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def add(
        self,
        memory_id: str,
        kind: str,
        value: str,
        source: str = "",
        confidence: float = 1.0,
    ) -> int:
        """Append an annotation row. Returns the new row id.

        No invalidation of prior rows — multiple values for the same
        (memory_id, kind) coexist and are all returned by query methods.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO annotations (memory_id, kind, value, source, confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            (memory_id, kind, value, source, confidence),
        )
        self.conn.commit()
        return cursor.lastrowid

    def add_many(
        self,
        memory_id: str,
        kind: str,
        values: List[str],
        source: str = "",
        confidence: float = 1.0,
    ) -> int:
        """Batch-insert helper for multiple values under one (memory_id, kind).

        Returns the count of rows inserted. Skips empty / blank values silently.
        """
        if not values:
            return 0

        rows = [
            (memory_id, kind, v, source, confidence)
            for v in values
            if v and v.strip()
        ]
        if not rows:
            return 0

        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT INTO annotations (memory_id, kind, value, source, confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def query_by_memory(
        self, memory_id: str, kind: Optional[str] = None
    ) -> List[Dict]:
        """All annotations for a memory, optionally filtered by kind."""
        cursor = self.conn.cursor()
        if kind is None:
            cursor.execute(
                "SELECT * FROM annotations WHERE memory_id = ? "
                "ORDER BY created_at ASC, id ASC",
                (memory_id,),
            )
        else:
            cursor.execute(
                "SELECT * FROM annotations WHERE memory_id = ? AND kind = ? "
                "ORDER BY created_at ASC, id ASC",
                (memory_id, kind),
            )
        return [dict(row) for row in cursor.fetchall()]

    def query_by_kind(
        self,
        kind: str,
        value: Optional[str] = None,
        memory_id: Optional[str] = None,
    ) -> List[Dict]:
        """All annotations with a given kind, optionally filtered by value or memory_id.

        Mirrors the shape of TripleStore.query_by_predicate so existing
        call sites can swap with minimal changes:
            triples.query_by_predicate("mentions", object=entity)
            annotations.query_by_kind("mentions", value=entity)
        """
        conditions = ["kind = ?"]
        params: List = [kind]
        if value is not None:
            conditions.append("value = ?")
            params.append(value)
        if memory_id is not None:
            conditions.append("memory_id = ?")
            params.append(memory_id)

        where_clause = " AND ".join(conditions)
        cursor = self.conn.cursor()
        cursor.execute(
            f"SELECT * FROM annotations WHERE {where_clause} "
            "ORDER BY created_at ASC, id ASC",
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_distinct_values(self, kind: str) -> List[str]:
        """All distinct values seen for a given kind.

        Mirrors TripleStore.get_distinct_objects.
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT DISTINCT value FROM annotations WHERE kind = ? ORDER BY value",
            (kind,),
        )
        return [row["value"] for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Export / import (parity with TripleStore)
    # ------------------------------------------------------------------

    def export_all(self) -> List[Dict]:
        """Export all rows as a list of dicts."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT id, memory_id, kind, value, source, confidence, created_at
            FROM annotations
            ORDER BY id
            """
        )
        return [dict(row) for row in cursor.fetchall()]

    def import_all(self, annotations: List[Dict], force: bool = False) -> Dict:
        """Import annotations from a list of dicts.

        Idempotent by default: skips records whose id already exists.
        Set force=True to overwrite. Returns import statistics.
        """
        stats = {"inserted": 0, "skipped": 0, "overwritten": 0}
        cursor = self.conn.cursor()
        for item in annotations:
            row_id = item.get("id")
            if row_id is not None:
                cursor.execute("SELECT 1 FROM annotations WHERE id = ?", (row_id,))
                exists = cursor.fetchone() is not None
                if exists and not force:
                    stats["skipped"] += 1
                    continue
                if exists and force:
                    cursor.execute("DELETE FROM annotations WHERE id = ?", (row_id,))
                    stats["overwritten"] += 1
                else:
                    stats["inserted"] += 1
                cursor.execute(
                    """
                    INSERT INTO annotations
                        (id, memory_id, kind, value, source, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row_id,
                        item.get("memory_id"),
                        item.get("kind"),
                        item.get("value"),
                        item.get("source", "imported"),
                        item.get("confidence", 1.0),
                        item.get("created_at"),
                    ),
                )
            else:
                stats["inserted"] += 1
                cursor.execute(
                    """
                    INSERT INTO annotations
                        (memory_id, kind, value, source, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.get("memory_id"),
                        item.get("kind"),
                        item.get("value"),
                        item.get("source", "imported"),
                        item.get("confidence", 1.0),
                        item.get("created_at"),
                    ),
                )
        self.conn.commit()
        return stats


# ---------------------------------------------------------------------------
# Module-level convenience functions (mirror triples.py shape)
# ---------------------------------------------------------------------------

def add_annotation(
    memory_id: str,
    kind: str,
    value: str,
    source: str = "",
    confidence: float = 1.0,
    db_path: Optional[Path] = None,
) -> int:
    """Add a single annotation without instantiating AnnotationStore manually."""
    store = AnnotationStore(db_path=db_path)
    return store.add(memory_id, kind, value, source=source, confidence=confidence)


def query_annotations(
    memory_id: Optional[str] = None,
    kind: Optional[str] = None,
    value: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict]:
    """Query annotations without instantiating AnnotationStore manually.

    At least one of (memory_id, kind) should be provided for indexed reads.
    """
    store = AnnotationStore(db_path=db_path)
    if memory_id is not None and kind is None and value is None:
        return store.query_by_memory(memory_id)
    if memory_id is not None and kind is not None and value is None:
        return store.query_by_memory(memory_id, kind=kind)
    if kind is not None:
        return store.query_by_kind(kind, value=value, memory_id=memory_id)
    # Fallback — no filter; return everything (rare; mostly for debugging).
    return store.export_all()
