# P7 — Query API

**Status:** ✅ Done  
**Scope:** Semantic Q&A, synthesizer, /query endpoint, /search endpoint

---

## What P7 delivered

P7 turns the document corpus into a queryable knowledge base. Users can ask natural
language questions ("What is John Smith's passport number?") and get answers grounded
in the extracted document data, with citations back to source documents.

---

## /query endpoint

`POST /query/` — semantic Q&A over the extracted document corpus.

Request:
```json
{"question": "What passport number belongs to John Smith?"}
```

Response:
```json
{
  "answer": "John Smith's passport number is A12345678 (document passport_john_20240115.pdf).",
  "sources": [
    {
      "document_id": "uuid...",
      "filename": "passport_john_20240115.pdf",
      "doc_type": "passport",
      "similarity_score": 0.92,
      "excerpt": "{\"surname\": \"SMITH\", \"given_names\": \"JOHN\", \"passport_number\": \"A12345678\"...}"
    }
  ]
}
```

---

## Query pipeline

```
POST /query/  {"question": "..."}
        │
        ▼
query/retriever.py  retrieve(question, doc_type=None, top_k=5)
  1. embed(question, task_type="RETRIEVAL_QUERY")
  2. similarity_search(session, query_embedding, top_k=5)
  3. Return list[dict] with chunk_text, similarity_score, document metadata
        │
        ▼
query/synthesizer.py  synthesize(question, retrieved_docs)
  1. Format context:
        "Source 1 (passport_john.pdf, score 0.92):\n{chunk_text}\n\n..."
  2. Prompt Gemini:
        "Answer the question using ONLY the sources below.
         If the answer is not in the sources, say so.
         Question: {question}
         Sources: {context}"
  3. Return {answer: str, sources: list[dict]}
```

The synthesizer is strictly grounded — it is instructed to answer only from the
provided sources and to state when information is absent. This prevents hallucination
of document content.

---

## /search endpoint

`POST /search/` — semantic search without synthesis. Returns ranked document results.

Request:
```json
{"query": "balance arithmetic", "doc_type": "bank_statement", "top_k": 5}
```

Response:
```json
[
  {
    "document_id": "uuid...",
    "filename": "bank_statement_2024.pdf",
    "doc_type": "bank_statement",
    "similarity_score": 0.89,
    "excerpt": "{\"account_holder\": \"Jane Doe\", \"opening_balance\": 4250.00...}"
  }
]
```

The `excerpt` is the first 300 characters of `chunk_text`. `doc_type` filter is
optional — omit to search across all document types.

---

## Retriever

`query/retriever.py`:

```python
def retrieve(
    question: str,
    doc_type: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    session = get_session()
    embedding = embed(question, task_type="RETRIEVAL_QUERY")
    results = similarity_search(session, embedding, top_k=top_k, doc_type=doc_type)
    return [
        {
            "document_id": row.document_id,
            "chunk_text": row.chunk_text,
            "similarity_score": 1.0 - distance,
            "doc_type": ...,
            "filename": ...,
        }
        for row, distance in results
    ]
```

---

## Asymmetric embeddings in Q&A

Stored document embeddings were created with default task type (`RETRIEVAL_DOCUMENT`).
Query embeddings use `RETRIEVAL_QUERY`. The Gemini `gemini-embedding-001` model is
designed for this asymmetric usage — the query and document spaces are different, so
cosine similarity between a query embedding and a document embedding is higher than
between two document embeddings of similar content. This improves recall for natural
language questions over technical JSON content.

---

## Embedding scope

Currently, the entire `extracted_fields` JSON is embedded as a single chunk
(`chunk_index=0`). This works well for whole-document retrieval but may miss
sub-document details for long bank statements with many transactions.

Future enhancement (not yet implemented): chunk large documents by field group
(e.g., one chunk per transaction row) and store multiple `DocumentEmbedding` rows
with incrementing `chunk_index`.

---

## Integration with the rest of the platform

The `/search/` and `/query/` endpoints only read from `document_embeddings` — they
never write to the DB. The embedding write path is exclusively in `write_output` via
`LearningPolicy`. This separation ensures the knowledge base only grows from
documents that the pipeline has fully validated.
