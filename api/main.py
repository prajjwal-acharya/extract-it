from fastapi import FastAPI
from api.routes.ingest import router as ingest_router
from api.routes.query import router as query_router
from api.routes.review import router as review_router
from observability.langsmith_setup import setup_langsmith

setup_langsmith()

app = FastAPI(title="Doc Intel Platform", version="0.1.0")
app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
app.include_router(query_router, prefix="/query", tags=["query"])
app.include_router(review_router, prefix="/review", tags=["review"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
