"""Private financial dataset registry and SEC fundamentals contract.

This package reads the four private financial R2 stores through the read-only
``zion-datasets`` service, resolves published release pointers and manifests, and
validates a typed SEC annual fundamentals contract. It never writes, copies,
publishes or re-points a dataset, and nothing here is wired to the public
valuation site.
"""

from zion_terminal.datasets.registry import (
    FAMILY_INDEX,
    SOURCE_FAMILIES,
    Discovery,
    FinancialDatasetRegistry,
    ObjectRecord,
    ReleaseRef,
    SourceFamily,
)
from zion_terminal.datasets.report import (
    RegistryReport,
    render_markdown,
    render_sanitized_markdown,
    sanitize_for_markdown,
    sanitize_summary,
    write_report,
)
from zion_terminal.datasets.sec_fundamentals import (
    CONTRACT_FIELDS,
    CONTRACT_SCHEMA,
    DERIVED_FIELD,
    DISCLOSED_STATES,
    CompanyContractResult,
    FieldSpec,
    SourceObservation,
    SourceRecord,
    annual_periods,
    cross_validate,
    evaluate_company,
    evaluate_field,
    parse_companyfacts,
)
from zion_terminal.datasets.transport import (
    DEFAULT_BRIDGE_URL,
    Bridge,
    FixtureBridge,
    HttpDatasetBridge,
    RangedObjectReader,
    TransportError,
    open_zip_member,
    validate_bridge_url,
)

__all__ = [
    "Bridge",
    "CONTRACT_FIELDS",
    "CONTRACT_SCHEMA",
    "CompanyContractResult",
    "DEFAULT_BRIDGE_URL",
    "DERIVED_FIELD",
    "DISCLOSED_STATES",
    "Discovery",
    "FAMILY_INDEX",
    "FieldSpec",
    "FinancialDatasetRegistry",
    "FixtureBridge",
    "HttpDatasetBridge",
    "ObjectRecord",
    "RangedObjectReader",
    "RegistryReport",
    "ReleaseRef",
    "SOURCE_FAMILIES",
    "SourceFamily",
    "SourceObservation",
    "SourceRecord",
    "TransportError",
    "annual_periods",
    "cross_validate",
    "evaluate_company",
    "evaluate_field",
    "open_zip_member",
    "parse_companyfacts",
    "render_markdown",
    "render_sanitized_markdown",
    "sanitize_for_markdown",
    "sanitize_summary",
    "validate_bridge_url",
    "write_report",
]
