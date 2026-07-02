"""Integration seam P6↔P7: normalize output is persisted by the output writer."""


def test_normalize_produces_universal_schema_with_required_keys() -> None:
    """universal_schema from normalize_node contains holder_name, id_number, expiry_date."""
    raise NotImplementedError


def test_write_output_persists_universal_schema_to_postgres() -> None:
    """write_output() updates the Document row's universal_schema column in Postgres."""
    raise NotImplementedError


def test_write_output_uploads_json_to_object_store() -> None:
    """write_output() writes output/<doc_id>.json to the object store after normalize."""
    raise NotImplementedError
