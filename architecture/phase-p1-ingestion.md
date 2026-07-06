# P1 — Ingestion

**Status:** ✅ Done  
**Scope:** Document ingestion pipeline, object store, Document model, dedup, API entry point

---

## What P1 delivered

P1 is the front door of the platform. It accepts raw file bytes (PDF, JPEG, PNG),
stores them durably, creates a `Document` row in Postgres, and hands the pipeline
a `document_id` to work with. Nothing is extracted yet — P1 is purely about reliable
intake and deduplication.

---

## Ingestion flow

```
HTTP multipart upload
        │
        ▼
api/routes/ingest.py
  1. os.path.basename(filename)     — CWE-22 path-traversal guard
  2. enforce 25 MB size limit       — HTTP 413 on breach
  3. mime detection from extension  — shared/utils/mime.py

        │
        ▼
io_pipeline/orchestrator.py  IngestionOrchestrator.ingest()
  1. SHA-256 hash of raw bytes                   — io_pipeline/hashing.py
  2. SELECT id FROM documents WHERE hash = ?
     → duplicate: return existing document_id   (no re-pipeline)
     → new file: continue

  3. object_store.put("raw/<filename>", bytes)   — MinIO or GCS
  4. parse_doc_type_from_filename(filename)       — shared/utils/filename.py
     regex: <type>_<entity>_<YYYYMMDD>.<ext>
     e.g. "passport_john_20240115.pdf" → doc_type="passport"
     non-matching: doc_type=None (classify_node corrects this later)

  5. INSERT INTO documents (filename, doc_type, object_key, hash,
                             file_size, mime_type, status="queued")
  6. Return document_id

        │
        ▼
api/routes/ingest.py  (continued)
  7. background_tasks.add_task(_run_pipeline, document_id, ...)
  8. Return {"document_id": "<uuid>"} — pipeline runs asynchronously
```

The caller gets the `document_id` immediately. Pipeline progress can be polled via
`GET /documents/{id}` watching `current_phase`.

---

## Deduplication

`io_pipeline/hashing.py` computes SHA-256 of the raw file bytes. The hash is stored
in `Document.hash`. On every new ingest, `IngestionOrchestrator` queries for an
existing document with the same hash. If found, it returns the existing `document_id`
without re-uploading or re-running the pipeline.

This prevents the same file from being processed twice (e.g., after a clear-data
reset, the old hash disappears so a fresh upload goes through).

---

## Object store abstraction

`adapters/factory.py` returns the appropriate `ObjectStore` implementation:

```
ENV=LOCAL  →  MinioStore   (adapters/object_store/minio_store.py)
ENV=GCP    →  GCSStore     (adapters/object_store/gcs_store.py)
```

Both implement `ObjectStore` (adapters/object_store/base.py):

```python
class ObjectStore(ABC):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
```

Raw files are stored at `raw/<filename>`. Output JSONs are written at `output/<document_id>.json`
by the persist node (Phase P11).

---

## Document model

`db/models.py` — `Document` table:

| Column | Type | Set when |
|---|---|---|
| `id` | UUID PK | ingest |
| `filename` | string | ingest |
| `doc_type` | string (nullable) | ingest (from filename) or write_output |
| `object_key` | string | ingest |
| `hash` | string(64) | ingest |
| `file_size` | int | ingest |
| `mime_type` | string | ingest |
| `status` | string | ingest (`queued`); updated by write_output |
| `current_phase` | string | each node via `_stamp_phase()` |
| `universal_schema` | JSON | write_output |
| `extracted_fields` | JSON | write_output |
| `created_at` | timestamp | ingest |
| `updated_at` | timestamp | every update |

---

## Folder watcher trigger

In addition to the HTTP API, `api/main.py` starts a `LocalWatchTrigger`
(`adapters/trigger/local_watch.py`) on startup. It watches `WATCH_DIR`
(`/tmp/extract_it_watch` by default) using inotify/polling:

```python
_watcher.on_new_object(_read_and_ingest)
_watcher.start()
```

When a file appears in the watch directory, `_read_and_ingest` calls
`IngestionOrchestrator.ingest()` with `source="folder_watch"`. This enables
local automation without an HTTP client.

In GCP mode, `PubSubTrigger` replaces `LocalWatchTrigger` — same interface,
different transport.

---

## Alembic migration

`da5070439f01_create_core_tables.py` creates the `documents` and `confidence_logs`
tables.

`ab12cd34ef56_add_identity_columns_to_documents.py` adds `hash`, `file_size`,
`mime_type` to `documents` (identity fields for dedup and content inspection).

---

## API endpoint

`POST /ingest/` accepts `multipart/form-data` with a `file` field. Returns:

```json
{"document_id": "uuid-here"}
```

The document is immediately visible via `GET /documents/{id}` with
`status=queued` and `current_phase=pending` while the pipeline picks it up.
