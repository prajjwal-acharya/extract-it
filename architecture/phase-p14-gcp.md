# P14 — GCP Deployment

**Status:** 🔲 Planned  
**Scope:** Cloud Run, GCS, Cloud SQL (pgvector), Pub/Sub, EventArc trigger, deploy scripts

---

## What P14 will deliver

P14 deploys the entire platform to Google Cloud Platform. The adapter pattern
(`adapters/factory.py`) was designed from P0 specifically to make this a
configuration change rather than a code change. Setting `ENV=GCP` in the
environment swaps MinIO → GCS and LocalWatchTrigger → PubSubTrigger.

---

## GCP service mapping

| Local (ENV=LOCAL) | GCP (ENV=GCP) | Purpose |
|---|---|---|
| MinIO | Cloud Storage (GCS) | Object store for raw files and output JSON |
| LocalWatchTrigger | Pub/Sub + EventArc | File-drop trigger for pipeline |
| PostgreSQL (Docker) | Cloud SQL for PostgreSQL (with pgvector extension) | Primary database |
| App container (Docker) | Cloud Run (serverless container) | API + pipeline runner |
| Frontend container (Docker) | Cloud Run (separate service) | Streamlit dashboard |
| LangSmith | LangSmith (same) | Tracing (optional) |

---

## Infrastructure files

Already present in the repo:

```
infra/gcp/
├── cloudrun.yaml          Cloud Run service definition (API)
├── deploy.sh              Build + push image, deploy to Cloud Run
└── eventarc-trigger.yaml  EventArc trigger: GCS object create → Pub/Sub → Cloud Run
```

`docker-compose.gcp-sim.yml` simulates GCP adapters locally using:
- `fake-gcs-server` for GCS emulation
- `gcpug/pubsub-emulator` for Pub/Sub emulation

```bash
make gcp-sim   # start GCP emulators
```

---

## Adapter swap — zero application code changes

`adapters/factory.py`:

```python
def get_object_store() -> ObjectStore:
    if settings.ENV == "GCP":
        return GCSStore(bucket=settings.GCS_BUCKET)
    return MinioStore(
        endpoint=settings.MINIO_ENDPOINT,
        access_key=settings.MINIO_ACCESS_KEY,
        secret_key=settings.MINIO_SECRET_KEY,
        bucket=settings.MINIO_BUCKET,
    )

def get_trigger() -> Trigger:
    if settings.ENV == "GCP":
        return PubSubTrigger(subscription=settings.PUBSUB_SUBSCRIPTION)
    return LocalWatchTrigger(watch_dir=WATCH_DIR)
```

Both `GCSStore` and `MinioStore` implement the same `ObjectStore.put/get/delete`
interface. No application code changes between environments.

---

## Cloud Run considerations

- **Concurrency**: Cloud Run instances can handle multiple concurrent requests.
  LangGraph pipeline tasks run as background threads — each request that ingests a
  document spawns a pipeline thread. Cloud Run's CPU-always-on mode is recommended
  to prevent cold-start latency from delaying pipeline execution.

- **Minimum instances**: Set to 1 to keep the folder watcher / Pub/Sub listener
  alive. Cold starts would miss Pub/Sub messages delivered before the instance is ready.

- **Memory**: Gemini multimodal calls with large PDFs can require 2–4 GB. Set Cloud
  Run memory to at least 4 GB.

- **Timeout**: Default Cloud Run request timeout is 60 s. Pipeline runs can take
  longer. The pipeline runs as a FastAPI `BackgroundTask` — the HTTP response
  returns immediately after ingest, so the 60 s timeout only applies to the ingest
  endpoint, not the pipeline execution.

---

## Cloud SQL (pgvector)

Cloud SQL for PostgreSQL supports the `pgvector` extension. Steps:
1. Create Cloud SQL instance with PostgreSQL 15+
2. Enable pgvector: `CREATE EXTENSION vector;`
3. Run Alembic migrations against Cloud SQL
4. Run `make checkpointer` to create LangGraph checkpoint tables

Connection from Cloud Run uses Cloud SQL Auth Proxy (auto-managed by Cloud Run's
Cloud SQL connection feature — add `--add-cloudsql-instances` to the Cloud Run
service definition).

---

## GCS + EventArc trigger

`infra/gcp/eventarc-trigger.yaml` defines an EventArc trigger that fires on
`google.cloud.storage.object.v1.finalized` events in the GCS bucket:

```yaml
name: doc-intel-gcs-trigger
destination:
  cloudRun:
    service: doc-intel-api
    region: us-central1
    path: /ingest-event/
transport:
  pubsub:
    topic: doc-intel-gcs-events
```

When a file is uploaded to the GCS bucket, EventArc publishes a Pub/Sub message,
which delivers to the `/ingest-event/` Cloud Run endpoint. The endpoint calls
`IngestionOrchestrator.ingest()` with the GCS object path.

---

## Environment variables for GCP

```bash
ENV=GCP
DATABASE_URL=postgresql+psycopg://user:password@/docint?host=/cloudsql/project:region:instance
GCS_BUCKET=doc-intel-documents
PUBSUB_SUBSCRIPTION=doc-intel-gcs-events-sub
GCP_PROJECT_ID=my-gcp-project
GCP_REGION=us-central1
GOOGLE_API_KEY=<gemini-api-key>
```

`GOOGLE_APPLICATION_CREDENTIALS` is automatically provided by Cloud Run's service
account — no manual key file needed when running on GCP.

---

## Deploy script

`infra/gcp/deploy.sh`:

```bash
#!/bin/bash
# Build and push Docker image to Artifact Registry
gcloud builds submit --tag gcr.io/$PROJECT_ID/doc-intel:latest .

# Deploy to Cloud Run
gcloud run deploy doc-intel-api \
  --image gcr.io/$PROJECT_ID/doc-intel:latest \
  --region $REGION \
  --set-env-vars ENV=GCP,...
  --add-cloudsql-instances $PROJECT_ID:$REGION:$INSTANCE_NAME \
  --memory 4Gi \
  --min-instances 1
```

---

## CI gate

E2E tests in `.github/workflows/ci.yml` are currently gated:

```yaml
e2e-tests:
  if: false   # Enable after GCP deployment
```

After P14 is complete, the `if: false` gate will be removed and E2E tests will
run against the deployed Cloud Run service with a test GCS bucket and a test
Cloud SQL instance.
