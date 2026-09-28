"""Dataplex / knowledge catalog adapter (stub).

TODO(verify-api): Read the current Dataplex business glossary / Knowledge Catalog docs before
implementing. We haven't confirmed the resource names, the term/field-link shape, or the export
format, so this adapter deliberately does nothing. See docs/api-verification.md.
"""

from __future__ import annotations

from lookml_agentops.catalog.base import Catalog, CatalogAdapter


class DataplexCatalogAdapter(CatalogAdapter):
    def load(self) -> Catalog:
        raise NotImplementedError(
            "Dataplex catalog adapter is not implemented yet (TODO(verify-api)); "
            "use `catalog.adapter: yaml` or export your glossary to YAML."
        )
