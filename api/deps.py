from sqlalchemy.orm import Session


def get_db() -> Session:
    """FastAPI dependency that yields a SQLAlchemy session and closes it after the request."""
    raise NotImplementedError
