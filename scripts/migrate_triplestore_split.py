#!/usr/bin/env python3
"""
Mnemosyne E6 Migration — TripleStore Split (CLI wrapper)
========================================================

Thin CLI wrapper around `mnemosyne.migrations.e6_triplestore_split.migrate()`.
The actual migration logic lives in the package so pip-installed deployments
get auto-migration on BeamMemory init without depending on this script being
present on disk.

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
- mnemosyne/migrations/e6_triplestore_split.py — migration implementation
- mnemosyne/core/annotations.py — AnnotationStore (target schema)
- mnemosyne/core/triples.py — TripleStore (now scoped to current-truth)
- .hermes/ledger/memory-contract.md (E6) — ledger row + audit trail
- .hermes/plans/2026-05-10-e6-triplestore-split-sweep.md — pre-coding sweep
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

# Ensure the package is importable when this script is run directly from
# a source checkout (pip installs already have it on sys.path).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mnemosyne.migrations.e6_triplestore_split import migrate


# Canonical DB path matches mnemosyne.core.triples.DEFAULT_DB and
# mnemosyne.core.annotations.DEFAULT_DB. Keep in sync if those move.
CANONICAL_DB = Path.home() / ".hermes" / "mnemosyne" / "data" / "triples.db"


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
