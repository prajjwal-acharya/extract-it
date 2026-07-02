import io
from minio import Minio
from config.settings import settings


class MinioStore:
    def __init__(self) -> None:
        self._client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=False,
        )
        self._bucket = settings.MINIO_BUCKET
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(self._bucket, key, io.BytesIO(data), len(data), content_type)

    def get(self, key: str) -> bytes:
        response = self._client.get_object(self._bucket, key)
        return response.read()

    def list(self, prefix: str = "") -> list[str]:
        objects = self._client.list_objects(self._bucket, prefix=prefix, recursive=True)
        return [obj.object_name for obj in objects]

    def delete(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)
