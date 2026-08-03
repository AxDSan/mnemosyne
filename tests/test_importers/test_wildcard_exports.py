"""Regression tests for the importers package wildcard exports.

Ensures that ``PROVIDERS`` (documented in ``docs/api-reference.md`` as a
public export) is included in ``__all__`` so that::

    from mnemosyne.core.importers import *

continues to expose it, preventing the package-API regression noted in
the PR review for #483.
"""

from mnemosyne.core import importers


class TestImportersWildcardExports:
    """Verify the public surface of ``mnemosyne.core.importers``."""

    def test_providers_in_all(self):
        """PROVIDERS must be in __all__ so wildcard imports expose it."""
        assert "PROVIDERS" in importers.__all__

    def test_providers_accessible_via_wildcard(self):
        """Wildcard import must surface the PROVIDERS registry."""
        namespace: dict = {}
        exec("from mnemosyne.core.importers import *", namespace)
        assert "PROVIDERS" in namespace
        assert isinstance(namespace["PROVIDERS"], dict)
        assert "mem0" in namespace["PROVIDERS"]

    def test_all_names_resolve(self):
        """Every name listed in __all__ must actually be importable."""
        for name in importers.__all__:
            assert hasattr(importers, name), f"{name!r} listed in __all__ but not defined"

    def test_helper_exports_in_all(self):
        """Key helper functions documented in the public API must be exported."""
        for expected in ("import_from_provider", "list_providers"):
            assert expected in importers.__all__
