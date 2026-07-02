import json
from adapters.factory import get_object_store
from db.session import get_session
from db.models import Document, ConfidenceLog
from pipelines.state import DocumentState


def write_output(state: DocumentState) -> None:
    session = get_session()
    try:
        doc = session.get(Document, state.document_id)
        if doc:
            doc.status = state.status
            doc.extracted_fields = state.extracted_fields
            doc.universal_schema = state.universal_schema

            log = ConfidenceLog(
                document_id=state.document_id,
                agent="validate",
                score=state.validate_confidence,
                reason="; ".join(state.validation_issues) if state.validation_issues else None,
            )
            session.add(log)
            session.commit()

    finally:
        session.close()

    output = json.dumps(state.universal_schema, indent=2, default=str).encode()
    store = get_object_store()
    store.put(f"output/{state.document_id}.json", output, content_type="application/json")
