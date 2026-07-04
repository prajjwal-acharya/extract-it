from __future__ import annotations
from typing import Annotated
from typing_extensions import TypedDict
import operator

from pipelines.routing_engine import ClassificationContext


def _keep_last(current: object, update: object) -> object:
    """Last-write-wins reducer.

    Used on fields that op_a_retry overwrites each pass so LangGraph
    replaces the previous value rather than raising an update-conflict error.
    """
    return update if update is not None else current


class GraphState(TypedDict):
    # ── Document metadata (set once by io_pipeline before graph entry) ─────
    document_id: str
    filename: str
    object_key: str
    raw_bytes: bytes

    # ── Classify node outputs ───────────────────────────────────────────────
    doc_type: str | None
    classify_confidence: float
    # Full routing audit envelope; graph routing depends only on this.
    classification_context: ClassificationContext | None
    # Routing policy version that produced the RoutingPlan (audit trail).
    routing_version: str | None

    # ── Extract node outputs ────────────────────────────────────────────────
    # extracted_fields is overwritten by op_a_retry on each retry pass, so
    # it needs a reducer to avoid LangGraph update-conflict errors.
    extracted_fields: Annotated[dict, _keep_last]
    extract_confidence: float

    # ── Validate node outputs ───────────────────────────────────────────────
    # validation_issues is accumulated across retry passes; operator.add
    # appends each pass's issues list rather than overwriting prior ones.
    validation_issues: Annotated[list[str], operator.add]
    validate_confidence: float

    # ── Normalize / universal schema output ────────────────────────────────
    universal_schema: dict

    # ── Control-flow fields ─────────────────────────────────────────────────
    retry_count: int
    hitl_required: bool
    hitl_approved: bool | None

    # ── Verifier tool-call budget tracking (accumulated across nodes) ───────
    tool_call_count: Annotated[int, operator.add]

    # ── Deterministic verifier outcome (None = not attempted) ───────────────
    verification_passed: bool | None

    # ── Active schema version used for this extraction (audit trail) ────────
    schema_version: str | None

    # ── Status and error tracking ───────────────────────────────────────────
    error: str | None
    status: str
