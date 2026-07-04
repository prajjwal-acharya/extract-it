"""Part 5 smoke test: prove checkpointer + interrupt/resume work via a single-node graph.

Usage (run from project root inside the app container):
    PYTHONPATH=/app python scripts/manual_hitl_smoke.py --document-id <id>
"""

import argparse
import psycopg

from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.types import Command
from langgraph.checkpoint.postgres import PostgresSaver

from config.settings import settings
from db.checkpointer import get_checkpointer
from pipelines.nodes.op_b_hitl import op_b_hitl_node
from pipelines.state import GraphState


def build_graph(checkpointer: PostgresSaver):
    builder = StateGraph(GraphState)
    builder.add_node("hitl", op_b_hitl_node)
    builder.set_entry_point("hitl")
    builder.add_edge("hitl", END)
    return builder.compile(checkpointer=checkpointer)


def run(doc_id: str) -> None:
    print("Building checkpointer…")
    checkpointer = get_checkpointer()
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": doc_id}}

    initial_state: GraphState = {  # type: ignore[typeddict-item]
        "document_id": doc_id,
        "filename": "passport_TEST001_20240101.pdf",
        "object_key": "raw/passport_TEST001_20240101.pdf",
        "raw_bytes": b"%PDF-1.4 smoke",
        "doc_type": "passport",
        "classify_confidence": 0.94,
        "extracted_fields": {
            "surname": "SMOKE",
            "given_names": "TEST",
            "nationality": "TST",
            "passport_number": "S0000001",
        },
        "extract_confidence": 0.87,
        "validation_issues": ["date_of_birth: field required"],
        "validate_confidence": 0.60,
        "universal_schema": {},
        "retry_count": 2,
        "hitl_required": False,
        "hitl_approved": None,
        "error": None,
        "status": "queued",
    }

    # ── Step 4: first invoke — should interrupt ───────────────────────
    print("\nStep 4: invoking graph (expect interrupt)…")
    result = graph.invoke(initial_state, config=config)
    print(f"  invoke returned keys: {list(result.keys())}")

    interrupted = "__interrupt__" in result
    print(f"  __interrupt__ in result: {interrupted}")

    snap = graph.get_state(config)
    print(f"  graph.get_state tasks: {snap.tasks}")

    assert interrupted or snap.tasks, (
        "Expected graph to be interrupted — no __interrupt__ and no pending tasks"
    )
    print("  ✓ Graph is paused at interrupt")

    # ── Step 5: resume ────────────────────────────────────────────────
    correction_field = "date_of_birth"
    correction_value = "1990-05-15"
    print(
        f"\nStep 5: resuming with approved=True, corrections={{'{correction_field}': '{correction_value}'}}…"
    )
    final = graph.invoke(
        Command(resume={"approved": True, "corrections": {correction_field: correction_value}}),
        config=config,
    )

    # ── Step 6: verify final state ────────────────────────────────────
    print(f"\nStep 6: final state keys: {list(final.keys())}")
    assert final.get("hitl_approved") is True, (
        f"Expected hitl_approved=True, got {final.get('hitl_approved')!r}"
    )
    print("  ✓ hitl_approved=True")

    ef = final.get("extracted_fields", {})
    assert ef.get(correction_field) == correction_value, (
        f"Expected {correction_field}={correction_value!r} in extracted_fields, got {ef!r}"
    )
    print(f"  ✓ extracted_fields[{correction_field!r}]={ef[correction_field]!r}")

    # ── Step 7: confirm checkpoint row persisted ──────────────────────────
    print("\nStep 7: querying checkpoint table…")
    raw_url = settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(raw_url) as conn:
        row = conn.execute(
            "SELECT thread_id, checkpoint_id FROM checkpoints WHERE thread_id = %s",
            (doc_id,),
        ).fetchone()
    assert row is not None, f"No checkpoint row found for thread_id={doc_id!r}"
    print(f"  ✓ Checkpoint row: thread_id={row[0]!r}  checkpoint_id={row[1]!r}")

    print("\n" + "═" * 60)
    print("  HITL SMOKE PASS")
    print(f"  document_id      : {doc_id}")
    print(f"  hitl_approved    : {final.get('hitl_approved')}")
    print(f"  correction merged: {correction_field}={ef.get(correction_field)!r}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    args = parser.parse_args()
    run(args.document_id)
