#!/usr/bin/env python3
"""
Mnemosyne E6 Migration — TripleStore Split
==========================================

Migrates annotation-flavored rows out of the legacy `triples` table and
into the new `annotations` table introduced by E6. Fixes the silent-
destruction bug where adding multiple mentions / facts for one memory
silently invalidated prior rows via `(subject, predicate)` auto-
invalidation.

Behavior
--------
1. Detect rows in `triples` whose predicate is in `ANNOTATION_KINDS`
   (mentions, fact, occurred_on, has_source).
2. Insert each row into `annotations` using the (memory_id, kind, value)
   mapping (subject → memory_id, predicate → kind, object → value).
3. Skip rows that already have a corresponding row in `annotations`
   (idempotent — safe to re-run).
4. Do NOT delete the source rows from `triples`. They remain as legacy
   data; `annotations` is the canonical store post-migration. This
   makes the migration reversible — restore the DB from backup if needed.

Usage
-----
    # Auto-discover canonical DB and run migration
    python scripts/migrate_triplestore_split.py

    # Run on a specific DB
    python scripts/migrate_triplestore_split.py --db /path/to/mnemosyne.db

    # Preview what would change without writing
    python scripts/migrate_triplestore_split.py --dry-run

    # Skip the file-level backup (default: creates {db}.pre_e6_backup)
    python scripts/migrate_triplestore_split.py --no-backup

Exit codes
----------
0 — migration completed (or was already complete / no work to do)
1 — error (DB missing, schema malformed, etc.)
2 — dry-run completed (rows would be migrated but no changes made)

See also
--------
- mnemosyne/core/annotations.py — AnnotationStore implementation
- mnemosyne/core/triples.py — TripleStore (now scoped to current-truth)
- .hermes/ledger/memory-contract.md (E6) — ledger row + audit trail
- .hermes/plans/2026-05-10-e6-triplestore-split-sweep.md — pre-coding sweep
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# Canonical DB path matches mnemosyne.core.triples.DEFAULT_DB and
# mnemosyne.core.annotations.DEFAULT_DB. Keep in sync if those move.
CANONICAL_DB = Path.home() / ".hermes" / "mnemosyne" / "data" / "triples.db"

# Annotation-kind classifier. Must stay in sync with
# mnemosyne.core.annotations.ANNOTATION_KINDS.
ANNOTATION_KINDS = frozenset({"mentions", "fact", "occurred_on", "has_source"})


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    cursor = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cursor.fetchone() is not None


def _ensure_annotations_schema(conn: sqlite3.Connection) -> None:
    """Create the annotations table + indexes if missing.

    Duplicates `mnemosyne.core.annotations.init_annotations` to keep this
    script runnable without importing from the package (useful for users
    who hit the migration before fully installing the new code).
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            source TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_annot_memory_kind "
        "ON annotations(memory_id, kind)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_annot_kind_value "
        "ON annotations(kind, value)"
    )
    conn.commit()


def _classify_rows(
    conn: sqlite3.Connection,
) -> Tuple[List[sqlite3.Row], int]:
    """Return (rows-to-migrate, total-triples-row-count).

    Rows-to-migrate are those whose predicate is in ANNOTATION_KINDS and
    that do not already have a matching row in `annotations`.
    """
    if not _has_table(conn, "triples"):
        return [], 0

    conn.row_factory = sqlite3.Row

    # Total for reporting
    total = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]

    # Candidates: annotation-flavored predicates only
    placeholders = ",".join("?" * len(ANNOTATION_KINDS))
    candidates = conn.execute(
        f"""
        SELECT id, subject, predicate, object, source, confidence, created_at
        FROM triples
        WHERE predicate IN ({placeholders})
        ORDER BY id ASC
        """,
        tuple(ANNOTATION_KINDS),
    ).fetchall()

    # Filter out those already migrated.
    # Idempotency key: (memory_id=subject, kind=predicate, value=object).
    # Tuple identity here is good enough; we're matching one-to-one across
    # the data move.
    if not _has_table(conn, "annotations"):
        return list(candidates), total

    existing_keys = set()
    for row in conn.execute(
        "SELECT memory_id, kind, value FROM annotations"
    ).fetchall():
        existing_keys.add((row[0], row[1], row[2]))

    needs_migration = [
        row
        for row in candidates
        if (row["subject"], row["predicate"], row["object"]) not in existing_keys
    ]
    return needs_migration, total


def _migrate_rows(
    conn: sqlite3.Connection, rows: List[sqlite3.Row]
) -> int:
    """Insert rows into annotations. Returns count written."""
    if not rows:
        return 0

    cursor = conn.cursor()
    written = 0
    for row in rows:
        cursor.execute(
            """
            INSERT INTO annotations
                (memory_id, kind, value, source, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                row["subject"],
                row["predicate"],
                row["object"],
                row["source"],
                row["confidence"] if row["confidence"] is not None else 1.0,
                row["created_at"],
            ),
        )
        written += 1
    return written


def _kind_counts(rows: List[sqlite3.Row]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        kind = row["predicate"]
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def migrate(
    db_path: Path,
    dry_run: bool = False,
    backup: bool = True,
    log_fn=print,
) -> int:
    """Run the migration. Returns the number of rows migrated.

    - `dry_run=True` reports what would change without writing.
    - `backup=True` copies the DB file to `{db}.pre_e6_backup` first.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        log_fn(f"ERROR: database not found: {db_path}")
        raise FileNotFoundError(db_path)

    # Pre-flight inspection (read-only)
    conn = sqlite3.connect(str(db_path))
    try:
        rows, total = _classify_rows(conn)
    finally:
        conn.close()

    log_fn(f"Database: {db_path}")
    log_fn(f"  triples rows (total):        {total}")
    log_fn(f"  rows-to-migrate (this run):  {len(rows)}")
    if rows:
        for kind, count in sorted(_kind_counts(rows).items()):
            log_fn(f"    {kind:<14} {count}")

    if not rows:
        log_fn("Nothing to migrate. Schema is already split or no annotation rows exist.")
        return 0

    if dry_run:
        log_fn("Dry run: no changes written.")
        return len(rows)

    # Backup (file-level copy)
    if backup:
        backup_path = db_path.with_suffix(db_path.suffix + ".pre_e6_backup")
        if backup_path.exists():
            # Don't overwrite an existing backup — that's likely from an
            # earlier migration attempt and is more valuable than the
            # current DB state.
            log_fn(
                f"Backup already exists at {backup_path}; leaving as-is."
            )
        else:
            shutil.copy2(db_path, backup_path)
            log_fn(f"Backup written to {backup_path}")

    # Transactional write
    conn = sqlite3.connect(str(db_path))
    try:
        _ensure_annotations_schema(conn)
        conn.execute("BEGIN")
        try:
            conn.row_factory = sqlite3.Row
            written = _migrate_rows(conn, rows)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()

    log_fn(f"Migration complete: {written} rows moved to annotations table.")
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate annotation-flavored TripleStore rows into AnnotationStore (E6)."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=CANONICAL_DB,
        help=f"Path to Mnemosyne SQLite database (default: {CANONICAL_DB})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the file-level backup (default: write {db}.pre_e6_backup).",
    )
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"ERROR: database not found: {args.db}", file=sys.stderr)
        return 1

    try:
        written = migrate(
            db_path=args.db,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except Exception as e:
        print(f"ERROR: migration failed: {e}", file=sys.stderr)
        return 1

    if args.dry_run and written > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
