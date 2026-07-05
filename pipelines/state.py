from __future__ import annotations
from typing import Annotated
from typing_extensions import TypedDict
import operator

from pipelines.routing_engine import ClassificationContext
from pipelines.truth_engine.models import ExtractionResult, TruthReport


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
    classification_context: ClassificationContext | None
    routing_version: str | None

    # ── Extract node outputs ────────────────────────────────────────────────
    extracted_fields: Annotated[dict, _keep_last]
    extract_confidence: float
    extraction_result: ExtractionResult | None

    # ── Truth Engine outputs (Phase 4) ──────────────────────────────────────
    truth_report: TruthReport | None

    # ── Validate node outputs (kept for op_b_hitl display compat) ──────────
    # validation_issues no longer populated by validate_node (removed in P4.2);
    # retained so op_b_hitl's interrupt payload compiles without change.
    validation_issues: Annotated[list[str], operator.add]

    # ── Normalize / universal schema output ────────────────────────────────
    universal_schema: dict

    # ── Control-flow fields ─────────────────────────────────────────────────
    retry_count: int
    hitl_required: bool
    hitl_approved: bool | None

    # ── Verifier tool-call budget tracking (accumulated across nodes) ───────
    tool_call_count: Annotated[int, operator.add]

    # ── Active schema version used for this extraction (audit trail) ────────
    schema_version: str | None

    # ── Status and error tracking ───────────────────────────────────────────
    error: str | None
    status: str
