"""Integration seam P1↔P2: ingestion hands off to the pipeline trigger."""


def test_ingest_file_enqueues_pipeline_run() -> None:
    """A file dropped via ingest_file() causes the LangGraph pipeline to be invoked."""
    raise NotImplementedError


def test_ingest_file_object_is_readable_by_pipeline() -> None:
    """The object stored during ingestion is retrievable by the pipeline's raw_content step."""
    raise NotImplementedError


def test_ingest_creates_document_with_queued_status() -> None:
    """After ingestion the Document row has status='queued' before the pipeline runs."""
    raise NotImplementedError
