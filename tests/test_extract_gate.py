"""Ship-gate metrics for the Letta-harness extract (schema tax + prefetch cap)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from hermes_memory_provider import DEFAULT_PREFETCH_CHAR_LIMIT, prefetch_omit_footer
from hermes_memory_provider.scripts.tool_schema_tax import inventory
from hermes_memory_provider.tool_sets import schemas_for_mode


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "hermes_memory_provider" / "scripts" / "extract_gate.py"
BASELINE_EST_TOKENS = 6645


def _load_extract_gate():
    spec = importlib.util.spec_from_file_location("extract_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_gate() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )


def test_slim_schema_tax_is_at_least_40_percent_below_full():
    full = inventory()
    slim = inventory(schemas_for_mode("slim"))
    assert full["est_tokens"] > 0
    assert slim["est_tokens"] <= 0.60 * full["est_tokens"]


def test_prefetch_synthetic_p95_is_under_limit_plus_footer():
    gate = _load_extract_gate()
    p95, cap = gate.synthetic_prefetch_p95()
    assert p95 <= cap
    assert cap >= DEFAULT_PREFETCH_CHAR_LIMIT + len(prefetch_omit_footer(1))


def test_extract_gate_script_json_has_ship_ok():
    assert SCRIPT.is_file(), f"missing extract gate script: {SCRIPT}"

    completed = _run_gate()
    report = json.loads(completed.stdout)
    assert "ship_ok" in report
    assert isinstance(report["ship_ok"], bool)
    assert completed.returncode == (0 if report["ship_ok"] else 1)

    full = inventory()
    slim = inventory(schemas_for_mode("slim"))
    none = inventory(schemas_for_mode("none"))
    assert report["full"] == {"count": full["count"], "est_tokens": full["est_tokens"]}
    assert report["slim"] == {"count": slim["count"], "est_tokens": slim["est_tokens"]}
    assert report["none"] == {"count": none["count"], "est_tokens": none["est_tokens"]}
    assert report["prefetch_char_limit"] == DEFAULT_PREFETCH_CHAR_LIMIT
    assert report["baseline_est_tokens"] == BASELINE_EST_TOKENS
    assert isinstance(report["schema_drop_pct"], (int, float))
    assert isinstance(report["prefetch_p95_chars"], int)
    assert report["ship_ok"] is (
        slim["est_tokens"] <= 0.60 * full["est_tokens"]
        and report["prefetch_p95_chars"] <= report["prefetch_cap_chars"]
    )
