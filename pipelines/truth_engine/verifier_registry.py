from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

MAX_TOOL_CALLS = 3


@dataclass
class VerifierSpec:
    """One deterministic verifier that can be run against extracted fields."""

    name: str
    fn: Callable[..., dict]  # fn(**kwargs) -> {"valid": bool, ...}
    description: str = ""


class VerifierRegistry:
    """Maps document types to their deterministic verifiers.

    Adding a verifier for a new doc type requires only a single register() call —
    no changes to graph nodes or routing.
    """

    def __init__(self) -> None:
        self._registry: dict[str, list[VerifierSpec]] = {}

    def register(self, doc_type: str, *specs: VerifierSpec) -> "VerifierRegistry":
        self._registry.setdefault(doc_type, []).extend(specs)
        return self

    def get(self, doc_type: str) -> list[VerifierSpec]:
        return list(self._registry.get(doc_type, []))

    def has_verifiers(self, doc_type: str) -> bool:
        return bool(self._registry.get(doc_type))

    def all_doc_types(self) -> list[str]:
        return list(self._registry.keys())


from agents.verifiers import balance_arithmetic, mrz_checksum

verifier_registry = VerifierRegistry()

verifier_registry.register(
    "passport",
    VerifierSpec(
        name="mrz_checksum",
        fn=mrz_checksum,
        description="Verify MRZ field check digits per ICAO 9303",
    ),
)
verifier_registry.register(
    "bank_statement",
    VerifierSpec(
        name="balance_arithmetic",
        fn=balance_arithmetic,
        description="Verify opening + sum(transactions) ≈ closing balance (±0.01)",
    ),
)
# Placeholders — verifiers will be registered here as they are implemented:
# verifier_registry.register("gst_invoice",   VerifierSpec("gstin_checksum", ...))
# verifier_registry.register("salary_slip",   VerifierSpec(...))
# verifier_registry.register("property_deed", VerifierSpec(...))
