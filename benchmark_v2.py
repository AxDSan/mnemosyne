#!/usr/bin/env python3
"""
Mnemosyne v2.0 Benchmark Suite
=================================
Measures store latency, recall latency, DB size, and embedding overhead.
Uses a temp directory for the DB (no project pollution).
Reports mean ± std across 3 runs in milliseconds.
"""

import os
import sys
import time
import json
import shutil
import tempfile
import statistics
from pathlib import Path
from datetime import datetime

# Ensure project root is on path
PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT))

from mnemosyne.core.beam import BeamMemory, init_beam
from mnemosyne.core import embeddings

# ── Configuration ────────────────────────────────────────────────────────────
N_RUNS = 3
STORE_SIZES = [100, 1000, 10000]
RECALL_CORPUS_SIZES = [1000, 10000]
EMBED_BATCHES = [1, 10, 100]
WARMUP_MEMORIES = 5

# Sample texts for generating varied memories
SAMPLE_TEXTS = [
    "The user prefers dark mode in all applications and finds light mode straining.",
    "Important meeting scheduled for next Tuesday at 3 PM with the engineering team.",
    "The project deadline has been moved to December 15th due to vendor delays.",
    "User's preferred programming language is Python for data analysis tasks.",
    "The database migration completed successfully with zero downtime last weekend.",
    "Customer reported a critical bug in the payment processing module yesterday.",
    "Team standup meetings are held every Monday, Wednesday, and Friday at 9 AM.",
    "The new API endpoint for user authentication has been deployed to production.",
    "Server monitoring shows CPU usage has been consistently above 80% this week.",
    "The machine learning model achieved 94.7% accuracy on the test dataset.",
    "Documentation for the REST API needs to be updated before the next release.",
    "The caching layer reduced average response times from 450ms to 32ms.",
    "User requested a feature to export data in CSV and JSON formats.",
    "The Kubernetes cluster was upgraded to version 1.28 without any issues.",
    "Memory optimization reduced the application's RAM usage by 40%.",
    "The integration tests now cover 87% of the critical code paths.",
    "A new team member will be joining the backend team starting next month.",
    "The CI/CD pipeline completes in approximately 12 minutes on average.",
    "Security audit identified three medium-severity vulnerabilities to patch.",
    "The GraphQL schema was refactored to support pagination on all queries.",
    "Load testing showed the system can handle 10,000 concurrent connections.",
    "The user's timezone is UTC-5 (Eastern Time) for scheduling purposes.",
    "Redis cache hit rate is currently at 96.3% across all services.",
    "The front-end bundle size was reduced from 2.1MB to 890KB after optimization.",
    "Automated backups run every 6 hours and are retained for 30 days.",
    "The user prefers concise summaries over detailed explanations.",
    "Network latency between the app and database servers averages 2.3ms.",
    "The staging environment mirrors production with 75% of real data volumes.",
    "Code coverage increased from 62% to 78% after the sprint testing push.",
    "The webhook integration with Slack sends alerts for all critical events.",
]

QUERIES = [
    "user preferences for UI settings",
    "meeting schedule and calendar events",
    "database performance issues",
    "API endpoint configuration",
    "security vulnerabilities and patches",
    "machine learning model accuracy",
    "deployment and release timeline",
    "team member updates",
    "caching strategy optimization",
    "testing and code coverage",
]


def generate_text(idx: int) -> str:
    """Generate unique memory text from index."""
    base = SAMPLE_TEXTS[idx % len(SAMPLE_TEXTS)]
    return f"{base} [ref-{idx:06d}]"


def fresh_db() -> tuple:
    """Create a fresh BeamMemory with a temp DB path. Returns (beam, db_dir)."""
    db_dir = tempfile.mkdtemp(prefix="mnemosyne_bench_")
    db_path = Path(db_dir) / "bench.db"
    init_beam(db_path)
    beam = BeamMemory(db_path=str(db_path))
    return beam, db_dir


def cleanup(beam, db_dir):
    """Close connection and remove temp dir."""
    try:
        beam.conn.close()
    except Exception:
        pass
    shutil.rmtree(db_dir, ignore_errors=True)


def fmt_ms(mean_val, std_val, unit="ms"):
    """Format mean ± std in milliseconds."""
    return f"{mean_val:.2f} ± {std_val:.2f}"


def fmt_size(size_bytes):
    """Format bytes as human-readable."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


# ── Benchmark 1: Store Latency ───────────────────────────────────────────────
def bench_store_latency():
    """Measure time for beam.remember() at different corpus sizes."""
    print("\n" + "=" * 70)
    print("BENCHMARK 1: Store Latency (beam.remember)")
    print("=" * 70)

    results = {}
    for size in STORE_SIZES:
        run_times = []
        for run in range(N_RUNS):
            beam, db_dir = fresh_db()
            try:
                # Warmup
                for i in range(WARMUP_MEMORIES):
                    beam.remember(f"warmup {i}", source="bench_warmup")

                # Timed store
                start = time.perf_counter()
                for i in range(size):
                    beam.remember(
                        generate_text(i),
                        source="bench_store",
                        importance=0.5 + (i % 5) * 0.1,
                    )
                elapsed = time.perf_counter() - start
                run_times.append(elapsed)
                print(f"  Store {size:>5} memories, run {run+1}: {elapsed*1000:.1f} ms "
                      f"({elapsed/size*1000:.3f} ms/mem)")
            finally:
                cleanup(beam, db_dir)

        mean_total = statistics.mean(run_times)
        std_total = statistics.stdev(run_times) if len(run_times) > 1 else 0.0
        mean_per = mean_total / size * 1000
        std_per = std_total / size * 1000
        results[size] = {
            "total_ms": fmt_ms(mean_total * 1000, std_total * 1000),
            "per_mem_ms": fmt_ms(mean_per, std_per),
        }
        print(f"  → Store {size:>5}: total {mean_total*1000:.1f} ± {std_total*1000:.1f} ms, "
              f"per-mem {mean_per:.3f} ± {std_per:.3f} ms")

    return results


# ── Benchmark 2: Recall Latency ──────────────────────────────────────────────
def bench_recall_latency():
    """Measure recall() latency against different corpus sizes."""
    print("\n" + "=" * 70)
    print("BENCHMARK 2: Recall Latency (beam.recall)")
    print("=" * 70)

    results = {}
    for corpus_size in RECALL_CORPUS_SIZES:
        # Build corpus once per run
        run_times = []
        for run in range(N_RUNS):
            beam, db_dir = fresh_db()
            try:
                # Populate corpus
                print(f"  Populating {corpus_size} memories for run {run+1}...", end=" ", flush=True)
                pop_start = time.perf_counter()
                for i in range(corpus_size):
                    beam.remember(
                        generate_text(i),
                        source="bench_recall_corpus",
                        importance=0.3 + (i % 7) * 0.1,
                    )
                pop_time = time.perf_counter() - pop_start
                print(f"done ({pop_time:.1f}s)")

                # Warmup recall
                beam.recall("warmup query", top_k=5)

                # Timed recalls
                recall_times = []
                for query in QUERIES:
                    start = time.perf_counter()
                    beam.recall(query, top_k=5)
                    elapsed = time.perf_counter() - start
                    recall_times.append(elapsed)

                avg_recall = statistics.mean(recall_times)
                run_times.append(avg_recall)
                print(f"  Recall over {corpus_size:>5} corpus, run {run+1}: "
                      f"avg {avg_recall*1000:.2f} ms across {len(QUERIES)} queries")

            finally:
                cleanup(beam, db_dir)

        mean_val = statistics.mean(run_times)
        std_val = statistics.stdev(run_times) if len(run_times) > 1 else 0.0
        results[corpus_size] = {
            "avg_ms": fmt_ms(mean_val * 1000, std_val * 1000),
        }
        print(f"  → Recall @ {corpus_size:>5} corpus: {mean_val*1000:.2f} ± {std_val*1000:.2f} ms")

    return results


# ── Benchmark 3: Memory Footprint ────────────────────────────────────────────
def bench_db_size():
    """Measure DB file size at different corpus sizes."""
    print("\n" + "=" * 70)
    print("BENCHMARK 3: Memory Footprint (DB file size)")
    print("=" * 70)

    results = {}
    for size in [1000, 10000]:
        beam, db_dir = fresh_db()
        try:
            for i in range(size):
                beam.remember(
                    generate_text(i),
                    source="bench_size",
                    importance=0.5,
                )

            db_path = Path(db_dir) / "bench.db"
            db_size = os.path.getsize(db_path)

            # Check for WAL and SHM files
            wal_path = Path(db_dir) / "bench.db-wal"
            shm_path = Path(db_dir) / "bench.db-shm"
            wal_size = os.path.getsize(wal_path) if wal_path.exists() else 0
            shm_size = os.path.getsize(shm_path) if shm_path.exists() else 0

            # Force checkpoint to get realistic main DB size
            beam.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db_size_after = os.path.getsize(db_path)

            per_mem = db_size_after / size
            results[size] = {
                "total": fmt_size(db_size_after),
                "total_bytes": db_size_after,
                "per_mem_bytes": per_mem,
            }
            print(f"  DB @ {size:>5} memories: {fmt_size(db_size_after)} "
                  f"({per_mem:.0f} bytes/mem)")
            if wal_size:
                print(f"    WAL: {fmt_size(wal_size)}, SHM: {fmt_size(shm_size)}")

        finally:
            cleanup(beam, db_dir)

    return results


# ── Benchmark 4: Embedding Overhead ──────────────────────────────────────────
def bench_embedding():
    """Measure embedding time for different batch sizes."""
    print("\n" + "=" * 70)
    print("BENCHMARK 4: Embedding Overhead")
    print("=" * 70)

    if not embeddings.available():
        print("  ⚠ fastembed not available — skipping embedding benchmark")
        return {}

    # Warm up the model
    print("  Warming up embedding model...", end=" ", flush=True)
    embeddings.embed(["warmup text for model loading"])
    print("done")

    results = {}
    for batch_size in EMBED_BATCHES:
        run_times = []
        for run in range(N_RUNS):
            texts = [f"This is benchmark embedding test sentence number {i}." for i in range(batch_size)]
            start = time.perf_counter()
            vecs = embeddings.embed(texts)
            elapsed = time.perf_counter() - start
            run_times.append(elapsed)
            print(f"  Embed batch={batch_size:>3}, run {run+1}: {elapsed*1000:.2f} ms "
                  f"({elapsed/batch_size*1000:.3f} ms/text)")

        mean_val = statistics.mean(run_times)
        std_val = statistics.stdev(run_times) if len(run_times) > 1 else 0.0
        per_text = mean_val / batch_size * 1000
        results[batch_size] = {
            "total_ms": fmt_ms(mean_val * 1000, std_val * 1000),
            "per_text_ms": per_text,
        }
        print(f"  → Embed batch {batch_size:>3}: {mean_val*1000:.2f} ± {std_val*1000:.2f} ms "
              f"({per_text:.3f} ms/text)")

    return results


# ── Summary Table ─────────────────────────────────────────────────────────────
def print_summary(store, recall, db_size, embed):
    """Print a clean markdown summary table."""
    print("\n" + "=" * 70)
    print("SUMMARY TABLE (Mnemosyne v2.0 Benchmark Results)")
    print("=" * 70)
    print(f"Runs: {N_RUNS} | Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Embedding model: BAAI/bge-small-en-v1.5 (384-dim, int8 vectors)")
    print()

    # Store latency
    print("## Store Latency (beam.remember)")
    print()
    print("| Corpus Size | Total Time (ms) | Per-Memory (ms) |")
    print("|------------:|:----------------|:----------------|")
    for size, data in sorted(store.items()):
        print(f"| {size:>11} | {data['total_ms']} | {data['per_mem_ms']} |")
    print()

    # Recall latency
    print("## Recall Latency (beam.recall, top_k=5)")
    print()
    print("| Corpus Size | Avg Query Time (ms) |")
    print("|------------:|:--------------------|")
    for size, data in sorted(recall.items()):
        print(f"| {size:>11} | {data['avg_ms']} |")
    print()

    # DB size
    print("## Memory Footprint (SQLite DB)")
    print()
    print("| Memories | Total Size | Bytes/Memory |")
    print("|---------:|:----------|:------------:|")
    for size, data in sorted(db_size.items()):
        print(f"| {size:>8} | {data['total']} | {data['per_mem_bytes']:.0f} |")
    print()

    # Embedding overhead
    if embed:
        print("## Embedding Overhead (fastembed)")
        print()
        print("| Batch Size | Total Time (ms) | Per-Text (ms) |")
        print("|-----------:|:----------------|:-------------:|")
        for batch, data in sorted(embed.items()):
            print(f"| {batch:>10} | {data['total_ms']} | {data['per_text_ms']:.3f} |")
        print()


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════════════════╗")
    print("║           Mnemosyne v2.0 Benchmark Suite                        ║")
    print("║           Store · Recall · Footprint · Embedding                  ║")
    print("╚══════════════════════════════════════════════════════════════════════╝")
    print(f"  Python:  {sys.version.split()[0]}")
    print(f"  Runs:    {N_RUNS} per benchmark")
    print(f"  Date:    {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    store_results = bench_store_latency()
    recall_results = bench_recall_latency()
    db_size_results = bench_db_size()
    embed_results = bench_embedding()

    print_summary(store_results, recall_results, db_size_results, embed_results)

    print("Benchmark complete.")
