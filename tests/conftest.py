import pytest


@pytest.fixture
def postgres_session():
    """Yield a SQLAlchemy session connected to a test Postgres instance (testcontainers)."""
    raise NotImplementedError


@pytest.fixture
def minio_client():
    """Yield a configured MinioStore pointed at a test MinIO bucket."""
    raise NotImplementedError


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Return the raw bytes of a minimal fixture PDF."""
    raise NotImplementedError


@pytest.fixture
def passport_state() -> dict:
    """Return a pre-populated GraphState dict for a passport document."""
    raise NotImplementedError


@pytest.fixture
def bank_statement_state() -> dict:
    """Return a pre-populated GraphState dict for a bank statement document."""
    raise NotImplementedError
