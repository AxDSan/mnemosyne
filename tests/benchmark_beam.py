"""
Quick benchmark for Mnemosyne BEAM architecture.
Run: PYTHONPATH=. python tests/benchmark_beam.py
"""

import time
import tempfile
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from mnemosyne.core.beam import BeamMemory


def benchmark():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "bench.db"
        beam = BeamMemory(session_id="bench", db_path=db_path)

        n = 500
        print(f"Storing {n} working memories...")
        t0 = time.time()
        for i in range(n):
            beam.remember(f"Task number {i}: solve problem {i * 7}", source="conversation", importance=0.5)
        write_time = (time.time() - t0) / n * 1000
        print(f"  Avg write: {write_time:.3f} ms")

        # Inject old memories so sleep has something to consolidate
        print("Injecting old working memories for consolidation...")
        conn = sqlite3.connect(db_path)
        old_ts = (datetime.now() - timedelta(hours=20)).isoformat()
        for i in range(100):
            conn.execute(
                "INSERT INTO working_memory (id, content, source, timestamp, session_id, importance) VALUES (?, ?, ?, ?, ?, ?)",
                (f"old{i}", f"Old task {i}", "conversation", old_ts, "bench", 0.5)
            )
        conn.commit()
        conn.close()

        print("Running sleep/consolidation...")
        t0 = time.time()
        result = beam.sleep(dry_run=False)
        sleep_time = (time.time() - t0) * 1000
        print(f"  Sleep took: {sleep_time:.1f} ms | status: {result['status']}")

        print("Recalling from hybrid memory...")
        queries = ["solve problem", "task number", "problem 3500", "nonexistent xyz"]
        for q in queries:
            times = []
            for _ in range(10):
                t0 = time.time()
                results = beam.recall(q, top_k=5)
                times.append((time.time() - t0) * 1000)
            avg_time = sum(times) / len(times)
            print(f"  Query '{q}': {avg_time:.2f} ms | {len(results)} results")

        stats = beam.get_working_stats()
        ep_stats = beam.get_episodic_stats()
        print(f"\nWorking memory: {stats}")
        print(f"Episodic memory: {ep_stats}")


if __name__ == "__main__":
    benchmark()
