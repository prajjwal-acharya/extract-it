from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RoutingAction(str, Enum):
    """Meaningful execution paths the pipeline can take after classification."""

    PROCEED = "PROCEED"
    UNKNOWN = "UNKNOWN"
    FAILURE = "FAILURE"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2


@dataclass(frozen=True)
class ConfidencePolicy:
    """Per-document-type confidence policy.

    The policy owns threshold logic; the RoutingEngine orchestrates.
    evaluate() is the single decision point — no threshold comparisons outside this class.
    """

    proceed_threshold: float = 0.70

    def evaluate(self, confidence: float) -> RoutingAction:
        """Return PROCEED if confidence meets the threshold, UNKNOWN otherwise."""
        if confidence >= self.proceed_threshold:
            return RoutingAction.PROCEED
        return RoutingAction.UNKNOWN


@dataclass(frozen=True)
class RegistryEntry:
    document_type: str
    reference_schema_name: str
    extraction_prompt_key: str
    verifier_profile: tuple[str, ...]
    retry_policy: RetryPolicy
    confidence_policy: ConfidencePolicy
    rag_namespace: str


_DEFAULT_RETRY = RetryPolicy(max_retries=2)
_DEFAULT_CONFIDENCE = ConfidencePolicy(proceed_threshold=0.70)


class DocumentRegistry:
    """Single source of truth for all supported document types.

    Replaces scattered if/elif chains and hardcoded lists throughout the project.
    """

    def __init__(self, entries: list[RegistryEntry]) -> None:
        seen: set[str] = set()
        for e in entries:
            if e.document_type in seen:
                raise ValueError(f"Duplicate registry key: {e.document_type!r}")
            seen.add(e.document_type)
        self._index: dict[str, RegistryEntry] = {e.document_type: e for e in entries}

    def get(self, doc_type: str) -> RegistryEntry:
        try:
            return self._index[doc_type]
        except KeyError:
            raise KeyError(f"Unknown document type: {doc_type!r}") from None

    def exists(self, doc_type: str) -> bool:
        return doc_type in self._index

    def all(self) -> list[RegistryEntry]:
        return list(self._index.values())

    def reference_schema_name(self, doc_type: str) -> str:
        """Return reference_schema_name for doc_type, falling back to doc_type itself if not registered."""
        return self._index[doc_type].reference_schema_name if doc_type in self._index else doc_type


registry = DocumentRegistry(
    [
        RegistryEntry(
            document_type="passport",
            reference_schema_name="passport",
            extraction_prompt_key="passport",
            verifier_profile=("mrz_checksum",),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="passport",
        ),
        RegistryEntry(
            document_type="bank_statement",
            reference_schema_name="bank_statement",
            extraction_prompt_key="bank_statement",
            verifier_profile=("balance_arithmetic",),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="bank_statement",
        ),
        RegistryEntry(
            document_type="salary_slip",
            reference_schema_name="salary_slip",
            extraction_prompt_key="salary_slip",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="salary_slip",
        ),
        RegistryEntry(
            document_type="itr",
            reference_schema_name="itr",
            extraction_prompt_key="itr",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="itr",
        ),
        RegistryEntry(
            document_type="gst_invoice",
            reference_schema_name="gst_invoice",
            extraction_prompt_key="gst_invoice",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="gst_invoice",
        ),
        RegistryEntry(
            document_type="property_deed",
            reference_schema_name="property_deed",
            extraction_prompt_key="property_deed",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="property_deed",
        ),
        RegistryEntry(
            document_type="driving_license",
            reference_schema_name="driving_license",
            extraction_prompt_key="driving_license",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="driving_license",
        ),
        RegistryEntry(
            document_type="aadhaar",
            reference_schema_name="aadhaar",
            extraction_prompt_key="aadhaar",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=_DEFAULT_CONFIDENCE,
            rag_namespace="aadhaar",
        ),
        RegistryEntry(
            document_type="UNKNOWN",
            reference_schema_name="unknown",
            extraction_prompt_key="unknown",
            verifier_profile=(),
            retry_policy=_DEFAULT_RETRY,
            confidence_policy=ConfidencePolicy(
                proceed_threshold=0.0
            ),  # UNKNOWN always evaluates to UNKNOWN
            rag_namespace="unknown",
        ),
    ]
)
