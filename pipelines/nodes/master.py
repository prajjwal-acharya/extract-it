import re
from pipelines.state import DocumentState

_FILENAME_PATTERN = re.compile(
    r"(?P<doc_type>[a-z_]+)_(?P<entity_id>[A-Z0-9]+)_(?P<date>\d{8})\.\w+",
    re.IGNORECASE,
)


def master_node(state: DocumentState) -> dict:
    match = _FILENAME_PATTERN.match(state.filename)
    if match:
        return {"doc_type": match.group("doc_type").lower()}
    return {}
