from os.path import basename

from adapters.factory import get_object_store
from db.models import Document
from db.session import get_session
from shared.utils.filename import parse_doc_type_from_filename
from shared.utils.mime import mime_from_filename


def ingest_file(file_path: str) -> str:
    """Store a local file in the object store and create a Document DB row.

    Returns the new document_id (UUID string).  The file is uploaded under
    the key raw/<filename>, and doc_type is parsed from the filename pattern
    if it matches <doc_type>_<entity_id>_<YYYYMMDD>.<ext>.
    """
    filename = basename(file_path)

    with open(file_path, "rb") as fh:
        data = fh.read()

    object_key = f"raw/{filename}"
    content_type = mime_from_filename(filename)

    store = get_object_store()
    store.put(object_key, data, content_type=content_type)

    doc_type = parse_doc_type_from_filename(filename)

    session = get_session()
    try:
        doc = Document(
            filename=filename,
            doc_type=doc_type,
            object_key=object_key,
            status="queued",
        )
        session.add(doc)
        session.commit()
        return doc.id
    finally:
        session.close()
