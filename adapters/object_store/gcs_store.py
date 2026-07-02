"""GCP Cloud Storage adapter — stub until P9."""


class GCSStore:
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        raise NotImplementedError("GCS adapter not active until P9")

    def get(self, key: str) -> bytes:
        raise NotImplementedError("GCS adapter not active until P9")

    def list(self, prefix: str = "") -> list[str]:
        raise NotImplementedError("GCS adapter not active until P9")

    def delete(self, key: str) -> None:
        raise NotImplementedError("GCS adapter not active until P9")
