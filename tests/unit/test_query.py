import unittest.mock as mock

from query.retriever import retrieve
from query.synthesizer import synthesize


def test_retrieve_returns_list_of_chunk_dicts(postgres_session) -> None:
    """retrieve() returns a list of dicts with document_id, chunk_text, chunk_index."""
    fake_row = mock.MagicMock()
    fake_row.document_id = "doc-abc"
    fake_row.chunk_text = '{"surname": "SMITH"}'
    fake_row.chunk_index = 0

    with mock.patch("query.retriever.get_session", return_value=postgres_session), \
         mock.patch("query.retriever.similarity_search", return_value=[fake_row]):
        results = retrieve([0.1] * 768)

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0]["document_id"] == "doc-abc"
    assert results[0]["chunk_text"] == '{"surname": "SMITH"}'
    assert results[0]["chunk_index"] == 0


def test_retrieve_respects_top_k_limit(postgres_session) -> None:
    """retrieve() passes top_k through to similarity_search."""
    fake_rows = [mock.MagicMock(document_id=f"doc-{i}", chunk_text="{}", chunk_index=0) for i in range(3)]

    with mock.patch("query.retriever.get_session", return_value=postgres_session), \
         mock.patch("query.retriever.similarity_search", return_value=fake_rows) as mock_search:
        results = retrieve([0.0] * 768, top_k=3)

    mock_search.assert_called_once_with(postgres_session, [0.0] * 768, top_k=3)
    assert len(results) == 3


def test_synthesize_returns_non_empty_string() -> None:
    """synthesize() returns a non-empty answer string for valid inputs."""
    chunks = [{"document_id": "doc-1", "chunk_text": '{"surname": "SMITH"}', "chunk_index": 0}]
    with mock.patch("query.synthesizer.generate", return_value="The holder is SMITH [Document doc-1]."):
        result = synthesize("Who is the passport holder?", chunks)
    assert isinstance(result, str)
    assert len(result) > 0


def test_synthesize_cites_document_ids() -> None:
    """synthesize() includes source document_id references in the answer."""
    chunks = [{"document_id": "doc-xyz", "chunk_text": '{"surname": "DOE"}', "chunk_index": 0}]
    with mock.patch("query.synthesizer.generate", return_value="The holder is DOE [Document doc-xyz].") as mock_gen:
        result = synthesize("Who is the holder?", chunks)

    assert "doc-xyz" in result
    # Verify the prompt contained the document_id so Gemini can cite it
    prompt_arg = mock_gen.call_args[0][0]
    assert "doc-xyz" in prompt_arg
