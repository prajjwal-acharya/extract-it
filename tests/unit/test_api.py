def test_health_endpoint_returns_ok() -> None:
    """GET /health returns 200 with {status: ok}."""
    raise NotImplementedError


def test_ingest_endpoint_accepts_pdf_upload() -> None:
    """POST /ingest/ with a PDF file returns 200 and a document_id."""
    raise NotImplementedError


def test_ingest_endpoint_rejects_missing_file() -> None:
    """POST /ingest/ without a file returns 422 Unprocessable Entity."""
    raise NotImplementedError


def test_query_endpoint_returns_answer_and_sources() -> None:
    """POST /query/ with a question returns answer and sources list."""
    raise NotImplementedError


def test_query_endpoint_rejects_empty_question() -> None:
    """POST /query/ with an empty question string returns a validation error."""
    raise NotImplementedError


def test_get_db_dependency_yields_session() -> None:
    """get_db() yields a SQLAlchemy Session and closes it after the request."""
    raise NotImplementedError
