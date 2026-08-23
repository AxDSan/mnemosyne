#!/usr/bin/env python3
"""Ship-gate metrics for the Letta-harness extract.

Prints JSON with full/slim/none schema tax, schema drop vs full (and Task 1
baseline 6645), and a synthetic 20-hit prefetch p95. Exits 0 when ship_ok is
true, else 1 so CI can gate.

ship_ok is true iff slim est_tokens <= 0.60 * full est_tokens AND
prefetch_p95_chars <= prefetch_char_limit + omit footer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


BASELINE_EST_TOKENS = 6645
SYNTHETIC_HIT_COUNT = 20
SYNTHETIC_HIT_PAD = 200


def _ensure_repo_on_path() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _inventory(schemas=None) -> dict:
    _ensure_repo_on_path()
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from tool_schema_tax import inventory

    report = inventory(schemas)
    return {"count": report["count"], "est_tokens": report["est_tokens"]}


def _drop_pct(new: int, old: int) -> float:
    if old <= 0:
        return 0.0
    return round((1.0 - (new / old)) * 100.0, 2)


def _synthetic_hits(n: int = SYNTHETIC_HIT_COUNT, pad: int = SYNTHETIC_HIT_PAD) -> list[str]:
    return [
        (
            f"  [2026-05-14T12:00] (importance 0.90) "
            f"Paris is the capital of France hit-{i:02d} {'x' * pad}"
        )
        for i in range(n)
    ]


def synthetic_prefetch_p95(limit: int | None = None) -> tuple[int, int]:
    """Assemble 20 synthetic hits and return (p95_chars, cap_chars).

    A single 20-hit assembly is the worst-case turn shape; its assembled
    length is the p95 sample. Cap is ``limit`` when nothing is omitted,
    otherwise ``limit + newline + footer``.
    """
    _ensure_repo_on_path()
    from hermes_memory_provider import (
        DEFAULT_PREFETCH_CHAR_LIMIT,
        apply_prefetch_char_budget,
        prefetch_omit_footer,
    )

    cap_limit = DEFAULT_PREFETCH_CHAR_LIMIT if limit is None else int(limit)
    block = apply_prefetch_char_budget(_synthetic_hits(), limit=cap_limit)
    p95 = len(block)
    if "more hits omitted; call mnemosyne_recall." in block:
        footer_line = block.splitlines()[-1]
        cap = cap_limit + len("\n") + len(footer_line)
    else:
        cap = cap_limit + len("\n") + len(prefetch_omit_footer(1))
    return p95, cap


def measure() -> dict:
    _ensure_repo_on_path()
    from hermes_memory_provider import DEFAULT_PREFETCH_CHAR_LIMIT
    from hermes_memory_provider.tool_sets import schemas_for_mode

    full = _inventory()
    slim = _inventory(schemas_for_mode("slim"))
    none = _inventory(schemas_for_mode("none"))
    p95, cap = synthetic_prefetch_p95()
    schema_ok = slim["est_tokens"] <= 0.60 * full["est_tokens"]
    prefetch_ok = p95 <= cap
    return {
        "full": full,
        "slim": slim,
        "none": none,
        "baseline_est_tokens": BASELINE_EST_TOKENS,
        "schema_drop_pct": _drop_pct(slim["est_tokens"], full["est_tokens"]),
        "schema_drop_pct_vs_baseline": _drop_pct(slim["est_tokens"], BASELINE_EST_TOKENS),
        "prefetch_char_limit": DEFAULT_PREFETCH_CHAR_LIMIT,
        "prefetch_p95_chars": p95,
        "prefetch_cap_chars": cap,
        "ship_ok": bool(schema_ok and prefetch_ok),
    }


def main() -> int:
    report = measure()
    print(json.dumps(report))
    return 0 if report["ship_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
