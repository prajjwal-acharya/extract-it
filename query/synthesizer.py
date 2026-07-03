from agents.llm_client import generate


def synthesize(question: str, chunks: list[dict]) -> str:
    """Generate a grounded answer to *question* from retrieved document *chunks*."""
    if not chunks:
        return "No relevant documents found to answer this question."

    context = "\n\n".join(f"[Document {c['document_id']}]: {c['chunk_text']}" for c in chunks)
    prompt = (
        f"Answer the question using only the context below. Cite document IDs "
        f'in brackets like [Document <id>] for any claim.\n\nContext:\n{context}\n\n'
        f"Question: {question}"
    )
    return generate(prompt)
