from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from sqlalchemy.exc import IntegrityError

from adapters.factory import get_object_store
from adapters.object_store.base import ObjectStore
from db.models import Document
from db.session import get_session
from io_pipeline.hashing import compute_sha256
from io_pipeline.validation import ValidatedFile, ValidationService
from shared.utils.filename import sanitize_filename

logger = logging.getLogger(__name__)

# Type alias for the background dispatch callable.
# Receives (document_id, safe_filename, object_key).
DispatchFn = Callable[[str, str, str], None]


class _DuplicateHashError(Exception):
    def __init__(self, existing_id: str) -> None:
        self.existing_id = existing_id


class IngestionOrchestrator:
    """Coordinates the full ingestion sequence for a single document.

    All collaborators are injected so the orchestrator is fully unit-testable
    without real I/O.  Production code constructs it with no arguments and
    relies on the factory defaults.
    """

    def __init__(
        self,
        *,
        validator: ValidationService | None = None,
        store: ObjectStore | None = None,
        dispatch_fn: DispatchFn | None = None,
    ) -> None:
        self._validator = validator or ValidationService()
        self._store = store or get_object_store()
        self._dispatch = dispatch_fn  # None = no-op / tests

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(
        self, data: bytes, original_filename: str, *, source: str = "unknown"
    ) -> tuple[str, bool]:
        """Run the full ingestion sequence.

        Returns (document_id, is_duplicate).
        Raises ValidationError if the file is invalid.
        """
        t0 = time.monotonic()
        logger.info(
            "event=UploadReceived filename=%r size=%d source=%s",
            original_filename,
            len(data),
            source,
        )

        # 1. Validate — raises ValidationError on failure
        logger.info("event=ValidationStarted")
        try:
            validated: ValidatedFile = self._validator.validate(data, original_filename)
        except Exception as exc:
            elapsed = time.monotonic() - t0
            logger.info(
                "event=IngestFailed reason=%s source=%s elapsed=%.3fs",
                exc,
                source,
                elapsed,
            )
            raise
        logger.info(
            "event=ValidationSucceeded mime=%s ext=%s source=%s",
            validated.mime_type,
            validated.extension,
            source,
        )

        # 2. Identity
        hash_hex: str = compute_sha256(data)

        # 3. Idempotency — return existing document_id if already ingested
        existing_id = self._find_by_hash(hash_hex)
        if existing_id is not None:
            elapsed = time.monotonic() - t0
            logger.info(
                "event=DuplicateDetected hash=%s existing_id=%s source=%s elapsed=%.3fs",
                hash_hex,
                existing_id,
                source,
                elapsed,
            )
            return existing_id, True

        # 4. Secure naming
        safe_name: str = sanitize_filename(original_filename)
        document_id: str = str(uuid.uuid4())
        object_key: str = f"raw/{document_id}.{validated.extension}"

        # 5. Persist to object store
        self._store.put(object_key, validated.data, content_type=validated.mime_type)
        logger.info(
            "event=ObjectStored object_key=%s hash=%s source=%s",
            object_key,
            hash_hex,
            source,
        )

        # 6. Bootstrap DB row — roll back orphaned object on failure
        try:
            self._bootstrap(
                document_id=document_id,
                safe_name=safe_name,
                object_key=object_key,
                validated=validated,
                hash_hex=hash_hex,
            )
        except _DuplicateHashError as dup:
            self._safe_delete(object_key, reason="concurrent_duplicate")
            elapsed = time.monotonic() - t0
            logger.info(
                "event=DuplicateDetected hash=%s existing_id=%s source=%s elapsed=%.3fs",
                hash_hex,
                dup.existing_id,
                source,
                elapsed,
            )
            return dup.existing_id, True
        except Exception:
            self._safe_delete(object_key, reason="db_failure")
            elapsed = time.monotonic() - t0
            logger.exception(
                "event=IngestFailed object_key=%s hash=%s source=%s elapsed=%.3fs",
                object_key,
                hash_hex,
                source,
                elapsed,
            )
            raise

        elapsed = time.monotonic() - t0
        logger.info(
            "event=DocumentCreated document_id=%s object_key=%s hash=%s source=%s elapsed=%.3fs",
            document_id,
            object_key,
            hash_hex,
            source,
            elapsed,
        )

        # 7. Dispatch pipeline (optional — skipped in tests when dispatch_fn is None)
        if self._dispatch is not None:
            logger.info("event=DispatchStarted document_id=%s source=%s", document_id, source)
            self._dispatch(document_id, safe_name, object_key)

        return document_id, False

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _safe_delete(self, object_key: str, *, reason: str) -> None:
        """Delete the object and log but never propagate errors."""
        try:
            self._store.delete(object_key)
            logger.info("event=OrphanCleaned object_key=%s reason=%s", object_key, reason)
        except Exception:
            logger.exception(
                "event=OrphanCleanupFailed object_key=%s reason=%s", object_key, reason
            )

    def _find_by_hash(self, hash_hex: str) -> str | None:
        """Return document_id of an existing document with this hash, or None."""
        session = get_session()
        try:
            doc = session.query(Document).filter(Document.hash == hash_hex).first()
            return doc.id if doc is not None else None
        finally:
            session.close()

    def _bootstrap(
        self,
        *,
        document_id: str,
        safe_name: str,
        object_key: str,
        validated: ValidatedFile,
        hash_hex: str,
    ) -> None:
        """Create the Document row and stamp current_phase = ingested."""
        session = get_session()
        try:
            doc = Document(
                id=document_id,
                filename=safe_name,
                object_key=object_key,
                hash=hash_hex,
                file_size=validated.file_size,
                mime_type=validated.mime_type,
                status="queued",
                current_phase="ingested",
            )
            session.add(doc)
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.query(Document).filter(Document.hash == hash_hex).first()
            if existing is None:
                raise  # re-raise if winner not found (shouldn't happen)
            raise _DuplicateHashError(existing.id)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
