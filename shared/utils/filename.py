import os
import re

_FILENAME_RE = re.compile(r"^(?P<doc_type>.+)_(?P<entity_id>[^_]+)_(?P<date>\d{8})\.(?P<ext>\w+)$")
_SAFE_RE = re.compile(r"[^\w.\-]")  # keep alphanumeric, dot, underscore, hyphen


def parse_doc_type_from_filename(filename: str) -> str | None:
    """Return the doc_type group from a filename, or None if it doesn't match."""
    match = _FILENAME_RE.match(filename)
    return match.group("doc_type") if match else None


def sanitize_filename(name: str) -> str:
    """Strip path components and reduce filename to filesystem-safe characters.

    Any character that is not alphanumeric, a dot, underscore, or hyphen is
    replaced with an underscore.  Leading dots and hyphens are stripped to
    prevent hidden-file tricks.  Falls back to 'upload' if the result is empty.
    """
    name = os.path.basename(name)  # CWE-22: strip all directory parts
    name = _SAFE_RE.sub("_", name)  # replace unsafe chars
    name = name.lstrip(".-")  # no leading dots/hyphens
    return name or "upload"
