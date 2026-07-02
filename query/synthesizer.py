def synthesize(question: str, chunks: list[dict]) -> str:
    """Generate a grounded answer to *question* from retrieved document *chunks*.

    Builds a prompt that includes chunk text with document_id citations and
    calls the Gemini model to produce a concise, source-attributed answer.
    """
    raise NotImplementedError
