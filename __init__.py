"""
Mnemosyne Plugin for Hermes Agent
Entry point at repo root for `hermes plugins install` compatibility.
"""

import sys
from pathlib import Path

# Ensure this directory is on path so `hermes_plugin` is discoverable
_repo_root = Path(__file__).resolve().parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Re-export __version__ / __author__ from the inner mnemosyne subpackage so
# `from mnemosyne import __version__` works in either install layout:
#   - Hermes plugin tree: outer `mnemosyne/` is the resolved package, inner
#     `mnemosyne/mnemosyne/` is the subpackage `mnemosyne.mnemosyne`.
#   - pip / repo-direct install: inner `mnemosyne/` is the resolved package
#     directly and this stub is never loaded.
# Without this re-export, `hermes mnemosyne version` (and any other caller
# doing `from mnemosyne import __version__`) crashed with ImportError under
# the Hermes plugin layout. See issue #53.
try:
    from .mnemosyne import __version__, __author__
except ImportError:
    __version__ = "unknown"
    __author__ = "Abdias J"

# Graceful fallback when Hermes framework is not present
# (e.g. pip-only / standalone installs without hermes_plugin)
try:
    from hermes_plugin import register
    __all__ = ["register", "__version__", "__author__"]
except ImportError:
    __all__ = ["__version__", "__author__"]


# ---- Memory Provider bridge ----
# The Hermes memory provider discovery system (plugins/memory/__init__.py)
# calls load_memory_provider("mnemosyne"), which looks for
# register_memory_provider or MemoryProvider in this top-level __init__.py.
# The actual MnemosyneMemoryProvider lives in hermes_memory_provider/__init__.py.
# This bridge function delegates to it so the memory provider system can
# discover and load the provider.
def register(ctx):
    """Bridge to hermes_memory_provider for memory provider discovery."""
    try:
        from .hermes_memory_provider import register_memory_provider
    except ImportError:
        return  # Bridge module not available — tools/hooks still load via hermes_plugin
    register_memory_provider(ctx)
