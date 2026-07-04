CONTENT_TYPES: dict[str, str] = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tiff": "image/tiff",
    "json": "application/json",
}


def mime_from_filename(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return CONTENT_TYPES.get(ext, "application/octet-stream")


# Ordered by specificity (longer magic bytes first)
_MAGIC_MAP: list[tuple[bytes, str]] = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF", "application/pdf"),
]


def detect_mime_from_bytes(data: bytes) -> str | None:
    """Return MIME type by inspecting leading bytes, or None if unrecognised."""
    for magic, mime in _MAGIC_MAP:
        if data.startswith(magic):
            return mime
    return None
