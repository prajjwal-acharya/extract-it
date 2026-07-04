import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from testcontainers.minio import MinioContainer
from testcontainers.postgres import PostgresContainer

from db.models import Base


# ---------------------------------------------------------------------------
# postgres_session — testcontainers PostgresContainer with pgvector image,
# migrations applied, yields a SQLAlchemy Session.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def postgres_session():
    """Yield a SQLAlchemy session connected to a test Postgres instance (testcontainers)."""
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        url = pg.get_connection_url()
        # psycopg3 driver
        url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        session = Session()
        yield session
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# minio_client — testcontainers MinioContainer, yields a MinioStore pointed
# at a unique test bucket.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def minio_client():
    """Yield a configured MinioStore pointed at a test MinIO bucket."""
    with MinioContainer() as mc:
        from adapters.object_store.minio_store import MinioStore
        from config import settings as _settings_module

        host = mc.get_config()["endpoint"]
        bucket = f"test-{uuid.uuid4().hex[:8]}"

        # Temporarily patch settings so MinioStore.__init__ picks up the container
        orig_endpoint = _settings_module.settings.MINIO_ENDPOINT
        orig_key = _settings_module.settings.MINIO_ACCESS_KEY
        orig_secret = _settings_module.settings.MINIO_SECRET_KEY
        orig_bucket = _settings_module.settings.MINIO_BUCKET

        _settings_module.settings.MINIO_ENDPOINT = host
        _settings_module.settings.MINIO_ACCESS_KEY = mc.access_key
        _settings_module.settings.MINIO_SECRET_KEY = mc.secret_key
        _settings_module.settings.MINIO_BUCKET = bucket

        store = MinioStore()

        yield store

        _settings_module.settings.MINIO_ENDPOINT = orig_endpoint
        _settings_module.settings.MINIO_ACCESS_KEY = orig_key
        _settings_module.settings.MINIO_SECRET_KEY = orig_secret
        _settings_module.settings.MINIO_BUCKET = orig_bucket


# ---------------------------------------------------------------------------
# sample_pdf_bytes — minimal valid 1-page PDF, no external file needed.
# ---------------------------------------------------------------------------
@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Return the raw bytes of a minimal fixture PDF."""
    # Minimal PDF that renders as a blank page — no external file required.
    minimal_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\n"
        b"startxref\n190\n%%EOF"
    )
    return minimal_pdf


# ---------------------------------------------------------------------------
# Stubs — implemented in later phases
# ---------------------------------------------------------------------------


@pytest.fixture
def passport_state(minio_client, sample_pdf_bytes) -> dict:
    """Return a pre-populated GraphState dict for a passport document (post-master_node)."""
    minio_client.put("raw/passport_P001_20240101.pdf", sample_pdf_bytes, "application/pdf")
    return {
        "document_id": "test-doc-passport",
        "filename": "passport_P001_20240101.pdf",
        "object_key": "raw/passport_P001_20240101.pdf",
        "doc_type": "passport",
        "raw_bytes": sample_pdf_bytes,
    }


@pytest.fixture
def bank_statement_state(minio_client, sample_pdf_bytes) -> dict:
    """Return a pre-populated GraphState dict for a bank statement document (post-master_node)."""
    minio_client.put("raw/bank_statement_A001_20240101.pdf", sample_pdf_bytes, "application/pdf")
    return {
        "document_id": "test-doc-bank",
        "filename": "bank_statement_A001_20240101.pdf",
        "object_key": "raw/bank_statement_A001_20240101.pdf",
        "doc_type": "bank_statement",
        "raw_bytes": sample_pdf_bytes,
    }
