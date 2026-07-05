from __future__ import annotations

from config.schema_loader import load_reference_fields

_ENVELOPE_INSTRUCTION = """\
Return a JSON object with this exact structure:
{"fields": { <all extracted key-value pairs> }, "overall_confidence": <float 0.0-1.0>}

overall_confidence reflects your overall confidence in the extraction quality.
Return only valid JSON with no additional explanation."""


def build_extraction_prompt(doc_type: str, context: str | None = None) -> str:
    """Build a schema-guided open extraction prompt for doc_type.

    The reference schema provides field guidance only — it never restricts
    what Gemini is allowed to extract.  All discovered fields must be returned.
    """
    required_fields, optional_fields = load_reference_fields(doc_type)

    lines: list[str] = ["Extract all meaningful information from this document.", ""]

    if required_fields:
        lines.append(
            f"Business-critical fields — extract these if present: {', '.join(required_fields)}"
        )
    if optional_fields:
        lines.append(
            f"Additional known fields — extract these if present: {', '.join(optional_fields)}"
        )
    if required_fields or optional_fields:
        lines += [
            "",
            "Also extract every other meaningful field you discover in the document.",
            "Never ignore a field simply because it is absent from the lists above.",
        ]
    else:
        lines += [
            "Extract every field you can identify.",
            "There is no predefined field list — capture everything meaningful.",
        ]

    lines += ["", _ENVELOPE_INSTRUCTION]

    prompt = "\n".join(lines)
    if context:
        prompt = f"{context}\n\n{prompt}"
    return prompt
