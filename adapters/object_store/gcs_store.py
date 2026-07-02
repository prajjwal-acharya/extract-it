class GCSStore:
    """ObjectStore implementation backed by Google Cloud Storage (used when ENV=GCP)."""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Upload *data* to *key* in the configured GCS bucket."""
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
