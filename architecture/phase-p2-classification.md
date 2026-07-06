# P2 — Classification

**Status:** ✅ Done  
**Scope:** classify_agent, routing engine, DocumentRegistry, ClassificationContext

---

## What P2 delivered

P2 answers "what kind of document is this?" It introduces the classify agent, the
routing engine, and the `DocumentRegistry` — the single source of truth for all
supported document types. The classification result drives every downstream decision.

---

## classify_node flow

```
master_node fetches raw_bytes from object store
        │
        ▼
classify_node
  1. classify_agent(raw_bytes, mime_type, filename)
        │
        ├── Sends image bytes + prompt to Gemini
        ├── Parses: {"doc_type": "passport", "confidence": 0.92}
        └── Returns AgentResult(success, confidence, data={"doc_type": ...})

  2. Build ClassificationContext
        ├── RoutingEngine.classify(doc_type, confidence)
        ├── ConfidencePolicy.evaluate(confidence) → RoutingAction
        └── Build RoutingPlan(action, reason)

  3. Update GraphState:
        ├── doc_type = classified type
        ├── classify_confidence = score
        └── classification_context = ClassificationContext(routing_plan)
```

---

## DocumentRegistry

`pipelines/registry.py` — the single source of truth for all supported document types.
Replaces scattered `if doc_type == "passport"` chains throughout the codebase.

```python
registry = DocumentRegistry([
    RegistryEntry(
        document_type="passport",
        reference_schema_name="passport",
        extraction_prompt_key="passport",
        verifier_profile=("mrz_checksum",),
        retry_policy=RetryPolicy(max_retries=2),
        confidence_policy=ConfidencePolicy(proceed_threshold=0.70),
        rag_namespace="passport",
    ),
    ...  # bank_statement, salary_slip, itr, gst_invoice, property_deed,
         # driving_license, aadhaar, UNKNOWN
])
```

Every document type has exactly one `RegistryEntry`. To add a new type:
1. Add a `RegistryEntry` to `registry` in `registry.py`
2. Add a YAML schema in `config/schemas/<type>.yaml`
3. Add an Alembic migration to seed the schema

### ConfidencePolicy

```python
@dataclass(frozen=True)
class ConfidencePolicy:
    proceed_threshold: float = 0.70

    def evaluate(self, confidence: float) -> RoutingAction:
        if confidence >= self.proceed_threshold:
            return RoutingAction.PROCEED
        return RoutingAction.UNKNOWN
```

`UNKNOWN` has `proceed_threshold=0.0` so it always evaluates to `UNKNOWN` —
documents of unknown type can never self-route to extraction.

---

## Routing after classification

`_route_after_classify` in `pipelines/graph.py`:

```python
def _route_after_classify(state: GraphState) -> str:
    ctx = state.get("classification_context")
    if ctx is not None and ctx.routing_plan.action == RoutingAction.PROCEED:
        return "extract"
    return "unknown_handler"
```

| RoutingAction | Next node |
|---|---|
| `PROCEED` | `extract` |
| `UNKNOWN` | `unknown_handler` |
| `FAILURE` | `unknown_handler` |

`unknown_handler` → `persist` (skips extraction entirely, document ends as `failed`).

---

## ClassificationContext

`pipelines/routing_engine.py` — carries the full classification context for auditing:

```python
@dataclass
class ClassificationContext:
    doc_type: str
    confidence: float
    routing_plan: RoutingPlan

@dataclass
class RoutingPlan:
    action: RoutingAction
    reason: str
```

This is stored in `GraphState.classification_context` and is available to all
downstream nodes for conditional logic and audit logging.

---

## Routing version tracking

`GraphState.routing_version` records which version of the registry produced the
routing decision. Enables reproducibility: knowing which routing logic was applied
makes it possible to re-evaluate old decisions when the registry changes.

---

## Supported document types (as of current registry)

| doc_type | proceed_threshold | verifier_profile |
|---|---|---|
| `passport` | 0.70 | `mrz_checksum` |
| `bank_statement` | 0.70 | `balance_arithmetic` |
| `salary_slip` | 0.70 | — |
| `itr` | 0.70 | — |
| `gst_invoice` | 0.70 | — |
| `property_deed` | 0.70 | — |
| `driving_license` | 0.70 | — |
| `aadhaar` | 0.70 | — |
| `UNKNOWN` | 0.00 | — |

---

## classify_agent prompt design

The classify agent sends the raw document bytes to Gemini as an image (multimodal
input). The prompt asks Gemini to return exactly one `doc_type` string from the
supported list and a confidence score ∈ [0.0, 1.0]. The response is parsed with
Pydantic for strict type safety.

When `GOOGLE_API_KEY` is absent or mocked in tests, the agent returns a synthetic
`AgentResult` with a fixed `doc_type` and confidence so pipeline integration tests
can run without live LLM costs.
