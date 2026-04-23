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

# Graceful fallback when Hermes framework is not present
# (e.g. pip-only / standalone installs without hermes_plugin)
try:
    from hermes_plugin import register
    __all__ = ["register"]
except ImportError:
    __all__ = []
