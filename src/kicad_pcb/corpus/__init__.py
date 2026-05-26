"""Model-corpus ingestion and evaluation helpers."""

from .embedded_symbols import extract_embedded_symbol_defs, write_embedded_symbol_library
from .ingestion import (
    CorpusFixtureSummary,
    IngestionReportEntry,
    IngestionSummary,
    ingest_model_corpus,
    list_model_corpus,
)
from .kicadxml import (
    KicadXmlComponent,
    KicadXmlNet,
    KicadXmlNetlist,
    KicadXmlNetPin,
    canonicalize_circuit_ir,
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
)
from .layout_features import (
    LayoutFeatures,
    RelativePositionFeature,
    SymbolLayoutFeature,
    extract_layout_features,
    guess_symbol_role,
    write_layout_features,
)
from .metadata import (
    CorpusFixtureMetadata,
    CorpusFixtureStatus,
    detect_fixture_id_collisions,
    make_fixture_id,
    merge_preserved_metadata,
    write_fixture_metadata,
)

__all__ = [
    "CorpusFixtureMetadata",
    "CorpusFixtureStatus",
    "CorpusFixtureSummary",
    "IngestionReportEntry",
    "IngestionSummary",
    "KicadXmlComponent",
    "KicadXmlNet",
    "KicadXmlNetPin",
    "KicadXmlNetlist",
    "LayoutFeatures",
    "RelativePositionFeature",
    "SymbolLayoutFeature",
    "canonicalize_circuit_ir",
    "detect_fixture_id_collisions",
    "extract_embedded_symbol_defs",
    "extract_layout_features",
    "guess_symbol_role",
    "ingest_model_corpus",
    "kicadxml_to_circuit_ir",
    "list_model_corpus",
    "make_fixture_id",
    "merge_preserved_metadata",
    "parse_kicadxml_netlist",
    "write_embedded_symbol_library",
    "write_fixture_metadata",
    "write_layout_features",
]
