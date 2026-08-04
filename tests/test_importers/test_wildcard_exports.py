"""Regression tests for the importers package wildcard exports.

Ensures that ``PROVIDERS`` (documented in ``docs/api-reference.md`` as a
public export) is included in ``__all__`` so that::

    from mnemosyne.core.importers import *

continues to expose it, preventing the package-API regression noted in
the PR review for #483.
"""

from mnemosyne.core import importers

#: The complete documented public export surface for the importers package.
#: Adding or removing an import here without updating this set causes
#: test_all_names_resolve and test_wildcard_namespace_complete to fail.
EXPECTED_EXPORTS: frozenset[str] = frozenset({
    "AgenticImporter",
    "BaseImporter",
    "CogneeImporter",
    "HindsightImporter",
    "HolographicImporter",
    "HonchoImporter",
    "ImporterResult",
    "LettaImporter",
    "Mem0Importer",
    "PROVIDERS",
    "SuperMemoryImporter",
    "ZepImporter",
    "generate_agent_instructions",
    "generate_docs_instructions",
    "generate_migration_script",
    "generate_script",
    "get_provider_info",
    "import_from_file",
    "import_from_hindsight",
    "import_from_holographic",
    "import_from_mem0",
    "import_from_provider",
    "list_providers",
})


class TestImportersWildcardExports:
    """Verify the public surface of ``mnemosyne.core.importers``."""

    def test_all_names_resolve(self):
        """Every name listed in __all__ must actually be importable."""
        for name in importers.__all__:
            assert hasattr(importers, name), f"{name!r} listed in __all__ but not defined"

    def test_wildcard_namespace_complete(self):
        """The wildcard-import namespace must contain exactly the expected set."""
        namespace: dict = {}
        exec("from mnemosyne.core.importers import *", namespace)
        exported = {k for k in namespace if not k.startswith("_")}
        # Every expected export must be present
        missing = EXPECTED_EXPORTS - exported
        assert not missing, f"Wildcard import missing expected names: {missing}"

    def test_all_matches_expected_contract(self):
        """__all__ must match the complete documented public export set."""
        assert set(importers.__all__) == EXPECTED_EXPORTS, (
            "Public export contract drift: __all__ no longer matches the "
            "documented API surface. If you intentionally added or removed "
            "a public export, update EXPECTED_EXPORTS in this test."
        )

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
