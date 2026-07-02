def get_checkpointer():
    """Return a configured PostgresSaver checkpointer for LangGraph.

    Uses langgraph-checkpoint-postgres to persist graph state in the same
    Postgres instance as the application tables.
    """
    raise NotImplementedError
