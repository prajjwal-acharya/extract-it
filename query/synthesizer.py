from agents.llm_client import generate

_PROMPT = """Answer the user's question using only the provided document context.
Be concise and cite the source document ID.

Context:
{context}

Question: {question}"""


def synthesize(question: str, chunks: list[dict]) -> str:
    context = "\n\n".join(
        f"[doc:{c['document_id']}] {c['chunk_text']}" for c in chunks
    )
    return generate(_PROMPT.format(context=context, question=question))
