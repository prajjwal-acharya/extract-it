import hashlib


def compute_sha256(data: bytes) -> str:
    """Return lowercase hex SHA-256 digest of data."""
    return hashlib.sha256(data).hexdigest()
