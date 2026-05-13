#!/usr/bin/env python3
"""
Cleanup script for noisy mention annotations in Mnemosyne DB.

Removes annotations whose values are meta-system noise words that leaked
in before the entity extraction stopword fix (PR #120).

Usage:
    python3 scripts/cleanup_noisy_mentions.py [--dry-run] [--db PATH]
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime


DB_DEFAULT = Path.home() / ".hermes" / "mnemosyne" / "data" / "mnemosyne.db"


NOISE_WORDS = frozenset({
    "ASSISTANT", "USER", "SKILL", "Review", "Target", "CLASS",
    "LEVEL", "Signals", "Phase", "API", "Summary", "Added", "Active",
    "Be", "Not", "Whether", "All", "No", "Replying", "AI", "Memory",
    "False", "True", "None", "Signal",
    "Hermes", "Agent", "Model", "System", "Fact", "Mnemosyne",
})


def main():
    dry_run = "--dry-run" in sys.argv
    db_flag = "--db" in sys.argv
    
    if db_flag:
        idx = sys.argv.index("--db")
        if idx + 1 < len(sys.argv):
            db_path = Path(sys.argv[idx + 1])
        else:
            print("Error: --db requires a path argument")
            sys.exit(1)
    else:
        db_path = DB_DEFAULT
    
    if not db_path.exists():
        print(f"Error: database not found at {db_path}")
        sys.exit(1)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Check current state
    cursor.execute("SELECT COUNT(*) FROM annotations WHERE kind='mentions'")
    total = cursor.fetchone()[0]
    print(f"Total mentions before cleanup: {total}")
    
    placeholders = ",".join("?" for _ in NOISE_WORDS)
    cursor.execute(
        f"SELECT value, COUNT(*) as cnt FROM annotations "
        f"WHERE kind='mentions' AND value IN ({placeholders}) "
        f"GROUP BY value ORDER BY cnt DESC",
        list(NOISE_WORDS)
    )
    noisy_rows = cursor.fetchall()
    noisy_total = sum(cnt for _, cnt in noisy_rows)
    
    print(f"Noisy annotations to remove: {noisy_total}")
    print("\nBreakdown by value:")
    for val, cnt in noisy_rows:
        print(f"  {val}: {cnt}")
    
    if dry_run:
        print(f"\n[DRY RUN] Would delete {noisy_total} annotations from {db_path}")
        conn.close()
        return
    
    if noisy_total == 0:
        print("No noisy annotations found. Database is clean.")
        conn.close()
        return
    
    # Create backup
    backup_path = db_path.with_suffix(
        f".pre_stopword_cleanup.{datetime.now().strftime('%Y%m%d%H%M%S')}{db_path.suffix}"
    )
    import shutil
    shutil.copy2(str(db_path), str(backup_path))
    print(f"\nBackup created: {backup_path}")
    
    # Delete noisy annotations
    cursor.execute(
        f"DELETE FROM annotations WHERE kind='mentions' AND value IN ({placeholders})",
        list(NOISE_WORDS)
    )
    conn.commit()
    deleted = cursor.rowcount
    print(f"Deleted {deleted} noisy annotations")
    
    # Verify
    cursor.execute("SELECT COUNT(*) FROM annotations WHERE kind='mentions'")
    remaining = cursor.fetchone()[0]
    print(f"Remaining mentions: {remaining}")
    
    print("\nTop 15 surviving mentions:")
    cursor.execute(
        "SELECT value, COUNT(*) as cnt FROM annotations "
        "WHERE kind='mentions' GROUP BY value ORDER BY cnt DESC LIMIT 15"
    )
    for val, cnt in cursor.fetchall():
        print(f"  {val}: {cnt}")
    
    conn.close()
    print(f"\nCleanup complete. Backup: {backup_path}")


if __name__ == "__main__":
    main()
