from __future__ import annotations

from os.path import basename

from io_pipeline.orchestrator import IngestionOrchestrator


def ingest_file(file_path: str) -> str:
    """Read a local file and run the full ingestion sequence.

    Returns document_id.  Thin shim over IngestionOrchestrator so that
    the folder-watcher trigger and legacy callers need no changes.
    """
    with open(file_path, "rb") as fh:
        data = fh.read()
    filename = basename(file_path)
    orchestrator = IngestionOrchestrator()  # no dispatch_fn → pipeline not triggered
    doc_id, _ = orchestrator.ingest(data, filename)
    return doc_id
