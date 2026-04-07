"""
Mnemosyne Temporal Triples
Time-aware knowledge graph on top of SQLite.
Tracks when facts were true, enabling contradiction detection and historical queries.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

DEFAULT_DB = Path.home() / ".mnemosyne" / "data" / "triples.db"


def _get_conn(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_triples(db_path: Path = None):
    conn = _get_conn(db_path)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_until TEXT,
            source TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_triples_valid_from ON triples(valid_from)")
    
    conn.commit()


class TripleStore:
    """
    Temporal knowledge graph for Mnemosyne.
    
    Example:
        >>> kg = TripleStore()
        >>> kg.add("Maya", "assigned_to", "auth-migration", valid_from="2026-01-15")
        >>> kg.query("Maya", as_of="2026-01-20")
    """
    
    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_DB
        init_triples(self.db_path)
        self.conn = _get_conn(self.db_path)
    
    def add(self, subject: str, predicate: str, object: str,
            valid_from: str = None, source: str = "inferred",
            confidence: float = 1.0) -> int:
        """
        Add a temporal triple. Automatically closes previous matching triples.
        """
        valid_from = valid_from or datetime.now().isoformat()[:10]
        
        # Invalidate previous triples for same (subject, predicate)
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE triples
            SET valid_until = ?
            WHERE subject = ? AND predicate = ? AND valid_until IS NULL
        """, (valid_from, subject, predicate))
        
        # Insert new triple
        cursor.execute("""
            INSERT INTO triples (subject, predicate, object, valid_from, source, confidence)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (subject, predicate, object, valid_from, source, confidence))
        
        self.conn.commit()
        return cursor.lastrowid
    
    def query(self, subject: str = None, predicate: str = None,
              object: str = None, as_of: str = None) -> List[Dict]:
        """
        Query triples, optionally as of a specific date.
        """
        cursor = self.conn.cursor()
        as_of = as_of or datetime.now().isoformat()[:10]
        
        conditions = []
        params = []
        
        if subject:
            conditions.append("subject = ?")
            params.append(subject)
        if predicate:
            conditions.append("predicate = ?")
            params.append(predicate)
        if object:
            conditions.append("object = ?")
            params.append(object)
        
        # Temporal filter: valid at as_of date
        conditions.append("valid_from <= ?")
        params.append(as_of)
        conditions.append("(valid_until IS NULL OR valid_until > ?)")
        params.append(as_of)
        
        where_clause = " AND ".join(conditions)
        cursor.execute(f"SELECT * FROM triples WHERE {where_clause} ORDER BY valid_from DESC", params)
        
        return [dict(row) for row in cursor.fetchall()]
    
    def find_conflicts(self, subject: str, predicate: str,
                       object: str, valid_from: str = None) -> List[Dict]:
        """
        Find contradicting triples for the same subject+predicate.
        """
        valid_from = valid_from or datetime.now().isoformat()[:10]
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT * FROM triples
            WHERE subject = ? AND predicate = ?
            AND object != ?
            AND valid_from <= ?
            AND (valid_until IS NULL OR valid_until > ?)
        """, (subject, predicate, object, valid_from, valid_from))
        
        return [dict(row) for row in cursor.fetchall()]
    
    def get_history(self, subject: str, predicate: str) -> List[Dict]:
        """Get full timeline of a subject+predicate pair."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM triples
            WHERE subject = ? AND predicate = ?
            ORDER BY valid_from DESC
        """, (subject, predicate))
        return [dict(row) for row in cursor.fetchall()]
