"""
Mnemosyne Core - Direct SQLite Integration
No HTTP, no server, just pure Python + SQLite

This is the heart of Mnemosyne — a zero-dependency memory system
that delivers sub-millisecond performance through direct SQLite access.

Now upgraded with BEAM architecture:
- working_memory: hot context auto-injected into prompts
- episodic_memory: long-term storage with sqlite-vec + FTS5
- scratchpad: temporary agent reasoning workspace
"""

import sqlite3
import json
import hashlib
import threading
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

from mnemosyne.core import embeddings as _embeddings
from mnemosyne.core.beam import BeamMemory, init_beam, _get_connection as _beam_get_connection

# Single shared connection per thread (legacy path)
_thread_local = threading.local()

# Default data directory
DEFAULT_DATA_DIR = Path.home() / ".mnemosyne" / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "mnemosyne.db"

# Allow override via environment
import os
if os.environ.get("MNEMOSYNE_DATA_DIR"):
    DEFAULT_DATA_DIR = Path(os.environ.get("MNEMOSYNE_DATA_DIR"))
    DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "mnemosyne.db"


def _get_connection(db_path: Path = None) -> sqlite3.Connection:
    """Get thread-local database connection"""
    path = db_path or DEFAULT_DB_PATH
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None or getattr(_thread_local, 'db_path', None) != str(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        _thread_local.conn = sqlite3.connect(str(path), check_same_thread=False)
        _thread_local.conn.row_factory = sqlite3.Row
        _thread_local.db_path = str(path)
    return _thread_local.conn


def init_db(db_path: Path = None):
    """Initialize legacy database schema + BEAM schema"""
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # Legacy memories table (kept for backward compatibility)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            source TEXT,
            timestamp TEXT,
            session_id TEXT DEFAULT 'default',
            importance REAL DEFAULT 0.5,
            metadata_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_session ON memories(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON memories(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_source ON memories(source)")

    # Legacy embeddings table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            memory_id TEXT PRIMARY KEY,
            embedding_json TEXT NOT NULL,
            model TEXT DEFAULT 'bge-small-en-v1.5',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
        )
    """)

    conn.commit()

    # Initialize BEAM schema on same DB
    init_beam(db_path)


# Initialize on module load
init_db()


def generate_id(content: str) -> str:
    """Generate unique ID for memory"""
    return hashlib.sha256(f"{content}{datetime.now().isoformat()}".encode()).hexdigest()[:16]


def calculate_relevance(query_words: List[str], content: str) -> float:
    """Calculate relevance score between query and content."""
    content_lower = content.lower()
    content_words = set(content_lower.split())

    exact_matches = sum(1 for word in query_words if word.lower() in content_words)
    partial_matches = sum(
        1 for word in query_words
        for content_word in content_words
        if word.lower() in content_word or content_word in word.lower()
    )

    score = (exact_matches * 1.0 + partial_matches * 0.3) / max(len(query_words), 1)
    return min(score, 1.0)


class Mnemosyne:
    """
    Native memory interface - no HTTP, direct SQLite.
    Now backed by BEAM architecture for scalable retrieval.
    """

    def __init__(self, session_id: str = "default", db_path: Path = None):
        self.session_id = session_id
        self.db_path = db_path or DEFAULT_DB_PATH
        self.conn = _get_connection(self.db_path)
        init_db(self.db_path)
        self.beam = BeamMemory(session_id=session_id, db_path=db_path)

    def remember(self, content: str, source: str = "conversation",
                 importance: float = 0.5, metadata: Dict = None) -> str:
        """
        Store a memory directly to SQLite.
        Writes to both BEAM working_memory and legacy memories table.
        """
        memory_id = generate_id(content)
        timestamp = datetime.now().isoformat()

        # Legacy dual-write
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO memories (id, content, source, timestamp, session_id, importance, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            memory_id, content, source, timestamp, self.session_id,
            importance, json.dumps(metadata or {})
        ))

        # Legacy embedding store
        if _embeddings.available():
            vec = _embeddings.embed([content])
            if vec is not None:
                cursor.execute("""
                    INSERT OR REPLACE INTO memory_embeddings (memory_id, embedding_json, model)
                    VALUES (?, ?, ?)
                """, (memory_id, _embeddings.serialize(vec[0]), _embeddings._DEFAULT_MODEL))

        self.conn.commit()

        # BEAM write
        self.beam.remember(content, source=source, importance=importance, metadata=metadata)

        return memory_id

    def recall(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Search memories with hybrid relevance scoring.
        Uses BEAM episodic + working memory retrieval (sqlite-vec + FTS5).
        """
        return self.beam.recall(query, top_k=top_k)

    def get_context(self, limit: int = 10) -> List[Dict]:
        """
        Get recent memories from current session for context injection.
        Pulls from BEAM working_memory.
        """
        return self.beam.get_context(limit=limit)

    def get_stats(self) -> Dict:
        """Get memory system statistics (legacy + BEAM)."""
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM memories")
        total_legacy = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT session_id) FROM memories")
        sessions = cursor.fetchone()[0]

        cursor.execute("SELECT source, COUNT(*) FROM memories GROUP BY source")
        sources = {row[0]: row[1] for row in cursor.fetchall()}

        cursor.execute("SELECT timestamp FROM memories ORDER BY timestamp DESC LIMIT 1")
        last = cursor.fetchone()

        beam_wm = self.beam.get_working_stats()
        beam_ep = self.beam.get_episodic_stats()

        return {
            "total_memories": total_legacy,
            "total_sessions": sessions,
            "sources": sources,
            "last_memory": last[0] if last else None,
            "database": str(self.db_path),
            "mode": "beam",
            "beam": {
                "working_memory": beam_wm,
                "episodic_memory": beam_ep
            }
        }

    def forget(self, memory_id: str) -> bool:
        """Delete a memory by ID from legacy table and working_memory."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM memories WHERE id = ? AND session_id = ?",
                      (memory_id, self.session_id))
        self.conn.commit()
        self.beam.forget_working(memory_id)
        return cursor.rowcount > 0

    def update(self, memory_id: str, content: str = None,
               importance: float = None) -> bool:
        """Update an existing memory in legacy table."""
        cursor = self.conn.cursor()

        updates = []
        params = []

        if content is not None:
            updates.append("content = ?")
            params.append(content)

        if importance is not None:
            updates.append("importance = ?")
            params.append(importance)

        if not updates:
            return False

        params.extend([memory_id, self.session_id])
        cursor.execute(
            f"UPDATE memories SET {', '.join(updates)} WHERE id = ? AND session_id = ?",
            params
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # BEAM-specific public methods
    # ------------------------------------------------------------------
    def sleep(self, dry_run: bool = False) -> Dict:
        """Run consolidation sleep cycle."""
        return self.beam.sleep(dry_run=dry_run)

    def scratchpad_write(self, content: str) -> str:
        """Write to scratchpad."""
        return self.beam.scratchpad_write(content)

    def scratchpad_read(self) -> List[Dict]:
        """Read scratchpad entries."""
        return self.beam.scratchpad_read()

    def scratchpad_clear(self):
        """Clear scratchpad."""
        self.beam.scratchpad_clear()

    def consolidation_log(self, limit: int = 10) -> List[Dict]:
        """Get consolidation history."""
        return self.beam.get_consolidation_log(limit=limit)


# Global instance for module-level convenience functions
_default_instance = None


def _get_default():
    """Get or create the default Mnemosyne instance"""
    global _default_instance
    if _default_instance is None:
        _default_instance = Mnemosyne()
    return _default_instance


# Module-level convenience functions
def remember(content: str, source: str = "conversation",
             importance: float = 0.5, metadata: Dict = None) -> str:
    """Store a memory using the global instance"""
    return _get_default().remember(content, source, importance, metadata)


def recall(query: str, top_k: int = 5) -> List[Dict]:
    """Search memories using the global instance"""
    return _get_default().recall(query, top_k)


def get_context(limit: int = 10) -> List[Dict]:
    """Get session context using the global instance"""
    return _get_default().get_context(limit)


def get_stats() -> Dict:
    """Get stats using the global instance"""
    return _get_default().get_stats()


def forget(memory_id: str) -> bool:
    """Delete memory using the global instance"""
    return _get_default().forget(memory_id)


def update(memory_id: str, content: str = None, importance: float = None) -> bool:
    """Update memory using the global instance"""
    return _get_default().update(memory_id, content, importance)


def sleep(dry_run: bool = False) -> Dict:
    """Run consolidation sleep cycle using the global instance"""
    return _get_default().sleep(dry_run=dry_run)


def scratchpad_write(content: str) -> str:
    """Write to scratchpad using the global instance"""
    return _get_default().scratchpad_write(content)


def scratchpad_read() -> List[Dict]:
    """Read scratchpad using the global instance"""
    return _get_default().scratchpad_read()


def scratchpad_clear():
    """Clear scratchpad using the global instance"""
    return _get_default().scratchpad_clear()
