import re
from os.path import basename

from adapters.factory import get_object_store
from db.models import Document
from db.session import get_session

_FILENAME_RE = re.compile(
    r"^(?P<doc_type>.+)_(?P<entity_id>[^_]+)_(?P<date>\d{8})\.(?P<ext>\w+)$"
)

_CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tiff": "image/tiff",
    "json": "application/json",
}


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
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")

    store = get_object_store()
    store.put(object_key, data, content_type=content_type)

    match = _FILENAME_RE.match(filename)
    doc_type: str | None = match.group("doc_type") if match else None

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
