"""CLI commands for Mnemosyne memory provider.

Available via: hermes mnemosyne <subcommand>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_mnemosyne_root = Path(__file__).resolve().parent.parent
if str(_mnemosyne_root) not in sys.path:
    sys.path.insert(0, str(_mnemosyne_root))


def register_cli(subparser):
    """Register CLI subcommands for ``hermes mnemosyne``."""
    mnemosyne_parser = subparser.add_parser(
        "mnemosyne",
        help="Manage Mnemosyne local memory",
        description="Inspect, consolidate, and manage Mnemosyne native memory.",
    )
    mn_cmds = mnemosyne_parser.add_subparsers(dest="mnemosyne_cmd")

    mn_cmds.add_parser("stats", help="Show memory statistics")
    mn_cmds.add_parser("sleep", help="Run consolidation cycle")

    inspect_cmd = mn_cmds.add_parser("inspect", help="Search memories")
    inspect_cmd.add_argument("query", nargs="?", default="", help="Search query")
    inspect_cmd.add_argument("--limit", type=int, default=10, help="Max results")

    mn_cmds.add_parser("clear", help="Clear scratchpad")
    mnemosyne_parser.set_defaults(func=mnemosyne_command)


def mnemosyne_command(args):
    """Dispatch ``hermes mnemosyne <subcommand>``."""
    cmd = getattr(args, "mnemosyne_cmd", None)
    if not cmd:
        print("Usage: hermes mnemosyne {stats|sleep|inspect|clear}")
        return 1

    try:
        from mnemosyne.core.beam import BeamMemory
        beam = BeamMemory(session_id="hermes_default")
    except Exception as e:
        print(f"Error: Mnemosyne not available: {e}")
        return 1

    if cmd == "stats":
        working = beam.get_working_stats()
        episodic = beam.get_episodic_stats()
        print(json.dumps({"working": working, "episodic": episodic}, indent=2))

    elif cmd == "sleep":
        beam.sleep()
        working = beam.get_working_stats()
        episodic = beam.get_episodic_stats()
        print(f"Consolidation complete. Working: {working.get('count', 0)}, Episodic: {episodic.get('count', 0)}")

    elif cmd == "inspect":
        query = getattr(args, "query", "") or ""
        limit = getattr(args, "limit", 10)
        if not query:
            query = input("Search query: ")
        results = beam.recall(query, top_k=limit)
        print(f"Results for '{query}': {len(results)}")
        for i, r in enumerate(results, 1):
            content = r.get("content", "")[:120]
            imp = r.get("importance", 0.0)
            print(f"  {i}. [{imp:.2f}] {content}")

    elif cmd == "clear":
        confirm = input("Clear scratchpad? This cannot be undone. [y/N]: ")
        if confirm.lower() in ("y", "yes"):
            beam.scratchpad_clear()
            print("Scratchpad cleared.")
        else:
            print("Cancelled.")

    return 0
