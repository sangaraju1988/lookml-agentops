"""Catalog interface and models."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from lookml_agentops.config import LkagentConfig


class GlossaryTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    definition: str
    synonyms: list[str] = Field(default_factory=list)
    owner: str = ""
    sensitivity: Literal["public", "internal", "confidential", "restricted"] = "internal"
    linked_fields: list[str] = Field(default_factory=list)  # project.view.field
    status: Literal["draft", "approved", "deprecated"] = "draft"

    @property
    def approved(self) -> bool:
        return self.status == "approved"


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = ""  # file path or URI, for reporting
    terms: list[GlossaryTerm] = Field(default_factory=list)
    # term id -> 1-based line in the source file (best effort, for lint locations)
    lines: dict[str, int] = Field(default_factory=dict, exclude=True)

    def by_id(self) -> dict[str, GlossaryTerm]:
        return {t.id: t for t in self.terms}

    def terms_linking(self, project: str, view: str, field: str) -> list[GlossaryTerm]:
        key = f"{project}.{view}.{field}"
        return [t for t in self.terms if key in t.linked_fields]


class CatalogAdapter(abc.ABC):
    """Reads business terms from a knowledge catalog."""

    @abc.abstractmethod
    def load(self) -> Catalog: ...


def load_catalog(cfg: LkagentConfig) -> Catalog:
    if cfg.catalog.adapter == "yaml":
        from lookml_agentops.catalog.yaml_adapter import YamlCatalogAdapter

        return YamlCatalogAdapter(cfg.path(cfg.catalog.path)).load()
    from lookml_agentops.catalog.dataplex_adapter import DataplexCatalogAdapter

    return DataplexCatalogAdapter().load()
