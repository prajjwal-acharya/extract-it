from langgraph.checkpoint.postgres import PostgresSaver

from config.settings import settings

_checkpointer: PostgresSaver | None = None


def get_checkpointer() -> PostgresSaver:
    """Return a process-wide PostgresSaver, created and .setup() on first call.

    Kept open for the app's lifetime — .from_conn_string() is a context
    manager; we enter it once and never exit (acceptable for demo scope,
    revisit with a FastAPI lifespan hook if this becomes long-running infra).
    """
    global _checkpointer
    if _checkpointer is None:
        cm = PostgresSaver.from_conn_string(settings.DATABASE_URL)
        _checkpointer = cm.__enter__()
        _checkpointer.setup()
    return _checkpointer
