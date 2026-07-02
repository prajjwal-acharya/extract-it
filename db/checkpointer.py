from langgraph.checkpoint.postgres import PostgresSaver
from config.settings import settings


def get_checkpointer() -> PostgresSaver:
    return PostgresSaver.from_conn_string(settings.DATABASE_URL)
