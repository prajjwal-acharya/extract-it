from typing import Annotated
from pydantic import BaseModel
import operator


class DocumentState(BaseModel):
    document_id: str = ""
    filename: str = ""
    object_key: str = ""
    raw_content: str = ""

    doc_type: str | None = None
    classify_confidence: float = 0.0

    extracted_fields: dict = {}
    extract_confidence: float = 0.0

    validation_issues: list[str] = []
    validate_confidence: float = 0.0

    universal_schema: dict = {}

    retry_count: int = 0
    hitl_required: bool = False
    hitl_approved: bool | None = None

    error: str | None = None
    status: str = "pending"
