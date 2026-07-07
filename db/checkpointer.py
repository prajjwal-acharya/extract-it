import threading

from langgraph.checkpoint.postgres import PostgresSaver

from config.settings import settings

_checkpointer: PostgresSaver | None = None
_checkpointer_cm = None  # held at module scope — psycopg closes conn on GC if cm is collected
_lock = threading.Lock()


def get_checkpointer() -> PostgresSaver:
    """Return a process-wide PostgresSaver, created and .setup() on first call.

    Thread-safe via double-checked locking — multiple background pipeline
    threads may call this simultaneously on first startup.
    """
    global _checkpointer, _checkpointer_cm
    if _checkpointer is not None:
        return _checkpointer
    with _lock:
        if _checkpointer is None:
            # psycopg's raw connection parser rejects SQLAlchemy's "+psycopg" dialect
            # suffix — strip it before handing the DSN to PostgresSaver.
            raw_url = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
            _checkpointer_cm = PostgresSaver.from_conn_string(raw_url)
            tmp = _checkpointer_cm.__enter__()
            tmp.setup()
            _checkpointer = tmp  # only visible to other threads after setup() completes
    return _checkpointer
