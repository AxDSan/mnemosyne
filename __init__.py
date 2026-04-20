"""
Mnemosyne Plugin for Hermes Agent
Entry point at repo root for `hermes plugins install` compatibility.
"""

# Delegate to the hermes_plugin package; fail gracefully when Hermes framework
# is not present (e.g. pip-only installs via mnemosyne-memory)
try:
    from hermes_plugin import register
    __all__ = ["register"]
except ImportError:
    __all__ = []
