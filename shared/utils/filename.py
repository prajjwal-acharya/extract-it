import re

_FILENAME_RE = re.compile(r"^(?P<doc_type>.+)_(?P<entity_id>[^_]+)_(?P<date>\d{8})\.(?P<ext>\w+)$")


def parse_doc_type_from_filename(filename: str) -> str | None:
    """Return the doc_type group from a filename, or None if it doesn't match."""
    match = _FILENAME_RE.match(filename)
    return match.group("doc_type") if match else None
