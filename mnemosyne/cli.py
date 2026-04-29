#!/usr/bin/env python3
"""
MNEMOSYNE - Local AI Memory System
==================================
A self-hosted, vector-based semantic memory that runs parallel to Hermes.
No cloud. No API keys. Completely local and free.

Features:
- Vector semantic search (using sentence-transformers)
- SQLite for metadata and conversation history
- Automatic context retrieval
- Conversation threading
- Importance scoring
- Runs on http://localhost:8090
"""

import os
import json
import sqlite3
import hashlib
import numpy as np
from datetime import datetime
from typing import List, Dict, Optional, Any
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import threading
import time

# Try to import optional dependencies
try:
    from sentence_transformers import SentenceTransformer
    EMBEDDINGS_AVAILABLE = True
except ImportError:
    EMBEDDINGS_AVAILABLE = False
    print("⚠️  sentence-transformers not installed. Using hash-based fallback.")

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from fastapi.responses import JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    print("⚠️  FastAPI not installed. Running in library mode only.")

# Configuration - use MNEMOSYNE_DATA_DIR env var or sensible default
from pathlib import Path as _Path

DATA_DIR = os.environ.get(
    "MNEMOSYNE_DATA_DIR",
    str(_Path.home() / ".hermes" / "mnemosyne" / "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)

DB_PATH = os.path.join(DATA_DIR, "mnemosyne.db")
VECTOR_DIM = 384  # all-MiniLM-L6-v2 dimension

@dataclass
class Memory:
    """A single memory unit"""
    id: str
    content: str
    source: str  # 'telegram', 'file', 'web', 'thought'
    timestamp: str
    session_id: str
    importance: float  # 0.0 - 1.0
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = None
    
    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content[:200] + "..." if len(self.content) > 200 else self.content,
            "source": self.source,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "importance": self.importance,
            "metadata": self.metadata or {}
        }

class MnemosyneCore:
    """
    Core memory engine - SQLite + Vector storage
    """
    
    def __init__(self):
        self.db_path = DB_PATH
        self.embedding_model = None
        self._init_db()
        self._load_embedding_model()
        self._memory_cache = []
        self._lock = threading.Lock()
        
    def _init_db(self):
        """Initialize SQLite database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Memories table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    source TEXT,
                    timestamp TEXT,
                    session_id TEXT,
                    importance REAL DEFAULT 0.5,
                    embedding_json TEXT,  -- Stored as JSON array
                    metadata_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Sessions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    context TEXT,
                    started_at TIMESTAMP,
                    last_accessed TIMESTAMP
                )
            """)
            
            # Keywords index for fast retrieval
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS keywords (
                    word TEXT,
                    memory_id TEXT,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)
            
            # Create indexes
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_keywords_word ON keywords(word)")
            
            conn.commit()
            
    def _load_embedding_model(self):
        """Load sentence transformer model for embeddings"""
        if EMBEDDINGS_AVAILABLE:
            try:
                # Use a small, fast model
                self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                print("🧠 Mnemosyne: Embedding model loaded (all-MiniLM-L6-v2)")
            except Exception as e:
                print(f"⚠️  Could not load embedding model: {e}")
                self.embedding_model = None
    
    def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding vector for text"""
        if self.embedding_model:
            embedding = self.embedding_model.encode(text)
            return embedding.tolist()
        else:
            # Fallback: Use simple hash-based embedding
            # Not semantic, but deterministic
            np.random.seed(int(hashlib.md5(text.encode()).hexdigest(), 16) % (2**32))
            return np.random.randn(VECTOR_DIM).tolist()
    
    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    
    def store(self, content: str, source: str = "unknown", 
              session_id: str = "default", importance: float = 0.5,
              metadata: Dict = None) -> str:
        """
        Store a new memory
        
        Args:
            content: The text content to remember
            source: Where it came from ('telegram', 'file', 'thought')
            session_id: Conversation/session identifier
            importance: How important (0.0-1.0)
            metadata: Additional structured data
            
        Returns:
            memory_id: Unique identifier
        """
        memory_id = hashlib.md5(f"{content}{time.time()}".encode()).hexdigest()[:16]
        timestamp = datetime.now().isoformat()
        
        # Generate embedding
        embedding = self._generate_embedding(content)
        
        # Store in database
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO memories 
                (id, content, source, timestamp, session_id, importance, embedding_json, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory_id, content, source, timestamp, session_id,
                importance, json.dumps(embedding), json.dumps(metadata or {})
            ))
            
            # Extract and store keywords
            words = set(content.lower().split())
            for word in words:
                if len(word) > 3:  # Only significant words
                    cursor.execute("INSERT INTO keywords (word, memory_id) VALUES (?, ?)",
                                 (word, memory_id))
            
            conn.commit()
        
        # Add to cache
        with self._lock:
            self._memory_cache.append({
                "id": memory_id,
                "content": content,
                "embedding": embedding,
                "timestamp": timestamp
            })
        
        return memory_id
    
    def recall(self, query: str, top_k: int = 5, 
               session_id: Optional[str] = None) -> List[Dict]:
        """
        Semantic search for relevant memories
        
        Args:
            query: Search query
            top_k: Number of results
            session_id: Optional filter by session
            
        Returns:
            List of relevant memories with similarity scores
        """
        query_embedding = self._generate_embedding(query)
        
        # Build SQL query
        sql = "SELECT id, content, source, timestamp, session_id, importance, embedding_json FROM memories"
        params = []
        
        if session_id:
            sql += " WHERE session_id = ?"
            params.append(session_id)
        
        sql += " ORDER BY timestamp DESC LIMIT 1000"  # Last 1000 memories for speed
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        
        # Calculate similarities
        results = []
        for row in rows:
            mem_id, content, source, timestamp, sess_id, importance, embedding_json = row
            
            try:
                mem_embedding = json.loads(embedding_json)
                similarity = self._cosine_similarity(query_embedding, mem_embedding)
                
                # Boost by importance and recency
                time_boost = 0.0
                try:
                    age_hours = (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds() / 3600
                    if age_hours < 24:
                        time_boost = 0.2
                    elif age_hours < 168:  # 1 week
                        time_boost = 0.1
                except:
                    pass
                
                final_score = (similarity * 0.7) + (importance * 0.2) + time_boost
                
                results.append({
                    "id": mem_id,
                    "content": content[:300] + "..." if len(content) > 300 else content,
                    "source": source,
                    "timestamp": timestamp,
                    "session_id": sess_id,
                    "similarity": round(similarity, 3),
                    "score": round(final_score, 3),
                    "importance": importance
                })
            except Exception as e:
                continue
        
        # Sort by score and return top_k
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
    
    def get_session_context(self, session_id: str, limit: int = 10) -> List[Dict]:
        """Get recent memories from a specific session"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, content, source, timestamp, importance
                FROM memories WHERE session_id = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (session_id, limit))
            
            rows = cursor.fetchall()
            return [
                {
                    "id": row[0],
                    "content": row[1][:200] + "..." if len(row[1]) > 200 else row[1],
                    "source": row[2],
                    "timestamp": row[3],
                    "importance": row[4]
                }
                for row in rows
            ]
    
    def get_stats(self) -> Dict:
        """Get memory system statistics"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) FROM memories")
            total_memories = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(DISTINCT session_id) FROM memories")
            total_sessions = cursor.fetchone()[0]
            
            cursor.execute("SELECT source, COUNT(*) FROM memories GROUP BY source")
            sources = {row[0]: row[1] for row in cursor.fetchall()}
            
            cursor.execute("""
                SELECT timestamp FROM memories 
                ORDER BY timestamp DESC LIMIT 1
            """)
            last_memory = cursor.fetchone()
            
        return {
            "total_memories": total_memories,
            "total_sessions": total_sessions,
            "sources": sources,
            "last_memory": last_memory[0] if last_memory else None,
            "embedding_model": "all-MiniLM-L6-v2" if self.embedding_model else "hash-fallback",
            "db_path": self.db_path
        }
    
    def forget(self, memory_id: str) -> bool:
        """Delete a specific memory"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            cursor.execute("DELETE FROM keywords WHERE memory_id = ?", (memory_id,))
            conn.commit()
            return cursor.rowcount > 0
    
    def update(self, memory_id: str, content: str = None, 
               importance: float = None, metadata: Dict = None) -> bool:
        """
        Update an existing memory's content, importance, or metadata
        
        Args:
            memory_id: The ID of the memory to update
            content: New content (optional)
            importance: New importance score (optional)
            metadata: New metadata dict (optional)
            
        Returns:
            bool: True if updated successfully
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Check if memory exists
            cursor.execute("SELECT id FROM memories WHERE id = ?", (memory_id,))
            if not cursor.fetchone():
                return False
            
            updates = []
            params = []
            
            if content is not None:
                # Regenerate embedding for new content
                new_embedding = self._generate_embedding(content)
                updates.append("content = ?")
                params.append(content)
                updates.append("embedding_json = ?")
                params.append(json.dumps(new_embedding))
                
                # Update keywords
                cursor.execute("DELETE FROM keywords WHERE memory_id = ?", (memory_id,))
                words = set(content.lower().split())
                for word in words:
                    if len(word) > 3:
                        cursor.execute("INSERT INTO keywords (word, memory_id) VALUES (?, ?)",
                                     (word, memory_id))
            
            if importance is not None:
                updates.append("importance = ?")
                params.append(importance)
            
            if metadata is not None:
                updates.append("metadata_json = ?")
                params.append(json.dumps(metadata))
            
            if updates:
                params.append(memory_id)
                sql = f"UPDATE memories SET {', '.join(updates)} WHERE id = ?"
                cursor.execute(sql, params)
                conn.commit()
                return cursor.rowcount > 0
            
            return True
    
    def consolidate(self):
        """
        Memory consolidation - remove duplicates and low-importance old memories
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Remove very low importance memories older than 30 days
            cursor.execute("""
                DELETE FROM memories 
                WHERE importance < 0.2 
                AND datetime(timestamp) < datetime('now', '-30 days')
            """)
            
            deleted = cursor.rowcount
            conn.commit()
            
        return {"deleted": deleted}


# FastAPI Application
if FASTAPI_AVAILABLE:
    app = FastAPI(
        title="Mnemosyne - Local AI Memory",
        description="Self-hosted semantic memory system",
        version="1.0.0"
    )
    
    # Global instance
    memory_core = MnemosyneCore()
    
    @app.get("/")
    def root():
        return {
            "service": "Mnemosyne",
            "status": "running",
            "version": "1.0.0",
            "embedding_available": EMBEDDINGS_AVAILABLE,
            "stats": memory_core.get_stats()
        }
    
    @app.post("/store")
    def store_memory(content: str, source: str = "api", 
                     session_id: str = "default", importance: float = 0.5):
        """Store a new memory"""
        memory_id = memory_core.store(content, source, session_id, importance)
        return {"id": memory_id, "status": "stored"}
    
    @app.get("/recall")
    def recall_memories(query: str, top_k: int = 5, session_id: str = None):
        """Semantic search for memories"""
        results = memory_core.recall(query, top_k, session_id)
        return {"query": query, "results": results}
    
    @app.get("/session/{session_id}")
    def get_session(session_id: str, limit: int = 10):
        """Get session context"""
        memories = memory_core.get_session_context(session_id, limit)
        return {"session_id": session_id, "memories": memories}
    
    @app.get("/stats")
    def get_stats():
        """Get system stats"""
        return memory_core.get_stats()
    
    @app.delete("/forget/{memory_id}")
    def delete_memory(memory_id: str):
        """Delete a memory"""
        success = memory_core.forget(memory_id)
        return {"deleted": success}
    
    @app.put("/update/{memory_id}")
    def update_memory(memory_id: str, content: str = None, 
                      importance: float = None, metadata: str = None):
        """
        Update an existing memory
        
        Args:
            memory_id: The memory ID to update
            content: New content text
            importance: New importance score (0.0-1.0)
            metadata: JSON string of metadata
        """
        meta_dict = json.loads(metadata) if metadata else None
        success = memory_core.update(memory_id, content, importance, meta_dict)
        return {
            "updated": success,
            "memory_id": memory_id,
            "fields_changed": {
                "content": content is not None,
                "importance": importance is not None,
                "metadata": metadata is not None
            }
        }
    
    @app.post("/consolidate")
    def consolidate_memories():
        """Run memory consolidation"""
        result = memory_core.consolidate()
        return result


def run_server():
    """Run the memory server"""
    if not FASTAPI_AVAILABLE:
        print("❌ FastAPI not available. Cannot run server.")
        return
    
    print("🧠 Mnemosyne Memory Server Starting...")
    print(f"📍 Local database: {DB_PATH}")
    print(f"🌐 API: http://localhost:8090")
    print("\nEndpoints:")
    print("  POST /store         - Store a memory")
    print("  GET  /recall        - Search memories")
    print("  GET  /session/{id}  - Get session context")
    print("  GET  /stats         - System stats")
    print("\nPress Ctrl+C to stop")
    
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")


def run_cli():
    """Run CLI commands for memory management"""
    import sys
    
    if len(sys.argv) < 2:
        print("Usage: python mnemosyne.py <command> [args]")
        print("\nCommands:")
        print("  store <content> [source] [importance]  - Store a memory")
        print("  recall <query> [top_k]                 - Search memories")
        print("  update <memory_id> <new_content>       - Update a memory")
        print("  delete <memory_id>                     - Delete a memory")
        print("  stats                                  - Show statistics")
        print("  diagnose                               - Run PII-safe diagnostics")
        print("  server                                 - Run API server")
        return
    
    command = sys.argv[1]
    core = MnemosyneCore()
    
    if command == "store":
        if len(sys.argv) < 3:
            print("❌ Usage: store <content> [source] [importance]")
            return
        content = sys.argv[2]
        source = sys.argv[3] if len(sys.argv) > 3 else "cli"
        importance = float(sys.argv[4]) if len(sys.argv) > 4 else 0.5
        memory_id = core.store(content, source, importance=importance)
        print(f"✅ Stored: {memory_id}")
    
    elif command == "recall":
        if len(sys.argv) < 3:
            print("❌ Usage: recall <query> [top_k]")
            return
        query = sys.argv[2]
        top_k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        results = core.recall(query, top_k)
        print(f"\n🔍 Results for: {query}\n")
        for r in results:
            print(f"  ID: {r['id']}")
            print(f"  Content: {r['content'][:100]}...")
            print(f"  Score: {r['score']} | Importance: {r['importance']}")
            print()
    
    elif command == "update":
        if len(sys.argv) < 4:
            print("❌ Usage: update <memory_id> <new_content> [importance]")
            return
        memory_id = sys.argv[2]
        content = sys.argv[3]
        importance = float(sys.argv[4]) if len(sys.argv) > 4 else None
        success = core.update(memory_id, content=content, importance=importance)
        if success:
            print(f"✅ Updated memory: {memory_id}")
        else:
            print(f"❌ Memory not found: {memory_id}")
    
    elif command == "delete" or command == "forget":
        if len(sys.argv) < 3:
            print("❌ Usage: delete <memory_id>")
            return
        memory_id = sys.argv[2]
        success = core.forget(memory_id)
        if success:
            print(f"✅ Deleted: {memory_id}")
        else:
            print(f"❌ Memory not found: {memory_id}")
    
    elif command == "stats":
        stats = core.get_stats()
        print("\n📊 Mnemosyne Stats\n")
        print(f"  Total memories: {stats['total_memories']}")
        print(f"  Total sessions: {stats['total_sessions']}")
        print(f"  Embedding model: {stats['embedding_model']}")
        print(f"  Last memory: {stats['last_memory']}")
        print(f"\n  Sources:")
        for source, count in stats['sources'].items():
            print(f"    - {source}: {count}")

    elif command == "diagnose":
        try:
            from mnemosyne.diagnose import run_diagnostics
            result = run_diagnostics()
            print("\n🔍 Mnemosyne Diagnostics\n")
            print(f"  Log file: {result['log_path']}")
            print(f"  Checks passed: {result['checks_passed']}/{result['checks_total']}")
            print(f"  Checks failed: {result['checks_failed']}/{result['checks_total']}")
            if result['key_findings']:
                print(f"\n  Key findings:")
                for finding in result['key_findings']:
                    print(f"    • {finding}")
            else:
                print(f"\n  No issues detected")
        except Exception as e:
            print(f"❌ Diagnostic failed: {e}")

    elif command == "server":
        run_server()

    elif command == "mcp":
        try:
            from mnemosyne.mcp_server import main as mcp_main
            mcp_main()
        except ImportError:
            print("❌ MCP not available. Install with: pip install mnemosyne-memory[mcp]")
            sys.exit(1)

    else:
        print(f"❌ Unknown command: {command}")


if __name__ == "__main__":
    run_cli()
