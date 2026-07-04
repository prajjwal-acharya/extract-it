"""Regression tests for DocumentRegistry — P2A Classification Foundation."""

import unittest.mock as mock
from pathlib import Path

import pytest

from pipelines.registry import (
    ConfidencePolicy,
    DocumentRegistry,
    RegistryEntry,
    RetryPolicy,
    registry,
)

_SCHEMA_DIR = Path(__file__).parent.parent.parent / "config" / "schemas"


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------


def test_registry_lookup_known_type() -> None:
    entry = registry.get("passport")
    assert entry.document_type == "passport"
    assert entry.schema_name == "passport"
    assert entry.rag_namespace == "passport"


def test_registry_lookup_gst_invoice() -> None:
    entry = registry.get("gst_invoice")
    assert entry.document_type == "gst_invoice"
    assert entry.schema_name == "gst_invoice"


def test_registry_lookup_unknown_raises_key_error() -> None:
    with pytest.raises(KeyError, match="nonexistent_type"):
        registry.get("nonexistent_type")


# ---------------------------------------------------------------------------
# Registry exists
# ---------------------------------------------------------------------------


def test_registry_exists_true_for_all_supported_types() -> None:
    for doc_type in (
        "passport",
        "bank_statement",
        "salary_slip",
        "itr",
        "gst_invoice",
        "property_deed",
        "UNKNOWN",
    ):
        assert registry.exists(doc_type), f"expected {doc_type!r} in registry"


def test_registry_exists_false_for_old_gst_name() -> None:
    assert registry.exists("gst") is False


def test_registry_exists_false_for_arbitrary_string() -> None:
    assert registry.exists("totally_fake_doc") is False


# ---------------------------------------------------------------------------
# Registry all()
# ---------------------------------------------------------------------------


def test_registry_all_contains_every_type() -> None:
    types = {e.document_type for e in registry.all()}
    assert types == {
        "passport",
        "bank_statement",
        "salary_slip",
        "itr",
        "gst_invoice",
        "property_deed",
        "UNKNOWN",
    }


# ---------------------------------------------------------------------------
# UNKNOWN as first-class entry
# ---------------------------------------------------------------------------


def test_unknown_has_registry_entry() -> None:
    entry = registry.get("UNKNOWN")
    assert entry.document_type == "UNKNOWN"
    assert entry.schema_name == "unknown"
    assert isinstance(entry.retry_policy, RetryPolicy)
    assert isinstance(entry.confidence_policy, ConfidencePolicy)


def test_unknown_has_empty_verifier_profile() -> None:
    entry = registry.get("UNKNOWN")
    assert entry.verifier_profile == ()


# ---------------------------------------------------------------------------
# Verifier profiles
# ---------------------------------------------------------------------------


def test_passport_has_mrz_checksum_verifier() -> None:
    assert "mrz_checksum" in registry.get("passport").verifier_profile


def test_bank_statement_has_balance_arithmetic_verifier() -> None:
    assert "balance_arithmetic" in registry.get("bank_statement").verifier_profile


def test_non_verifiable_types_have_empty_profile() -> None:
    for doc_type in ("salary_slip", "itr", "gst_invoice", "property_deed", "UNKNOWN"):
        assert registry.get(doc_type).verifier_profile == (), (
            f"{doc_type} should have empty verifier_profile"
        )


# ---------------------------------------------------------------------------
# Duplicate key guard
# ---------------------------------------------------------------------------


def test_duplicate_keys_raise_value_error() -> None:
    entry = RegistryEntry(
        document_type="passport",
        schema_name="passport",
        extraction_prompt_key="passport",
        verifier_profile=(),
        retry_policy=RetryPolicy(),
        confidence_policy=ConfidencePolicy(),
        rag_namespace="passport",
    )
    with pytest.raises(ValueError, match="Duplicate registry key"):
        DocumentRegistry([entry, entry])


# ---------------------------------------------------------------------------
# Registry completeness — every schema YAML has a registry entry and vice versa
# ---------------------------------------------------------------------------


def test_registry_completeness() -> None:
    schema_names = {p.stem for p in _SCHEMA_DIR.glob("*.yaml")}
    registry_schema_names = {e.schema_name for e in registry.all()}
    missing_from_registry = schema_names - registry_schema_names
    missing_schema_file = registry_schema_names - schema_names
    assert not missing_from_registry, (
        f"Schema YAMLs with no registry entry: {missing_from_registry}"
    )
    assert not missing_schema_file, f"Registry entries with no schema YAML: {missing_schema_file}"


# ---------------------------------------------------------------------------
# schema_name helper
# ---------------------------------------------------------------------------


def test_schema_name_resolves_unknown() -> None:
    assert registry.schema_name("UNKNOWN") == "unknown"


def test_schema_name_falls_back_for_unregistered_type() -> None:
    assert registry.schema_name("not_in_registry") == "not_in_registry"


# ---------------------------------------------------------------------------
# classify_node registry validation
# ---------------------------------------------------------------------------


def test_classify_node_accepts_valid_type() -> None:
    from agents.base import AgentResult
    from pipelines.nodes.classify import classify_node
    from pipelines.state import GraphState

    state: GraphState = {"filename": "passport_P001_20240101.pdf", "raw_bytes": b"%PDF"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=True, confidence=0.95, data={"doc_type": "passport"})
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    assert result["doc_type"] == "passport"
    assert result["classify_confidence"] == 0.95


def test_classify_node_accepts_gst_invoice() -> None:
    from agents.base import AgentResult
    from pipelines.nodes.classify import classify_node
    from pipelines.state import GraphState

    state: GraphState = {"filename": "gst_invoice_G001_20240101.pdf", "raw_bytes": b"%PDF"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=True, confidence=0.9, data={"doc_type": "gst_invoice"})
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    assert result["doc_type"] == "gst_invoice"


def test_classify_node_rejects_old_gst_falls_back_to_unknown() -> None:
    from agents.base import AgentResult
    from pipelines.nodes.classify import classify_node
    from pipelines.state import GraphState

    state: GraphState = {"filename": "gst_G001_20240101.pdf", "raw_bytes": b"%PDF"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=True, confidence=0.85, data={"doc_type": "gst"})
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    assert result["doc_type"] == "UNKNOWN"


def test_classify_node_falls_back_to_unknown_for_unsupported_type() -> None:
    from agents.base import AgentResult
    from pipelines.nodes.classify import classify_node
    from pipelines.state import GraphState

    state: GraphState = {"filename": "random_R001_20240101.pdf", "raw_bytes": b"%PDF"}  # type: ignore[typeddict-item]
    fake = AgentResult(success=True, confidence=0.4, data={"doc_type": "birth_certificate"})
    with mock.patch("pipelines.nodes.classify.classify", return_value=fake):
        result = classify_node(state)
    assert result["doc_type"] == "UNKNOWN"
