"""
Mnemosyne BEAM Architecture
============================
Bilevel Episodic-Associative Memory

Three SQLite tables:
- working_memory: hot, recent context (auto-injected into prompts)
- episodic_memory: long-term storage with native vector + FTS5 search
- scratchpad: temporary agent reasoning workspace

Native sqlite-vec for vector search.
FTS5 for full-text retrieval.
Hybrid ranking: 50% vector + 30% FTS rank + 20% importance.
"""

import sqlite3
import json
import hashlib
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path

from mnemosyne.core import embeddings as _embeddings

# sqlite-vec optional dependency
try:
    import sqlite_vec
    _SQLITE_VEC_AVAILABLE = True
except Exception:
    _SQLITE_VEC_AVAILABLE = False

_thread_local = threading.local()

DEFAULT_DATA_DIR = Path.home() / ".mnemosyne" / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "mnemosyne.db"

import os
if os.environ.get("MNEMOSYNE_DATA_DIR"):
    DEFAULT_DATA_DIR = Path(os.environ.get("MNEMOSYNE_DATA_DIR"))
    DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "mnemosyne.db"

# Config
EMBEDDING_DIM = 384  # bge-small-en-v1.5
WORKING_MEMORY_MAX_ITEMS = 100
WORKING_MEMORY_TTL_HOURS = 24


def _get_connection(db_path: Path = None) -> sqlite3.Connection:
    """Get thread-local database connection with extensions loaded."""
    path = db_path or DEFAULT_DB_PATH
    if not hasattr(_thread_local, 'conn') or _thread_local.conn is None or getattr(_thread_local, 'db_path', None) != str(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if _SQLITE_VEC_AVAILABLE:
            try:
                conn.enable_load_extension(True)
                sqlite_vec.load(conn)
            except Exception:
                pass  # Some environments don't support load_extension
        _thread_local.conn = conn
        _thread_local.db_path = str(path)
    return _thread_local.conn


def init_beam(db_path: Path = None):
    """Initialize BEAM schema."""
    conn = _get_connection(db_path)
    cursor = conn.cursor()

    # --- WORKING MEMORY ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS working_memory (
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_session ON working_memory(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_timestamp ON working_memory(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_wm_source ON working_memory(source)")

    # --- EPISODIC MEMORY ---
    # rowid is INTEGER PRIMARY KEY for sqlite-vec join
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS episodic_memory (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT UNIQUE NOT NULL,
            content TEXT NOT NULL,
            source TEXT,
            timestamp TEXT,
            session_id TEXT DEFAULT 'default',
            importance REAL DEFAULT 0.5,
            metadata_json TEXT,
            summary_of TEXT DEFAULT '',  -- comma-separated working_memory ids
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_em_session ON episodic_memory(session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_em_timestamp ON episodic_memory(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_em_source ON episodic_memory(source)")

    # --- SCRATCHPAD ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scratchpad (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            session_id TEXT DEFAULT 'default',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sp_session ON scratchpad(session_id)")

    # --- sqlite-vec VIRTUAL TABLE ---
    if _SQLITE_VEC_AVAILABLE:
        try:
            cursor.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_episodes USING vec0(
                    embedding float[{EMBEDDING_DIM}]
                )
            """)
        except sqlite3.OperationalError:
            pass  # May already exist or extension not loadable

    # --- FTS5 VIRTUAL TABLE ---
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_episodes USING fts5(
            content,
            content='episodic_memory',
            content_rowid='rowid'
        )
    """)

    # --- FTS5 Triggers ---
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS em_ai AFTER INSERT ON episodic_memory BEGIN
            INSERT INTO fts_episodes(rowid, content) VALUES (new.rowid, new.content);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS em_ad AFTER DELETE ON episodic_memory BEGIN
            INSERT INTO fts_episodes(fts_episodes, rowid, content) VALUES ('delete', old.rowid, old.content);
        END
    """)
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS em_au AFTER UPDATE ON episodic_memory BEGIN
            INSERT INTO fts_episodes(fts_episodes, rowid, content) VALUES ('delete', old.rowid, old.content);
            INSERT INTO fts_episodes(rowid, content) VALUES (new.rowid, new.content);
        END
    """)

    # --- Consolidation Log ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS consolidation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            items_consolidated INTEGER,
            summary_preview TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()


def _generate_id(content: str) -> str:
    return hashlib.sha256(f"{content}{datetime.now().isoformat()}".encode()).hexdigest()[:16]


def _vec_available(conn: sqlite3.Connection) -> bool:
    if not _SQLITE_VEC_AVAILABLE:
        return False
    try:
        conn.execute("SELECT 1 FROM vec_episodes LIMIT 0")
        return True
    except Exception:
        return False


def _vec_insert(conn: sqlite3.Connection, rowid: int, embedding: List[float]):
    """Insert embedding into sqlite-vec table."""
    emb_json = json.dumps(embedding)
    conn.execute("INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)", (rowid, emb_json))


def _vec_search(conn: sqlite3.Connection, embedding: List[float], k: int = 20) -> List[Dict]:
    """Search sqlite-vec and return rowids with distances."""
    emb_json = json.dumps(embedding)
    rows = conn.execute(
        "SELECT rowid, distance FROM vec_episodes WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
        (emb_json, k)
    ).fetchall()
    return [{"rowid": r["rowid"], "distance": r["distance"]} for r in rows]


def _fts_search(conn: sqlite3.Connection, query: str, k: int = 20) -> List[Dict]:
    """Search FTS5 and return rowids with ranks."""
    # Sanitize query for FTS5
    safe_query = " ".join(f'"{w}"' for w in query.split() if w)
    if not safe_query:
        return []
    rows = conn.execute(
        "SELECT rowid, rank FROM fts_episodes WHERE fts_episodes MATCH ? ORDER BY rank LIMIT ?",
        (safe_query, k)
    ).fetchall()
    return [{"rowid": r["rowid"], "rank": r["rank"]} for r in rows]


class BeamMemory:
    """
    BEAM memory interface.
    """

    def __init__(self, session_id: str = "default", db_path: Path = None):
        self.session_id = session_id
        self.db_path = db_path or DEFAULT_DB_PATH
        self.conn = _get_connection(self.db_path)
        init_beam(self.db_path)

    # ------------------------------------------------------------------
    # Working Memory
    # ------------------------------------------------------------------
    def remember(self, content: str, source: str = "conversation",
                 importance: float = 0.5, metadata: Dict = None) -> str:
        """Store into working_memory."""
        memory_id = _generate_id(content)
        timestamp = datetime.now().isoformat()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO working_memory (id, content, source, timestamp, session_id, importance, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (memory_id, content, source, timestamp, self.session_id, importance, json.dumps(metadata or {})))
        self.conn.commit()
        self._trim_working_memory()
        return memory_id

    def _trim_working_memory(self):
        """Keep working_memory within size/time limits."""
        cutoff = (datetime.now() - timedelta(hours=WORKING_MEMORY_TTL_HOURS)).isoformat()
        self.conn.execute("""
            DELETE FROM working_memory
            WHERE session_id = ? AND (
                timestamp < ? OR
                id NOT IN (
                    SELECT id FROM working_memory
                    WHERE session_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                )
            )
        """, (self.session_id, cutoff, self.session_id, WORKING_MEMORY_MAX_ITEMS))
        self.conn.commit()

    def get_context(self, limit: int = 10) -> List[Dict]:
        """Get recent working_memory for prompt injection."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, content, source, timestamp, importance
            FROM working_memory
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (self.session_id, limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_working_stats(self) -> Dict:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM working_memory WHERE session_id = ?", (self.session_id,))
        total = cursor.fetchone()[0]
        cursor.execute("SELECT timestamp FROM working_memory WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1", (self.session_id,))
        last = cursor.fetchone()
        return {"total": total, "last": last[0] if last else None}

    def forget_working(self, memory_id: str) -> bool:
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM working_memory WHERE id = ? AND session_id = ?", (memory_id, self.session_id))
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Episodic Memory
    # ------------------------------------------------------------------
    def consolidate_to_episodic(self, summary: str, source_wm_ids: List[str],
                                source: str = "consolidation", importance: float = 0.6,
                                metadata: Dict = None) -> str:
        """
        Store a consolidated summary into episodic_memory with optional embedding.
        """
        memory_id = _generate_id(summary)
        timestamp = datetime.now().isoformat()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO episodic_memory (id, content, source, timestamp, session_id, importance, metadata_json, summary_of)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (memory_id, summary, source, timestamp, self.session_id, importance,
              json.dumps(metadata or {}), ",".join(source_wm_ids)))
        rowid = cursor.lastrowid

        if _embeddings.available():
            vec = _embeddings.embed([summary])
            if vec is not None and _vec_available(self.conn):
                _vec_insert(self.conn, rowid, vec[0].tolist())

        self.conn.commit()
        return memory_id

    def recall(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        Hybrid recall across working_memory + episodic_memory.
        Uses sqlite-vec + FTS5 for episodic, keyword overlap for working.
        """
        query_lower = query.lower()
        query_words = query_lower.split()
        results = []

        # ---- Working memory (keyword + recency) ----
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, content, source, timestamp, importance
            FROM working_memory
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT 200
        """, (self.session_id,))
        for row in cursor.fetchall():
            content_lower = row["content"].lower()
            exact = sum(1 for w in query_words if w in content_lower)
            partial = sum(1 for w in query_words for cw in content_lower.split() if w in cw or cw in w)
            relevance = (exact * 1.0 + partial * 0.3) / max(len(query_words), 1)
            if relevance > 0.05:
                score = relevance * 0.35 + row["importance"] * 0.2
                results.append({
                    "id": row["id"],
                    "content": row["content"][:500],
                    "source": row["source"],
                    "timestamp": row["timestamp"],
                    "tier": "working",
                    "score": round(score, 4),
                    "keyword_score": round(relevance, 4),
                    "dense_score": 0.0,
                    "fts_score": 0.0,
                    "importance": row["importance"]
                })

        # ---- Episodic memory (vec + FTS5 hybrid) ----
        vec_results = {}
        if _embeddings.available() and _vec_available(self.conn):
            emb_result = _embeddings.embed([query])
            if emb_result is not None:
                vec_rows = _vec_search(self.conn, emb_result[0].tolist(), k=max(top_k * 3, 20))
                for vr in vec_rows:
                    # distance is cosine distance in sqlite-vec
                    sim = max(0.0, 1.0 - vr["distance"])
                    vec_results[vr["rowid"]] = sim

        fts_results = {}
        fts_rows = _fts_search(self.conn, query, k=max(top_k * 3, 20))
        if fts_rows:
            # rank from FTS5 is bm25; lower is better. Normalize roughly.
            min_rank = min(r["rank"] for r in fts_rows)
            max_rank = max(r["rank"] for r in fts_rows)
            rng = max_rank - min_rank if max_rank != min_rank else 1.0
            for fr in fts_rows:
                normalized = 1.0 - ((fr["rank"] - min_rank) / rng)
                fts_results[fr["rowid"]] = normalized

        # Fetch episodic rows that appeared in either search
        episodic_rowids = set(vec_results.keys()) | set(fts_results.keys())
        if episodic_rowids:
            placeholders = ",".join("?" * len(episodic_rowids))
            cursor.execute(f"""
                SELECT rowid, id, content, source, timestamp, importance
                FROM episodic_memory
                WHERE rowid IN ({placeholders}) AND session_id = ?
            """, (*tuple(episodic_rowids), self.session_id))
            for row in cursor.fetchall():
                rid = row["rowid"]
                sim = vec_results.get(rid, 0.0)
                fts = fts_results.get(rid, 0.0)
                # Hybrid: 50% vec, 30% fts, 20% importance
                score = sim * 0.5 + fts * 0.3 + row["importance"] * 0.2
                results.append({
                    "id": row["id"],
                    "content": row["content"][:500],
                    "source": row["source"],
                    "timestamp": row["timestamp"],
                    "tier": "episodic",
                    "score": round(score, 4),
                    "keyword_score": 0.0,
                    "dense_score": round(sim, 4),
                    "fts_score": round(fts, 4),
                    "importance": row["importance"]
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def get_episodic_stats(self) -> Dict:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM episodic_memory WHERE session_id = ?", (self.session_id,))
        total = cursor.fetchone()[0]
        cursor.execute("SELECT timestamp FROM episodic_memory WHERE session_id = ? ORDER BY timestamp DESC LIMIT 1", (self.session_id,))
        last = cursor.fetchone()
        vec_count = 0
        if _vec_available(self.conn):
            try:
                vec_count = cursor.execute("SELECT COUNT(*) FROM vec_episodes").fetchone()[0]
            except Exception:
                pass
        return {"total": total, "last": last[0] if last else None, "vectors": vec_count}

    # ------------------------------------------------------------------
    # Scratchpad
    # ------------------------------------------------------------------
    def scratchpad_write(self, content: str) -> str:
        pad_id = _generate_id(content)
        ts = datetime.now().isoformat()
        self.conn.execute("""
            INSERT INTO scratchpad (id, content, session_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at
        """, (pad_id, content, self.session_id, ts, ts))
        self.conn.commit()
        return pad_id

    def scratchpad_read(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, content, created_at, updated_at
            FROM scratchpad
            WHERE session_id = ?
            ORDER BY updated_at DESC
            LIMIT 50
        """, (self.session_id,))
        return [dict(row) for row in cursor.fetchall()]

    def scratchpad_clear(self):
        self.conn.execute("DELETE FROM scratchpad WHERE session_id = ?", (self.session_id,))
        self.conn.commit()

    # ------------------------------------------------------------------
    # Consolidation / Sleep
    # ------------------------------------------------------------------
    def sleep(self, dry_run: bool = False) -> Dict:
        """
        Consolidate old working_memory into episodic_memory summaries.
        Returns summary of what was done.
        """
        from mnemosyne.core.aaak import encode as aaak_encode

        cursor = self.conn.cursor()
        # Find working memories older than TTL/2 that haven't been consolidated
        cutoff = (datetime.now() - timedelta(hours=WORKING_MEMORY_TTL_HOURS // 2)).isoformat()
        cursor.execute("""
            SELECT id, content, source, timestamp, importance, metadata_json
            FROM working_memory
            WHERE session_id = ? AND timestamp < ?
            ORDER BY timestamp ASC
            LIMIT 20
        """, (self.session_id, cutoff))
        rows = cursor.fetchall()
        if not rows:
            return {"status": "no_op", "message": "No old working memories to consolidate"}

        # Simple batch summarization: group by source, concatenate, compress
        grouped: Dict[str, List[Dict]] = {}
        for row in rows:
            grouped.setdefault(row["source"], []).append(dict(row))

        consolidated_ids = []
        summaries_created = 0
        for source, items in grouped.items():
            # Build a concise summary
            lines = [item["content"] for item in items]
            combined = " | ".join(lines)
            # Compress with AAAK
            compressed = aaak_encode(combined)
            summary = f"[{source}] {compressed}"
            ids = [item["id"] for item in items]
            if not dry_run:
                self.consolidate_to_episodic(
                    summary=summary,
                    source_wm_ids=ids,
                    source="sleep_consolidation",
                    importance=0.6,
                    metadata={"original_count": len(items), "source": source}
                )
                # Remove consolidated working memories
                placeholders = ",".join("?" * len(ids))
                cursor.execute(f"DELETE FROM working_memory WHERE id IN ({placeholders})", ids)
                self.conn.commit()
            consolidated_ids.extend(ids)
            summaries_created += 1

        if not dry_run:
            cursor.execute("""
                INSERT INTO consolidation_log (session_id, items_consolidated, summary_preview)
                VALUES (?, ?, ?)
            """, (self.session_id, len(consolidated_ids), f"{summaries_created} summaries from {len(consolidated_ids)} items"))
            self.conn.commit()

        return {
            "status": "dry_run" if dry_run else "consolidated",
            "items_consolidated": len(consolidated_ids),
            "summaries_created": summaries_created,
            "consolidated_ids": consolidated_ids
        }

    def get_consolidation_log(self, limit: int = 10) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT id, session_id, items_consolidated, summary_preview, created_at
            FROM consolidation_log
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (self.session_id, limit))
        return [dict(row) for row in cursor.fetchall()]
