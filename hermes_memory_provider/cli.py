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
    mn_cmds = subparser.add_subparsers(dest="mnemosyne_cmd")

    mn_cmds.add_parser("stats", help="Show memory statistics")
    mn_cmds.add_parser("sleep", help="Run consolidation cycle")
    mn_cmds.add_parser("version", help="Show Mnemosyne version")

    inspect_cmd = mn_cmds.add_parser("inspect", help="Search memories")
    inspect_cmd.add_argument("query", nargs="?", default="", help="Search query")
    inspect_cmd.add_argument("--limit", type=int, default=10, help="Max results")

    mn_cmds.add_parser("clear", help="Clear scratchpad")

    export_cmd = mn_cmds.add_parser("export", help="Export all memories to a JSON file")
    export_cmd.add_argument("--output", "-o", type=str, required=True, help="Output JSON file path")

    import_cmd = mn_cmds.add_parser("import", help="Import memories from a JSON file")
    import_cmd.add_argument("--input", "-i", type=str, required=True, help="Input JSON file path")
    import_cmd.add_argument("--force", action="store_true", help="Overwrite existing records")

    subparser.set_defaults(func=mnemosyne_command)


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

    elif cmd == "version":
        from mnemosyne import __version__, __author__
        print(f"Mnemosyne {__version__} by {__author__}")

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

    elif cmd == "export":
        output_path = getattr(args, "output", None)
        if not output_path:
            print("Usage: hermes mnemosyne export --output <path>")
            return 1
        try:
            from mnemosyne.core.memory import Mnemosyne
            mem = Mnemosyne(session_id="hermes_default")
            result = mem.export_to_file(output_path)
            print(f"Exported {result['working_memory_count']} working, {result['episodic_memory_count']} episodic, {result['legacy_memories_count']} legacy, {result['triples_count']} triples to {output_path}")
        except Exception as e:
            print(f"Export failed: {e}")
            return 1

    elif cmd == "import":
        input_path = getattr(args, "input", None)
        force = getattr(args, "force", False)
        if not input_path:
            print("Usage: hermes mnemosyne import --input <path> [--force]")
            return 1
        try:
            from mnemosyne.core.memory import Mnemosyne
            mem = Mnemosyne(session_id="hermes_default")
            stats = mem.import_from_file(input_path, force=force)
            beam_stats = stats.get("beam", {})
            legacy_stats = stats.get("legacy", {})
            triples_stats = stats.get("triples", {})
            print(f"Import complete:")
            print(f"  Working: +{beam_stats.get('working_memory', {}).get('inserted', 0)}")
            print(f"  Episodic: +{beam_stats.get('episodic_memory', {}).get('inserted', 0)}")
            print(f"  Legacy: +{legacy_stats.get('inserted', 0)}")
            print(f"  Triples: +{triples_stats.get('inserted', 0)}")
            if force:
                print(f"  (force mode: overwrites applied)")
        except Exception as e:
            print(f"Import failed: {e}")
            return 1

    return 0
