def test_ingest_file_stores_object_and_creates_db_row() -> None:
    """ingest_file() uploads to object store and inserts a Document with status='queued'."""
    raise NotImplementedError


def test_ingest_file_parses_doc_type_from_filename() -> None:
    """ingest_file() sets doc_type from the filename pattern when it matches."""
    raise NotImplementedError


def test_ingest_file_returns_document_id_string() -> None:
    """ingest_file() returns a non-empty UUID string."""
    raise NotImplementedError


def test_write_output_updates_document_row() -> None:
    """write_output() sets status and universal_schema on the Document row."""
    raise NotImplementedError


def test_write_output_appends_confidence_log() -> None:
    """write_output() inserts a ConfidenceLog row for the validate agent."""
    raise NotImplementedError


def test_write_output_writes_json_to_object_store() -> None:
    """write_output() uploads output/<doc_id>.json to the object store."""
    raise NotImplementedError
