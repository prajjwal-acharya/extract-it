from enum import Enum
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(str, Enum):
    LOCAL = "LOCAL"
    GCP = "GCP"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ENV: Env = Env.LOCAL

    DATABASE_URL: str = "postgresql+psycopg://user:password@localhost:5432/docint"

    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "documents"

    GCP_PROJECT_ID: str = ""
    GCP_REGION: str = "us-central1"
    GCS_BUCKET: str = ""
    PUBSUB_TOPIC: str = ""

    GEMINI_MODEL: str = "gemini-2.0-flash"  # confirmed current model
    GEMINI_EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = 768
    GOOGLE_API_KEY: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    LANGCHAIN_TRACING_V2: bool = True
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = "doc-intel-platform"

    REVIEW_API_KEY: str = ""

    CONFIDENCE_THRESHOLD: float = 0.85
    MAX_RETRIES: int = 2


settings = Settings()
