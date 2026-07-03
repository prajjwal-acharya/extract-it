# P8 — Query endpoint, embedding task-type asymmetry

## Deliverables shipped

| File | Change |
|---|---|
| `agents/llm_client.py` | Added `task_type: str = "RETRIEVAL_DOCUMENT"` param to `embed()` |
| `pipelines/nodes/op_a_retry.py` | Fixed `embed()` call to `task_type="RETRIEVAL_QUERY"` |
| `query/retriever.py` | Stub → full |
| `query/synthesizer.py` | Stub → full |
| `api/routes/query.py` | Stub → full (`POST /query/`, Pydantic min_length=1 for 422 rejection) |
| `tests/unit/test_query.py` | All 4 stubs unstubbed |
| `tests/unit/test_api.py` | 2 query endpoint stubs unstubbed |
| `tests/unit/test_agents.py` | Added `test_embed_passes_task_type_to_config` regression guard |
| `tests/unit/test_frontend.py` | Catch-up — `test_review_app_module_is_importable` unstubbed |

## Design decisions

**Task-type asymmetry**: Gemini embeddings use different internal projections for RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY. Document embeddings stored at write time use the default (RETRIEVAL_DOCUMENT); query embeddings at retrieval time explicitly pass RETRIEVAL_QUERY. Op_a_retry was fixed to do the same since it embeds a query against the corpus, not a new document.

**Frontend test**: `review_app.py` executes `st.text_area()` at module level and passes the return value to `json.loads`. The mock must configure `st_mock.text_area.return_value = "{}"` before import; plain `MagicMock()` would fail `json.loads`.

## Test results

```
71 passed, 16 failed (pre-existing stubs), 4 deselected
```

Delta from P7 baseline (63 passed): +8.

## Live smoke

```
POST /query/ {"question": "What is the passport number and expiry date?"}
→ {"answer": "The passport number is Z43R34255 and the expiry date is 10/02/2020
   [Document 507ebd8b-b361-4160-b79c-06dbf48ee700].",
   "sources": ["507ebd8b-b361-4160-b79c-06dbf48ee700"]}
```

Correct document cited, correct values extracted from the P7 E2E passport run.
