from langgraph.checkpoint.postgres import PostgresSaver

from config.settings import settings

_checkpointer: PostgresSaver | None = None
_checkpointer_cm = None  # held at module scope — psycopg closes conn on GC if cm is collected


def get_checkpointer() -> PostgresSaver:
    """Return a process-wide PostgresSaver, created and .setup() on first call.

    Kept open for the app's lifetime — .from_conn_string() is a context
    manager; we enter it once and never exit (acceptable for demo scope,
    revisit with a FastAPI lifespan hook if this becomes long-running infra).
    """
    global _checkpointer, _checkpointer_cm
    if _checkpointer is None:
        # psycopg's raw connection parser rejects SQLAlchemy's "+psycopg" dialect
        # suffix — strip it before handing the DSN to PostgresSaver.
        raw_url = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
        _checkpointer_cm = PostgresSaver.from_conn_string(raw_url)
        _checkpointer = _checkpointer_cm.__enter__()
        _checkpointer.setup()
    return _checkpointer
