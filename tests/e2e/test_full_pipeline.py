import pytest


@pytest.mark.live
def test_full_pipeline_passport() -> None:
    """Ingest a passport fixture, run the full graph, verify status='complete' and doc_type='passport'."""
    raise NotImplementedError


@pytest.mark.live
def test_full_pipeline_bank_statement() -> None:
    """Ingest a bank statement fixture, run the full graph, verify status='complete'."""
    raise NotImplementedError


@pytest.mark.live
def test_full_pipeline_unknown_document_triggers_hitl() -> None:
    """Ingest an ambiguous document that cannot be classified confidently and verify HITL is raised."""
    raise NotImplementedError


@pytest.mark.live
def test_full_pipeline_trace_appears_in_langsmith() -> None:
    """After a pipeline run, a trace with the expected run name is present in LangSmith."""
    raise NotImplementedError
