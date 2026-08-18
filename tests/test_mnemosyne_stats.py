#!/usr/bin/env python3
"""
Comprehensive test suite for mnemosyne-stats.py
Runs 30 iterations across edge cases, integration, and stress tests.
"""

import subprocess
import json
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

import pytest


def _safe_count(db, table):
    """Mirror scripts/mnemosyne-stats.py cnt(): missing table -> 0."""
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def _expected_data_dir() -> Path:
    if data_dir := os.environ.get("MNEMOSYNE_DATA_DIR"):
        return Path(data_dir)
    if hermes_home := os.environ.get("HERMES_HOME"):
        return Path(hermes_home).expanduser() / "mnemosyne" / "data"
    return Path.home() / ".hermes" / "mnemosyne" / "data"


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mnemosyne-stats.py"
DB_PATH = _expected_data_dir() / "mnemosyne.db"
SNAP_DIR = Path.home() / ".hermes" / "mnemosyne" / "stats"
WIKI_PATH = Path.home() / "wiki"

# Populated by the module-scoped `_hermetic_stats_env` fixture below. Every
# subprocess invocation of the stats CLI inherits this environment, so tests
# never read or write the developer's real Mnemosyne database or home dirs.
TEST_ENV = None

passed = 0
failed = 0
errors = []

def run(args="", check=True):
    """Run the script and return (exit_code, stdout, stderr)."""
    cmd = f"cd {SCRIPT.parent} && python3 {SCRIPT} {args}"
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30, env=TEST_ENV)
    return result.returncode, result.stdout, result.stderr

def _seed_stats_db(db_path, wiki_path):
    """Create a small deterministic database and wiki for the stats CLI to read."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE working_memory (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            source TEXT,
            timestamp TEXT,
            session_id TEXT DEFAULT 'default',
            importance REAL DEFAULT 0.5,
            metadata_json TEXT,
            veracity TEXT DEFAULT 'unknown',
            recall_count INTEGER DEFAULT 0,
            last_recalled TEXT,
            valid_until TEXT,
            superseded_by TEXT,
            scope TEXT DEFAULT 'global',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE episodic_memory (
            id TEXT UNIQUE NOT NULL,
            content TEXT NOT NULL,
            source TEXT,
            timestamp TEXT,
            session_id TEXT DEFAULT 'default',
            importance REAL DEFAULT 0.5,
            metadata_json TEXT,
            summary_of TEXT DEFAULT '',
            veracity TEXT DEFAULT 'unknown',
            recall_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE triples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_until TEXT,
            source TEXT,
            confidence REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE consolidation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            items_consolidated INTEGER,
            summary_preview TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE memory_embeddings (
            memory_id TEXT PRIMARY KEY,
            embedding_json TEXT NOT NULL,
            model TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        "INSERT INTO working_memory (id, content, source, session_id, importance, veracity, recall_count, scope) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("wm-1", "global hermetic stats seed memory", "test", "default", 0.8, "tool", 3, "global"),
    )
    conn.execute(
        "INSERT INTO working_memory (id, content, source, session_id, importance, veracity, recall_count, scope) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("wm-2", "unrecalled session memory for the stats fixture", "user", "default", 0.2, "stated", 0, "session"),
    )
    conn.execute(
        "INSERT INTO episodic_memory (id, content, source, importance) VALUES (?, ?, ?, ?)",
        ("em-1", "episodic summary of the hermetic stats run", "test", 0.6),
    )
    conn.execute(
        "INSERT INTO triples (subject, predicate, object, valid_from) VALUES (?, ?, ?, ?)",
        ("stats-fixture", "prefers", "hermetic tests", "2024-01-01"),
    )
    conn.execute(
        "INSERT INTO consolidation_log (session_id, items_consolidated, summary_preview) VALUES (?, ?, ?)",
        ("sess-1", 2, "consolidated two fixture memories"),
    )
    conn.execute(
        "INSERT INTO memory_embeddings (memory_id, embedding_json, model) VALUES (?, ?, ?)",
        ("wm-1", "[0.1, 0.2, 0.3]", "test-model"),
    )
    (wiki_path / "memories" / "alpha.md").write_text("# Alpha\n")
    (wiki_path / "concepts" / "beta.md").write_text("# Beta\n")
    conn.commit()
    conn.close()

@pytest.fixture(scope="module", autouse=True)
def _hermetic_stats_env(tmp_path_factory):
    """Point the stats CLI at pytest-owned data, home, and wiki dirs (#783).

    These tests shell out to scripts/mnemosyne-stats.py as a subprocess. Without
    a hermetic environment that subprocess resolves the ambient MNEMOSYNE_DATA_DIR
    / HERMES_HOME / HOME and reads the developer's real database, which makes
    test_rapid_fire flaky under concurrent writers and leaks stored memories into
    test output. This fixture replaces those env vars with tmp dirs, seeds a
    small deterministic database, and re-points the module-level DB_PATH /
    SNAP_DIR / WIKI_PATH that the assertion helpers read.
    """
    global DB_PATH, SNAP_DIR, WIKI_PATH, TEST_ENV
    root = tmp_path_factory.mktemp("stats-env")
    data_dir = root / "mnemosyne-data"
    data_dir.mkdir()
    home = root / "home"
    home.mkdir()
    wiki = home / "wiki"
    (wiki / "memories").mkdir(parents=True)
    (wiki / "concepts").mkdir()

    db_path = data_dir / "mnemosyne.db"
    _seed_stats_db(db_path, wiki)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HERMES_HOME"] = str(root / "hermes-home")
    env["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"

    DB_PATH = db_path
    SNAP_DIR = home / ".hermes" / "mnemosyne" / "stats"
    WIKI_PATH = wiki
    TEST_ENV = env

    yield env
    TEST_ENV = None

def _test(name, fn):
    """Run a test function and track results."""
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  ✓ {name}")
    except AssertionError as e:
        failed += 1
        errors.append((name, str(e)))
        print(f"  ✗ {name}: {e}")
    except Exception as e:
        failed += 1
        errors.append((name, f"EXCEPTION: {e}"))
        print(f"  ✗ {name}: EXCEPTION: {e}")

# ═══════════════════════════════════════════════════════════
# GROUP 1: Normal Operation (10 tests)
# ═══════════════════════════════════════════════════════════

def test_full_dashboard():
    code, out, err = run()
    assert code == 0, f"Exit code {code}: {err}"
    assert "MNEMOSYNE HEALTH DASHBOARD" in out, "Missing dashboard header"
    assert "WORKING MEMORY:" in out, "Missing working memory section"
    assert "QUALITY INDICATORS" in out, "Missing quality indicators"
    assert "RECOMMENDATIONS" in out, "Missing recommendations"

def test_compact_mode():
    code, out, err = run("--compact")
    assert code == 0, f"Exit code {code}: {err}"
    assert "MNEMOSYNE HEALTH DASHBOARD" in out
    # Compact should NOT have source breakdown
    assert "By Source:" not in out, "Compact mode should not show source breakdown"
    # Compact should NOT have top recalled
    assert "TOP RECALLED:" not in out, "Compact mode should not show top recalled"

def test_json_mode():
    code, out, err = run("--json")
    assert code == 0, f"Exit code {code}: {err}"
    data = json.loads(out)
    assert "working_memory" in data, "Missing working_memory in JSON"
    assert "quality_score" in data, "Missing quality_score in JSON"
    assert isinstance(data["working_memory"]["total"], int), "wm_total not int"


def test_json_mode_uses_mnemosyne_data_dir(tmp_path):
    """Stats should read mnemosyne.db from MNEMOSYNE_DATA_DIR when configured."""
    home = tmp_path / "home"
    data_dir = tmp_path / "custom-data"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["MNEMOSYNE_DATA_DIR"] = str(data_dir)
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"

    store = subprocess.run(
        [sys.executable, "-m", "mnemosyne.cli", "store", "stats data dir probe"],
        cwd=str(SCRIPT.parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert store.returncode == 0, store.stderr
    assert (data_dir / "mnemosyne.db").exists()
    assert not (home / ".hermes" / "mnemosyne" / "data" / "mnemosyne.db").exists()

    stats = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=str(SCRIPT.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert stats.returncode == 0, stats.stderr
    payload = json.loads(stats.stdout)
    assert "error" not in payload
    assert payload["working_memory"]["total"] == 1


def test_json_mode_uses_hermes_home_when_data_dir_unset(tmp_path):
    home = tmp_path / "home"
    hermes_home = tmp_path / "hermes-home"
    hermes_db = hermes_home / "mnemosyne" / "data" / "mnemosyne.db"
    default_db = home / ".hermes" / "mnemosyne" / "data" / "mnemosyne.db"
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["HERMES_HOME"] = str(hermes_home)
    env.pop("MNEMOSYNE_DATA_DIR", None)
    env["MNEMOSYNE_NO_EMBEDDINGS"] = "1"

    store = subprocess.run(
        [sys.executable, "-m", "mnemosyne.cli", "store", "stats empty data dir probe"],
        cwd=str(SCRIPT.parent.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert store.returncode == 0, store.stderr
    assert hermes_db.exists()
    assert not default_db.exists()

    stats = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=str(SCRIPT.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert stats.returncode == 0, stats.stderr
    payload = json.loads(stats.stdout)
    assert "error" not in payload
    assert payload["working_memory"]["total"] == 1
    assert not (SCRIPT.parent / "mnemosyne.db").exists()

def test_save_snapshot():
    code, out, err = run("--save-snapshot")
    assert code == 0, f"Exit code {code}: {err}"
    assert "Snapshot saved:" in out, "Missing snapshot confirmation"

def test_trends():
    code, out, err = run("--trends")
    assert code == 0, f"Exit code {code}: {err}"
    # Should either show trends or "No trend data yet"
    assert "TRENDS" in out or "No trend data" in out, "Unexpected trends output"

def test_auto_snapshot():
    """Full dashboard should auto-save snapshot."""
    code, out, err = run()
    assert code == 0, f"Exit code {code}: {err}"
    # Check snapshot was saved
    snaps = sorted(SNAP_DIR.glob("snap_*.json"))
    assert len(snaps) >= 1, "No snapshots found after full run"

def test_health_score_in_output():
    code, out, err = run()
    assert code == 0
    assert "Health:" in out, "Missing health score"
    assert "/7" in out, "Health score not in X/7 format"

def test_db_size_in_output():
    code, out, err = run()
    assert code == 0
    assert "DB:" in out, "Missing DB size"

def test_quality_indicators_section():
    code, out, err = run()
    assert code == 0
    # Should have at least some indicators
    lines = [l for l in out.split('\n') if '✓' in l or '✗' in l]
    assert len(lines) >= 5, f"Expected at least 5 quality indicators, got {len(lines)}"

def test_recommendations_section():
    code, out, err = run()
    assert code == 0
    # Should have recommendations (even if "All systems healthy")
    assert "RECOMMENDATIONS" in out

# ═══════════════════════════════════════════════════════════
# GROUP 2: Edge Cases (10 tests)
# ═══════════════════════════════════════════════════════════

def test_invalid_flag():
    """Unknown flag should still show dashboard."""
    code, out, err = run("--bogus-flag")
    assert code == 0, f"Exit code {code} on invalid flag"
    assert "MNEMOSYNE HEALTH DASHBOARD" in out

def test_multiple_flags():
    """Multiple flags should not crash."""
    code, out, err = run("--compact --json")
    assert code == 0, f"Exit code {code} on multiple flags"
    # JSON should take precedence
    data = json.loads(out)
    assert "working_memory" in data

def test_empty_db_path():
    """Script should handle missing DB gracefully."""
    # Temporarily rename DB
    tmp = DB_PATH.with_suffix(".db.bak")
    if DB_PATH.exists():
        DB_PATH.rename(tmp)
        try:
            code, out, err = run("--json")
            assert code == 0, f"Exit code {code}: {err}"
            payload = json.loads(out)
            assert "error" in payload, "Missing DB should surface an error payload"
        finally:
            tmp.rename(DB_PATH)

def test_corrupted_json_snapshot():
    """Script should handle corrupted snapshot files."""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap_file = SNAP_DIR / "snap_corrupted.json"
    snap_file.write_text("NOT VALID JSON{{{")
    try:
        code, out, err = run("--trends")
        # Should handle gracefully, not crash
        assert code == 0, f"Crashed on corrupted snapshot: {err}"
    finally:
        snap_file.unlink(missing_ok=True)

def test_empty_snapshot_dir():
    """Script should handle empty snapshot directory."""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_dir = SNAP_DIR / "tmp_empty_test"
    tmp_dir.mkdir(exist_ok=True)
    try:
        # The script uses SNAP_DIR, not a configurable path
        # So we can't easily test this without mocking
        # But we can verify the script doesn't crash with current state
        code, out, err = run("--trends")
        assert code == 0
    finally:
        tmp_dir.rmdir()

def test_special_characters_in_memory():
    """SQLite special characters should not crash output."""
    code, out, err = run()
    assert code == 0
    # If any memory has special chars, they should be handled
    assert "Traceback" not in err

def test_large_output():
    """Full dashboard should not produce excessively large output."""
    code, out, err = run()
    assert code == 0
    assert len(out) < 100000, f"Output too large: {len(out)} chars"

def test_json_valid_structure():
    """JSON output should have consistent structure."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    required_keys = ["db_size_mb", "working_memory", "episodic", "triples",
                     "consolidation", "dreamer", "embeddings", "wiki", "quality_score"]
    for key in required_keys:
        assert key in data, f"Missing key: {key}"

def test_snapshot_json_valid():
    """Saved snapshots should be valid JSON."""
    code, out, err = run("--save-snapshot")
    assert code == 0
    snaps = sorted(SNAP_DIR.glob("snap_*.json"))
    assert len(snaps) >= 1
    with open(snaps[-1]) as f:
        data = json.load(f)
    assert "timestamp" in data, "Snapshot missing timestamp"
    assert "quality_score" in data, "Snapshot missing quality_score"

def test_concurrent_access():
    """Two rapid runs should not corrupt snapshot files."""
    code1, _, _ = run("--save-snapshot")
    code2, _, _ = run("--save-snapshot")
    assert code1 == 0 and code2 == 0
    # All snapshots should be valid JSON
    for snap in SNAP_DIR.glob("snap_*.json"):
        with open(snap) as f:
            json.load(f)  # Should not raise

# ═══════════════════════════════════════════════════════════
# GROUP 3: Integration Tests (5 tests)
# ═══════════════════════════════════════════════════════════

def test_wiki_count_matches():
    """Wiki page count should match actual files."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    actual = len(list(WIKI_PATH.rglob("*.md")))
    reported = data["wiki"]["total"]
    assert reported == actual, f"Wiki count mismatch: reported={reported}, actual={actual}"

def test_db_count_matches():
    """Working memory count should match actual DB."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    db = sqlite3.connect(str(DB_PATH))
    actual = _safe_count(db, "working_memory")
    db.close()
    reported = data["working_memory"]["total"]
    assert reported == actual, f"WM count mismatch: reported={reported}, actual={actual}"

def test_episodic_count_matches():
    """Episodic count should match actual DB."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    db = sqlite3.connect(str(DB_PATH))
    actual = _safe_count(db, "episodic_memory")
    db.close()
    reported = data["episodic"]["total"]
    assert reported == actual, f"Episodic mismatch: reported={reported}, actual={actual}"

def test_triples_count_matches():
    """Triple count should match actual DB."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    db = sqlite3.connect(str(DB_PATH))
    actual = _safe_count(db, "triples")
    db.close()
    reported = data["triples"]["total"]
    assert reported == actual, f"Triples mismatch: reported={reported}, actual={actual}"

def test_consolidation_count_matches():
    """Consolidation count should match actual DB."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    db = sqlite3.connect(str(DB_PATH))
    actual = _safe_count(db, "consolidation_log")
    db.close()
    reported = data["consolidation"]["events"]
    assert reported == actual, f"Consolidation mismatch: reported={reported}, actual={actual}"

# ═══════════════════════════════════════════════════════════
# GROUP 4: Stress / Boundary Tests (5 tests)
# ═══════════════════════════════════════════════════════════

def test_rapid_fire():
    """10 rapid runs should all succeed."""
    for i in range(10):
        code, _, err = run("--compact")
        assert code == 0, f"Run {i+1} failed: {err}"

def test_json_pipe_to_python():
    """JSON output should be pipeable to python."""
    code, out, err = run("--json")
    assert code == 0
    # Parse it back
    data = json.loads(out)
    assert isinstance(data, dict)

def test_output_encoding():
    """Output should be valid UTF-8."""
    code, out, err = run()
    assert code == 0
    # Should not have encoding errors
    assert "UnicodeEncodeError" not in err

def test_performance():
    """Dashboard should complete in under 5 seconds."""
    import time
    start = time.time()
    code, _, err = run()
    elapsed = time.time() - start
    assert code == 0
    assert elapsed < 5, f"Dashboard took {elapsed:.1f}s (>5s limit)"

def test_snapshot_growth():
    """Snapshots should not accumulate infinitely (check count)."""
    snaps_before = len(list(SNAP_DIR.glob("snap_*.json")))
    run("--save-snapshot")
    snaps_after = len(list(SNAP_DIR.glob("snap_*.json")))
    assert snaps_after == snaps_before + 1, f"Expected +1 snapshot, got {snaps_after - snaps_before}"

# ═══════════════════════════════════════════════════════════
# GROUP 5: Data Integrity Tests (5 tests)
# ═══════════════════════════════════════════════════════════

def test_importance_distribution_sums():
    """Importance distribution should sum to total."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    dist_sum = sum(data["working_memory"]["importance_dist"].values())
    total = data["working_memory"]["total"]
    assert dist_sum == total, f"Distribution sum {dist_sum} != total {total}"

def test_recall_distribution_sums():
    """Recall distribution should sum to total."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    dist_sum = sum(data["working_memory"]["recall_dist"].values())
    total = data["working_memory"]["total"]
    assert dist_sum == total, f"Recall sum {dist_sum} != total {total}"

def test_noise_pct_calculation():
    """Noise percentage should be correctly calculated."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    wm = data["working_memory"]
    # Verify noise_pct matches manual calculation
    db = sqlite3.connect(str(DB_PATH))
    noise_count = db.execute(
        "SELECT COUNT(*) FROM working_memory WHERE importance<0.3 AND recall_count=0"
    ).fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM working_memory").fetchone()[0]
    db.close()
    expected_pct = round(noise_count / total * 100, 1) if total > 0 else 0
    assert wm["noise_pct"] == expected_pct, f"Noise mismatch: {wm['noise_pct']} != {expected_pct}"

def test_global_count_accuracy():
    """Global count should match DB."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    db = sqlite3.connect(str(DB_PATH))
    actual = db.execute("SELECT COUNT(*) FROM working_memory WHERE scope='global'").fetchone()[0]
    db.close()
    assert data["working_memory"]["global_count"] == actual

def test_quality_score_bounds():
    """Quality score should be between 0 and 7."""
    code, out, err = run("--json")
    assert code == 0
    data = json.loads(out)
    score = data["quality_score"]
    assert 0 <= score <= 7, f"Quality score out of bounds: {score}"

# ═══════════════════════════════════════════════════════════
# RUN ALL TESTS
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("  MNEMOSYNE-STATS.PY TEST SUITE")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n  GROUP 1: Normal Operation (10 tests)")
    print("  " + "─" * 40)
    _test("Full dashboard output", test_full_dashboard)
    _test("Compact mode", test_compact_mode)
    _test("JSON mode", test_json_mode)
    _test("Save snapshot", test_save_snapshot)
    _test("Trends display", test_trends)
    _test("Auto-snapshot on full run", test_auto_snapshot)
    _test("Health score format", test_health_score_in_output)
    _test("DB size in output", test_db_size_in_output)
    _test("Quality indicators section", test_quality_indicators_section)
    _test("Recommendations section", test_recommendations_section)

    print("\n  GROUP 2: Edge Cases (10 tests)")
    print("  " + "─" * 40)
    _test("Invalid flag handling", test_invalid_flag)
    _test("Multiple flags", test_multiple_flags)
    _test("Missing DB path", test_empty_db_path)
    _test("Corrupted snapshot file", test_corrupted_json_snapshot)
    _test("Empty snapshot dir", test_empty_snapshot_dir)
    _test("Special characters in memories", test_special_characters_in_memory)
    _test("Output size reasonable", test_large_output)
    _test("JSON structure valid", test_json_valid_structure)
    _test("Snapshot JSON valid", test_snapshot_json_valid)
    _test("Concurrent access safety", test_concurrent_access)

    print("\n  GROUP 3: Integration (5 tests)")
    print("  " + "─" * 40)
    _test("Wiki count matches files", test_wiki_count_matches)
    _test("WM count matches DB", test_db_count_matches)
    _test("Episodic count matches DB", test_episodic_count_matches)
    _test("Triples count matches DB", test_triples_count_matches)
    _test("Consolidation count matches DB", test_consolidation_count_matches)

    print("\n  GROUP 4: Stress / Boundary (5 tests)")
    print("  " + "─" * 40)
    _test("Rapid fire (10 runs)", test_rapid_fire)
    _test("JSON pipe to python", test_json_pipe_to_python)
    _test("Output encoding (UTF-8)", test_output_encoding)
    _test("Performance (<5s)", test_performance)
    _test("Snapshot growth tracking", test_snapshot_growth)

    print("\n  GROUP 5: Data Integrity (5 tests)")
    print("  " + "─" * 40)
    _test("Importance dist sums to total", test_importance_distribution_sums)
    _test("Recall dist sums to total", test_recall_distribution_sums)
    _test("Noise % calculation correct", test_noise_pct_calculation)
    _test("Global count accuracy", test_global_count_accuracy)
    _test("Quality score in bounds", test_quality_score_bounds)

    # Summary
    print(f"\n{'=' * 60}")
    print(f"  RESULTS: {passed} passed, {failed} failed, {passed+failed} total")
    print(f"{'=' * 60}")

    if errors:
        print("\n  FAILURES:")
        for name, err in errors:
            print(f"    ✗ {name}: {err}")

    sys.exit(0 if failed == 0 else 1)
