"""Part 4 smoke test: chain nodes manually to prove state contract end-to-end.

Usage:
    # 4a — mocked Gemini (zero API cost):
    python scripts/manual_pipeline_smoke.py --document-id <id> --mock

    # 4b — live Gemini (requires GOOGLE_API_KEY in .env):
    python scripts/manual_pipeline_smoke.py --document-id <id>

Run from the project root.
"""

import argparse
import json
import unittest.mock as mock

from config.settings import settings
from db.models import Document
from db.session import SessionLocal
from pipelines.nodes.classify import classify_node
from pipelines.nodes.extract import extract_node
from pipelines.nodes.master import master_node
from pipelines.nodes.validate import validate_node
from pipelines.router import route_after_validate
from pipelines.state import GraphState

print(f"ENV={settings.ENV}  MODEL={settings.GEMINI_MODEL}")


def build_initial_state(doc_id: str) -> GraphState:
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == doc_id).one()
    finally:
        db.close()

    return {  # type: ignore[typeddict-item]
        "document_id": doc.id,
        "filename": doc.filename,
        "object_key": doc.object_key,
        "doc_type": doc.doc_type,
        "raw_bytes": b"",
        "classify_confidence": 0.0,
        "extracted_fields": {},
        "extract_confidence": 0.0,
        "validation_issues": [],
        "validate_confidence": 0.0,
        "universal_schema": {},
        "retry_count": 0,
        "hitl_required": False,
        "hitl_approved": None,
        "error": None,
        "status": doc.status,
    }


def print_diff(step: str, update: dict) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {step}")
    print(f"{'─' * 60}")
    for k, v in update.items():
        val = v if not isinstance(v, bytes) else f"<bytes len={len(v)}>"
        print(f"  {k}: {json.dumps(val, default=str)}")


def run(doc_id: str, use_mock: bool) -> None:
    state = build_initial_state(doc_id)
    print(f"\nInitial state: document_id={state['document_id']}  filename={state['filename']}")

    classify_resp_json = json.dumps(
        {
            "doc_type": "passport",
            "confidence": 0.94,
        }
    )
    extract_resp_json = json.dumps(
        {
            "surname": "SMOKE",
            "given_names": "TEST",
            "nationality": "TST",
            "date_of_birth": "2000-01-01",
            "sex": "M",
            "place_of_birth": None,
            "date_of_issue": "2020-01-01",
            "date_of_expiry": "2030-01-01",
            "passport_number": "S0000001",
            "mrz_line1": None,
            "mrz_line2": None,
            "confidence": 0.87,
        }
    )

    def _mock_response(text: str):
        r = mock.MagicMock()
        r.text = text
        return r

    ctx = None
    if use_mock:
        mock_client = mock.MagicMock()
        mock_client.models.generate_content.side_effect = [
            _mock_response(classify_resp_json),
            _mock_response(extract_resp_json),
        ]
        ctx = mock.patch("agents.llm_client._client", return_value=mock_client)
        ctx.__enter__ = lambda s: mock_client  # type: ignore[method-assign]
        ctx.__exit__ = lambda s, *a: None  # type: ignore[method-assign]

    with (
        mock.patch(
            "agents.llm_client._client",
            return_value=mock.MagicMock(
                models=mock.MagicMock(
                    generate_content=mock.MagicMock(
                        side_effect=[
                            _mock_response(classify_resp_json),
                            _mock_response(extract_resp_json),
                        ]
                    )
                )
            ),
        )
        if use_mock
        else mock.MagicMock()
    ) as _:
        # Step 2 — master_node
        update = master_node(state)
        print_diff("master_node", {k: v for k, v in update.items() if k != "raw_bytes"})
        state = {**state, **update}  # type: ignore[misc]

        # Step 3 — classify_node
        update = classify_node(state)
        print_diff("classify_node", update)
        state = {**state, **update}  # type: ignore[misc]

        # Step 4 — extract_node
        update = extract_node(state)
        print_diff("extract_node", update)
        state = {**state, **update}  # type: ignore[misc]

    # Step 5 — validate_node (no LLM call)
    update = validate_node(state)
    print_diff("validate_node", update)
    state = {**state, **update}  # type: ignore[misc]

    # Step 6 — router
    route = route_after_validate(state)
    print(f"\n{'─' * 60}")
    print(f"  route_after_validate → {route!r}")
    print(f"{'─' * 60}")

    # Step 7 — assertions
    print("\nAssertions:")
    assert state["doc_type"] is not None and isinstance(state["doc_type"], str), (
        "doc_type must be a non-None string after classify_node"
    )
    print("  ✓ doc_type is non-None string")

    assert isinstance(state["extracted_fields"], dict) and len(state["extracted_fields"]) > 0, (
        "extracted_fields must be a non-empty dict after extract_node"
    )
    print("  ✓ extracted_fields is non-empty dict")

    vc = state["validate_confidence"]
    assert isinstance(vc, float) and 0.0 <= vc <= 1.0, (
        f"validate_confidence must be float in [0,1], got {vc!r}"
    )
    print(f"  ✓ validate_confidence={vc:.3f} in [0.0, 1.0]")

    print(f"\n{'═' * 60}")
    print(f"  SMOKE PASS  {'(MOCKED)' if use_mock else '(LIVE)'}")
    print(f"  classify_confidence : {state['classify_confidence']:.3f}")
    print(f"  extract_confidence  : {state['extract_confidence']:.3f}")
    print(f"  validate_confidence : {state['validate_confidence']:.3f}")
    print(f"  routing decision    : {route}")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--mock", action="store_true", help="Patch Gemini calls (no API cost)")
    args = parser.parse_args()
    run(args.document_id, args.mock)
