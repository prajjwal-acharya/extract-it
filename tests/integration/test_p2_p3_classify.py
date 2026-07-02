"""Integration seam P2↔P3: classification output feeds the extraction node."""
import typing
import unittest.mock as mock

from pipelines.nodes.classify import classify_node
from pipelines.state import GraphState


def test_classify_output_is_valid_graph_state_update(minio_client, sample_pdf_bytes) -> None:
    minio_client.put("raw/passport_P001_20240101.pdf", sample_pdf_bytes, "application/pdf")

    state: GraphState = {  # type: ignore[typeddict-item]
        "filename": "passport_P001_20240101.pdf",
        "object_key": "raw/passport_P001_20240101.pdf",
        "doc_type": None,
    }

    mock_response = mock.MagicMock()
    mock_response.text = '{"doc_type": "passport", "confidence": 0.97}'

    valid_keys = set(typing.get_type_hints(GraphState).keys())

    with (
        mock.patch("pipelines.nodes.classify.get_object_store", return_value=minio_client),
        mock.patch("agents.llm_client._client") as mock_client_fn,
    ):
        mock_client_fn.return_value.models.generate_content.return_value = mock_response
        update = classify_node(state)

    assert set(update.keys()).issubset(valid_keys)
    assert update.get("doc_type") == "passport"
    assert 0.0 <= update.get("classify_confidence", -1) <= 1.0


def test_doc_type_from_classify_is_used_by_extract_schema_lookup() -> None:
    raise NotImplementedError


def test_parallel_classify_and_extract_merge_without_conflict() -> None:
    raise NotImplementedError
