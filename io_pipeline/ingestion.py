def ingest_file(file_path: str) -> str:
    """Store a local file in the object store and create a Document DB row.

    Returns the new document_id (UUID string).  The file is uploaded under
    the key raw/<filename>, and doc_type is parsed from the filename pattern
    if it matches <doc_type>_<entity_id>_<YYYYMMDD>.<ext>.
    """
    raise NotImplementedError
