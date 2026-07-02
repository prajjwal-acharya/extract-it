import re
from pathlib import Path
from adapters.factory import get_object_store
from db.session import get_session
from db.models import Document

_FILENAME_RE = re.compile(
    r"(?P<doc_type>[a-z_]+)_(?P<entity_id>[A-Z0-9]+)_(?P<date>\d{8})\.\w+",
    re.IGNORECASE,
)


def ingest_file(file_path: str) -> str:
    path = Path(file_path)
    store = get_object_store()
    data = path.read_bytes()
    object_key = f"raw/{path.name}"
    store.put(object_key, data)

    session = get_session()
    try:
        doc = Document(filename=path.name, object_key=object_key, status="queued")
        match = _FILENAME_RE.match(path.name)
        if match:
            doc.doc_type = match.group("doc_type").lower()
        session.add(doc)
        session.commit()
        return doc.id
    finally:
        session.close()
