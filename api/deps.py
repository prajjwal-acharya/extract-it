from typing import Generator

from sqlalchemy.orm import Session

from db.session import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
