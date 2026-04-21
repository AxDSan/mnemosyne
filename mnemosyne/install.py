"""
Mnemosyne Hermes Installer
==========================

One-command setup for Mnemosyne as a Hermes MemoryProvider.

Usage:
    python -m mnemosyne.install
    # or after pip install:
    mnemosyne-install
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _get_mnemosyne_root() -> Path:
    """Return the absolute path to the Mnemosyne repo root."""
    # This file is at mnemosyne/install.py, so parent.parent is repo root
    return Path(__file__).resolve().parent.parent


def _get_hermes_home() -> Path:
    """Return the Hermes home directory, or None if not found."""
    # Check env var first
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env)
    # Default location
    default = Path.home() / ".hermes"
    if default.exists():
        return default
    return None


def _get_hermes_agent_path() -> Path | None:
    """Try to find the hermes-agent installation."""
    # Check common locations
    candidates = [
        Path.home() / ".hermes" / "hermes-agent",
        Path.home() / "hermes-agent",
        Path("/opt/hermes/hermes-agent"),
    ]
    for c in candidates:
        if (c / "run_agent.py").exists():
            return c
    return None


def _ensure_symlink() -> bool:
    """Create the symlink from ~/.hermes/plugins/mnemosyne -> hermes_memory_provider."""
    hermes_home = _get_hermes_home()
    if not hermes_home:
        print("❌ Hermes not found. Is Hermes installed?")
        print("   Expected: ~/.hermes/ or $HERMES_HOME set")
        return False

    plugins_dir = hermes_home / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    target = plugins_dir / "mnemosyne"
    source = _get_mnemosyne_root() / "hermes_memory_provider"

    if not source.exists():
        print(f"❌ Mnemosyne MemoryProvider not found at {source}")
        return False

    # Remove existing
    if target.is_symlink() or target.exists():
        if target.is_symlink():
            target.unlink()
        else:
            import shutil
            shutil.rmtree(target)
        print(f"🔄 Removed existing {target}")

    target.symlink_to(source, target_is_directory=True)
    print(f"✅ Symlinked: {target} -> {source}")
    return True


def _configure_hermes() -> bool:
    """Set memory.provider = mnemosyne in Hermes config."""
    hermes_home = _get_hermes_home()
    if not hermes_home:
        return False

    config_path = hermes_home / "config.yaml"

    # Read existing config
    config_text = ""
    if config_path.exists():
        config_text = config_path.read_text(encoding="utf-8")

    # Check if already configured
    if "provider: mnemosyne" in config_text:
        print("✅ Hermes config already has memory.provider = mnemosyne")
        return True

    # Simple append approach (YAML-compatible)
    if "memory:" in config_text:
        # Replace existing memory block
        import re
        # Find memory: block and replace provider
        new_config = re.sub(
            r'(memory:\s*)\n(\s*provider:\s*\S+)?',
            r'\1\n  provider: mnemosyne\n',
            config_text,
            count=1,
        )
        if new_config == config_text:
            # No provider line found, insert one
            new_config = config_text.replace(
                "memory:",
                "memory:\n  provider: mnemosyne"
            )
        config_path.write_text(new_config, encoding="utf-8")
    else:
        # Append memory block
        with open(config_path, "a", encoding="utf-8") as f:
            f.write("\nmemory:\n  provider: mnemosyne\n")

    print(f"✅ Updated {config_path}: memory.provider = mnemosyne")
    return True


def _verify() -> bool:
    """Try to import and verify the provider works."""
    hermes_home = _get_hermes_home()
    if not hermes_home:
        return False

    # Add Hermes to path for verification
    agent_path = _get_hermes_agent_path()
    if agent_path and str(agent_path) not in sys.path:
        sys.path.insert(0, str(agent_path))

    try:
        from plugins.memory import load_memory_provider
        provider = load_memory_provider("mnemosyne")
        if provider and provider.is_available():
            print(f"✅ Provider verified: {provider.name} is_available=True")
            return True
        else:
            print("⚠️  Provider loaded but not available (Mnemosyne core not importable)")
            return False
    except Exception as e:
        print(f"⚠️  Verification skipped: {e}")
        return False


def install():
    """Run the full Mnemosyne Hermes installation."""
    print("🌀 Mnemosyne Hermes Installer")
    print("=" * 40)
    print()

    # Step 1: Symlink
    if not _ensure_symlink():
        print()
        print("❌ Install failed at symlink step.")
        sys.exit(1)

    # Step 2: Configure
    _configure_hermes()

    # Step 3: Verify
    print()
    print("🔍 Verifying...")
    _verify()

    print()
    print("✅ Mnemosyne is ready!")
    print()
    print("Next steps:")
    print("  • Restart Hermes (if running)")
    print("  • Run: hermes memory status")
    print("  • Run: hermes mnemosyne stats")
    print()


def uninstall():
    """Remove Mnemosyne from Hermes."""
    hermes_home = _get_hermes_home()
    if not hermes_home:
        print("❌ Hermes not found.")
        return

    target = hermes_home / "plugins" / "mnemosyne"
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            import shutil
            shutil.rmtree(target)
        print(f"🗑️  Removed {target}")
    else:
        print("ℹ️  Mnemosyne plugin not found in Hermes.")

    # Reset config
    config_path = hermes_home / "config.yaml"
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        if "provider: mnemosyne" in text:
            new_text = text.replace("provider: mnemosyne", "provider: null")
            config_path.write_text(new_text, encoding="utf-8")
            print("✅ Reset memory.provider to null")

    print("\n✅ Mnemosyne uninstalled. Hermes will use built-in memory.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Mnemosyne Hermes Installer")
    parser.add_argument("--uninstall", action="store_true", help="Remove Mnemosyne from Hermes")
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
    else:
        install()
