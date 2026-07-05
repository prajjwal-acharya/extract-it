from agents.extract_agent import extract
from agents.llm_client import embed
from db.models import RetrievalLog
from db.session import get_session
from db.vector_store import similarity_search
from pipelines.state import GraphState
from shared.utils.mime import mime_from_filename


def extract_node(state: GraphState) -> dict:
    """Run open extraction with RAG context; return GraphState update.

    Deterministic verification (tool_call_count, verification_passed) is no
    longer performed here — Phase 4 owns that responsibility.
    """
    doc_type = state.get("doc_type") or ""
    mime_type = mime_from_filename(state["filename"])
    document_id = state["document_id"]

    session = get_session()
    try:
        similar = similarity_search(
            session,
            embed(doc_type or "document", task_type="RETRIEVAL_QUERY"),
            top_k=3,
            doc_type=doc_type or None,
        )
        context = "\n".join(f"Example: {row.chunk_text}" for row, _ in similar) or None
        retrieval_metadata = {
            "retrieved_count": len(similar),
            "doc_ids": [row.document_id for row, _ in similar if row.document_id != document_id],
        }

        for row, distance in similar:
            if row.document_id == document_id:
                continue
            session.add(
                RetrievalLog(
                    document_id=document_id,
                    retrieved_document_id=row.document_id,
                    stage="first_pass",
                    similarity_score=1 - distance,
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    result = extract(
        state["raw_bytes"],
        mime_type,
        doc_type,
        context=context,
        retrieval_metadata=retrieval_metadata,
    )

    update: dict = {
        "extracted_fields": result.fields,
        "extract_confidence": result.overall_confidence,
        "extraction_result": result,
        "tool_call_count": 0,
    }
    if not result.success:
        update["error"] = result.error
    return update
