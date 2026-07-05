"""Integration seam P6↔P7: normalize output is persisted by the output writer."""

import json
import unittest.mock as mock
import uuid

from db.models import Document
from io_pipeline.output_writer import write_output
from pipelines.nodes.normalize import normalize_node
from pipelines.resolution.models import ResolutionDecision, Strategy
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
)


def _truth_report() -> TruthReport:
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=0.99, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=[],
            additional_fields=[],
            coverage_score=1.0,
        ),
        verification_reports=[],
        final_confidence=0.99,
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="completed",
            allow_completion=True,
            allow_embedding=True,
            allow_learning=True,
            reason="test",
        ),
    )


def _passport_state(doc_id: str) -> dict:
    return {
        "document_id": doc_id,
        "doc_type": "passport",
        "extracted_fields": {
            "surname": "AL-FARSI",
            "given_names": "AHMAD",
            "passport_number": "Z43R34255",
            "date_of_expiry": "2030-02-10",
        },
        "classify_confidence": 1.0,
        "extract_confidence": 0.99,
        "validate_confidence": 1.0,
        "validation_issues": [],
        "error": None,
        "hitl_required": False,
        "hitl_approved": None,
        "truth_report": _truth_report(),
        "resolution_decision": ResolutionDecision(
            strategy=Strategy.ACCEPT,
            reason="test",
            requires_human=False,
            learning_candidate=True,
        ),
        "execution_history": [],
        "hitl_correction": False,
        "schema_version": None,
        "schema_proposal": None,
    }


def test_normalize_produces_universal_schema_with_required_keys() -> None:
    """universal_schema from normalize_node contains holder_name, id_number, expiry_date."""
    state = _passport_state("irrelevant-id")
    result = normalize_node(state)  # type: ignore[arg-type]
    schema = result["universal_schema"]
    assert "holder_name" in schema
    assert "id_number" in schema
    assert "expiry_date" in schema
    assert schema["holder_name"] == "AHMAD AL-FARSI"
    assert schema["id_number"] == "Z43R34255"
    assert schema["expiry_date"] == "2030-02-10"


def test_write_output_persists_universal_schema_to_postgres(minio_client, postgres_session) -> None:
    """write_output() updates the Document row's universal_schema column in Postgres."""
    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        filename="passport_P002_20240101.pdf",
        object_key="raw/passport_P002_20240101.pdf",
        status="queued",
    )
    postgres_session.add(doc)
    postgres_session.commit()

    state = _passport_state(doc_id)
    state.update(normalize_node(state))  # type: ignore[arg-type]

    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
        mock.patch("agents.llm_client._client") as mock_client_fn,
    ):
        mock_client_fn.return_value.models.embed_content.return_value = mock.MagicMock(
            embeddings=[mock.MagicMock(values=[0.0] * 768)]
        )
        write_output(state)  # type: ignore[arg-type]

    postgres_session.expire_all()
    updated = postgres_session.get(Document, doc_id)
    assert updated.status == "completed"
    assert updated.universal_schema["holder_name"] == "AHMAD AL-FARSI"
    assert updated.universal_schema["id_number"] == "Z43R34255"


def test_write_output_uploads_json_to_object_store(minio_client, postgres_session) -> None:
    """write_output() writes output/<doc_id>.json to the object store after normalize."""
    doc_id = str(uuid.uuid4())
    doc = Document(
        id=doc_id,
        filename="passport_P003_20240101.pdf",
        object_key="raw/passport_P003_20240101.pdf",
        status="queued",
    )
    postgres_session.add(doc)
    postgres_session.commit()

    state = _passport_state(doc_id)
    state.update(normalize_node(state))  # type: ignore[arg-type]

    with (
        mock.patch("io_pipeline.output_writer.get_session", return_value=postgres_session),
        mock.patch("io_pipeline.output_writer.get_object_store", return_value=minio_client),
        mock.patch("agents.llm_client._client") as mock_client_fn,
    ):
        mock_client_fn.return_value.models.embed_content.return_value = mock.MagicMock(
            embeddings=[mock.MagicMock(values=[0.0] * 768)]
        )
        write_output(state)  # type: ignore[arg-type]

    stored = json.loads(minio_client.get(f"output/{doc_id}.json"))
    assert stored == state["universal_schema"]
