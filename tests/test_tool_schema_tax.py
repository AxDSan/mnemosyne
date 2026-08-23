"""Baseline inventory of the interactive Hermes provider tool-schema tax."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from hermes_memory_provider import ALL_TOOL_SCHEMAS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "hermes_memory_provider" / "scripts" / "tool_schema_tax.py"


def test_all_tool_schemas_baseline_count():
    assert ALL_TOOL_SCHEMAS, "ALL_TOOL_SCHEMAS is missing or empty"
    assert len(ALL_TOOL_SCHEMAS) >= 20


def test_tool_schema_tax_script_prints_inventory():
    assert SCRIPT.is_file(), f"missing inventory script: {SCRIPT}"

    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    names = [schema["name"] for schema in ALL_TOOL_SCHEMAS]
    assert report == {
        "count": len(ALL_TOOL_SCHEMAS),
        "names": names,
        "est_tokens": len(json.dumps(ALL_TOOL_SCHEMAS)) // 4,
    }
    assert report["count"] >= 20
