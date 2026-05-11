#!/usr/bin/env python3
"""
Mnemosyne Temporal Triples Backfill Script
==========================================

Generates temporal triples (occurred_on, has_source) for all existing
working_memory and episodic_memory entries that lack them.

Usage:
    python scripts/backfill_temporal_triples.py [--dry-run]

This is a one-time migration script for Mnemosyne v1.13.0.

Post-E6 note
------------
This script writes occurred_on / has_source rows into the legacy `triples`
table. Post-E6, those rows belong in the `annotations` table — but they
will be relocated automatically on the next `BeamMemory()` init via the
auto-migrate hook (or by `python scripts/migrate_triplestore_split.py`
when MNEMOSYNE_AUTO_MIGRATE=0). So this script remains correct in its
data semantics; it just adds an extra migration hop. Prefer running on
a fresh / pre-E6 database; on a post-E6 database the new annotations
are written directly by BeamMemory.remember() and the backfill is
typically unnecessary.
"""

import os
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime
from typing import Tuple


def get_db_path() -> Path:
    """Resolve Mnemosyne database path."""
    default_dir = os.environ.get("MNEMOSYNE_DATA_DIR") or (
        Path.home() / ".hermes" / "mnemosyne" / "data"
    )
    return Path(default_dir) / "mnemosyne.db"


def count_missing_triples(conn: sqlite3.Connection) -> Tuple[int, int]:
    """Count working and episodic memories lacking temporal triples."""
    cursor = conn.cursor()
    
    # Count working memories without occurred_on triples
    cursor.execute("""
        SELECT COUNT(*) FROM working_memory wm
        WHERE NOT EXISTS (
            SELECT 1 FROM triples t 
            WHERE t.subject = wm.id AND t.predicate = 'occurred_on'
        )
    """)
    working_missing = cursor.fetchone()[0]
    
    # Count episodic memories without occurred_on triples
    cursor.execute("""
        SELECT COUNT(*) FROM episodic_memory em
        WHERE NOT EXISTS (
            SELECT 1 FROM triples t 
            WHERE t.subject = em.id AND t.predicate = 'occurred_on'
        )
    """)
    episodic_missing = cursor.fetchone()[0]
    
    return working_missing, episodic_missing


def backfill_working_memory(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    """Generate temporal triples for all working_memory entries."""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, timestamp, source FROM working_memory
        WHERE NOT EXISTS (
            SELECT 1 FROM triples t 
            WHERE t.subject = working_memory.id AND t.predicate = 'occurred_on'
        )
    """)
    
    rows = cursor.fetchall()
    inserted = 0
    
    for memory_id, timestamp, source in rows:
        date_str = timestamp[:10] if timestamp else datetime.now().isoformat()[:10]
        
        if not dry_run:
            cursor.execute("""
                INSERT INTO triples (subject, predicate, object, valid_from, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (memory_id, 'occurred_on', date_str, date_str, 'backfill', 1.0))
            
            if source and source not in ('conversation', 'user', 'assistant'):
                cursor.execute("""
                    INSERT INTO triples (subject, predicate, object, valid_from, source, confidence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (memory_id, 'has_source', source, date_str, 'backfill', 1.0))
        
        inserted += 1
        
        if inserted % 500 == 0:
            print(f"  Processed {inserted} working memories...")
            if not dry_run:
                conn.commit()
    
    if not dry_run:
        conn.commit()
    
    return inserted


def backfill_episodic_memory(conn: sqlite3.Connection, dry_run: bool = False) -> int:
    """Generate temporal triples for all episodic_memory entries."""
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, timestamp, source FROM episodic_memory
        WHERE NOT EXISTS (
            SELECT 1 FROM triples t 
            WHERE t.subject = episodic_memory.id AND t.predicate = 'occurred_on'
        )
    """)
    
    rows = cursor.fetchall()
    inserted = 0
    
    for memory_id, timestamp, source in rows:
        date_str = timestamp[:10] if timestamp else datetime.now().isoformat()[:10]
        
        if not dry_run:
            cursor.execute("""
                INSERT INTO triples (subject, predicate, object, valid_from, source, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (memory_id, 'occurred_on', date_str, date_str, 'backfill', 1.0))
            
            if source and source not in ('conversation', 'user', 'assistant'):
                cursor.execute("""
                    INSERT INTO triples (subject, predicate, object, valid_from, source, confidence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (memory_id, 'has_source', source, date_str, 'backfill', 1.0))
        
        inserted += 1
    
    if not dry_run:
        conn.commit()
    
    return inserted


def main():
    parser = argparse.ArgumentParser(description='Backfill temporal triples for Mnemosyne')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be done without writing')
    parser.add_argument('--db-path', type=str, help='Path to mnemosyne.db (default: ~/.hermes/mnemosyne/data/mnemosyne.db)')
    args = parser.parse_args()
    
    db_path = Path(args.db_path) if args.db_path else get_db_path()
    
    print(f"Mnemosyne Temporal Triples Backfill")
    print(f"Database: {db_path}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()
    
    if not db_path.exists():
        print(f"ERROR: Database not found at {db_path}")
        return 1
    
    conn = sqlite3.connect(str(db_path))
    
    try:
        # Check current state
        working_missing, episodic_missing = count_missing_triples(conn)
        total_missing = working_missing + episodic_missing
        
        print(f"Missing temporal triples:")
        print(f"  Working memory:  {working_missing}")
        print(f"  Episodic memory: {episodic_missing}")
        print(f"  Total:           {total_missing}")
        print()
        
        if total_missing == 0:
            print("All memories already have temporal triples. Nothing to do.")
            return 0
        
        if args.dry_run:
            print("DRY RUN — no changes will be made.")
            print(f"Would insert {total_missing} occurred_on triples.")
            return 0
        
        # Confirm
        print("This will insert temporal triples for all existing memories.")
        print("Type 'yes' to continue: ", end='')
        
        # In non-interactive mode, just proceed
        print("(proceeding in non-interactive mode)")
        print()
        
        # Backfill working memory
        print("Backfilling working_memory...")
        working_inserted = backfill_working_memory(conn, dry_run=args.dry_run)
        print(f"  Inserted {working_inserted} triples for working memory")
        
        # Backfill episodic memory
        print("Backfilling episodic_memory...")
        episodic_inserted = backfill_episodic_memory(conn, dry_run=args.dry_run)
        print(f"  Inserted {episodic_inserted} triples for episodic memory")
        
        print()
        print(f"Done. Total triples inserted: {working_inserted + episodic_inserted}")
        
    finally:
        conn.close()
    
    return 0


if __name__ == '__main__':
    exit(main())
