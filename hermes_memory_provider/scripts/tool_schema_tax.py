#!/usr/bin/env python3
"""Inventory the interactive Mnemosyne tool-schema tax.

Prints JSON {count, names, est_tokens} for ALL_TOOL_SCHEMAS and exits 0.
est_tokens = len(json.dumps(schemas)) // 4
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _ensure_repo_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def inventory(schemas=None) -> dict:
    _ensure_repo_on_path()
    if schemas is None:
        from hermes_memory_provider import ALL_TOOL_SCHEMAS

        schemas = ALL_TOOL_SCHEMAS
    schemas = list(schemas)
    return {
        "count": len(schemas),
        "names": [schema["name"] for schema in schemas],
        "est_tokens": len(json.dumps(schemas)) // 4,
    }


def main() -> int:
    print(json.dumps(inventory()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
