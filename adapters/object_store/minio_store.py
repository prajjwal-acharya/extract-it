class MinioStore:
    """ObjectStore implementation backed by MinIO (used when ENV=LOCAL)."""

    def __init__(self) -> None:
        """Initialise Minio client and ensure the configured bucket exists."""
        raise NotImplementedError

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload *data* to *key* in the bucket."""
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        """Download and return the object at *key*."""
        raise NotImplementedError

    def list(self, prefix: str = "") -> list[str]:
        """Return all object keys that start with *prefix*."""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        """Delete the object at *key*."""
        raise NotImplementedError
