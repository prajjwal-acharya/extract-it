import tempfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, BackgroundTasks
from io_pipeline.ingestion import ingest_file
from pipelines.graph import graph
from pipelines.state import DocumentState
from io_pipeline.output_writer import write_output
from adapters.factory import get_object_store

router = APIRouter()


def _run_pipeline(document_id: str, object_key: str, filename: str) -> None:
    store = get_object_store()
    raw_content = store.get(object_key).decode(errors="replace")
    initial_state = DocumentState(
        document_id=document_id,
        filename=filename,
        object_key=object_key,
        raw_content=raw_content,
    )
    final_state = graph.invoke(initial_state)
    write_output(DocumentState(**final_state))


@router.post("/")
async def ingest(file: UploadFile, background_tasks: BackgroundTasks) -> dict:
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    document_id = ingest_file(tmp_path)
    background_tasks.add_task(_run_pipeline, document_id, f"raw/{file.filename}", file.filename)
    return {"document_id": document_id, "status": "queued"}
