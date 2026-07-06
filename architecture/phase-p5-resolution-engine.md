# P5 — Resolution Engine

**Status:** ✅ Done  
**Scope:** ResolutionPlanner, StrategyExecutor, Strategy enum, execution side-effects

---

## What P5 delivered

P5 is the decision layer between the Truth Engine and the retry/HITL/accept paths.
The Resolution Engine takes a `TruthReport` and decides what to do next — accept,
pick a specific recovery strategy, escalate to a human, or reject. This replaces
the static confidence-threshold routing with a principled, extensible strategy system.

---

## Why a Resolution Engine?

The original routing after validation was a simple threshold check: above 0.85 →
accept, below → retry, retries exhausted → HITL. This works but:

1. It doesn't distinguish **why** confidence is low (missing fields vs. wrong field
   values vs. bad image quality) — all failures get the same retry.
2. It doesn't enable targeted recovery (use a better model, preprocess the image,
   refine the prompt) based on failure mode.
3. The routing logic was scattered in `router.py` with no audit trail.

The Resolution Engine externalises this decision into `ResolutionPlanner` + `StrategyExecutor`
with a full `ResolutionDecision` audit record per run.

---

## Graph position

```
extract → truth_engine → resolution_planner → strategy_executor
                                                      │
              ┌────────────┬──────────────────────────┤
              │            │                          │
          ACCEPT      RETRY / ...                   HITL
         normalize    op_a_retry               op_b_hitl
              │            │                          │
              └────────────┴──────────────────────────┘
                                     ▼
                                  persist
```

`route_after_executor` dispatches based on `ResolutionDecision.strategy`:
- `ACCEPT` → `normalize`
- `RETRY`, `PROMPT_REFINEMENT`, `BETTER_RETRIEVAL`, `IMAGE_PREPROCESS`, `MODEL_ESCALATION` → `op_a_retry`
- `HITL` → `op_b_hitl`
- `REJECT` → `persist` (skips normalize)

---

## Strategy enum

`pipelines/resolution/models.py`:

```python
class Strategy(str, Enum):
    ACCEPT           = "ACCEPT"
    RETRY            = "RETRY"
    PROMPT_REFINEMENT = "PROMPT_REFINEMENT"
    BETTER_RETRIEVAL  = "BETTER_RETRIEVAL"
    IMAGE_PREPROCESS  = "IMAGE_PREPROCESS"
    MODEL_ESCALATION  = "MODEL_ESCALATION"
    HITL             = "HITL"
    REJECT           = "REJECT"
```

---

## ResolutionDecision

```python
@dataclass
class ResolutionDecision:
    strategy: Strategy
    reason: str
    requires_human: bool          # True when strategy is HITL
    learning_candidate: bool      # True when this run is worth learning from
    retry_count: int
```

One `ResolutionDecision` per pipeline run. Stored in `GraphState.resolution_decision`
and persisted to `PersistenceAuditLog.resolution_strategy` by `write_output`.

---

## ResolutionPlanner

`pipelines/resolution/planner.py` — maps `TruthReport` → `ResolutionDecision`:

| Condition | Strategy selected |
|---|---|
| `allow_completion=True` | `ACCEPT` |
| Missing required fields AND retry available | `PROMPT_REFINEMENT` (targeted fix) |
| Verifier failed AND retry available | `BETTER_RETRIEVAL` (get better examples) |
| Image quality signal AND retry available | `IMAGE_PREPROCESS` |
| Repeated failure on same doc AND retry available | `MODEL_ESCALATION` |
| Retry available, no specific signal | `RETRY` |
| `requires_human=True` in TruthReport | `HITL` |
| Retries exhausted | `HITL` |
| UNKNOWN doc_type with no recovery | `REJECT` |

The planner reads `state.retry_count` against the `RetryPolicy.max_retries` from the
`DocumentRegistry` to determine whether retries are available.

---

## StrategyExecutor

`pipelines/resolution/executor.py` — applies the strategy's side-effects to GraphState:

| Strategy | Side-effect | How used by op_a_retry |
|---|---|---|
| `PROMPT_REFINEMENT` | `refined_prompt: RefinedPrompt` | Added to extraction prompt as additional instructions |
| `BETTER_RETRIEVAL` | `better_retrieval_queries: list[str]` | Replaces default `doc_type` query for similarity_search |
| `IMAGE_PREPROCESS` | `preprocessed_bytes`, `preprocessed_mime_type` | Replaces `raw_bytes` for the extraction call |
| `MODEL_ESCALATION` | `model_override: str` | Passed to `llm_client.generate` |
| `ACCEPT`, `RETRY`, `HITL`, `REJECT` | No state side-effects | — |

### Side-effect isolation

`op_a_retry_node` reads and applies the side-effects, then clears them to `None`
before re-invoking `truth_engine`. This prevents stale side-effects from leaking
into subsequent retry passes where a different strategy may be selected.

```python
# op_a_retry_node clears after use:
return {
    "refined_prompt": None,
    "better_retrieval_queries": None,
    "preprocessed_bytes": None,
    "preprocessed_mime_type": None,
    "model_override": None,
    "retry_count": state["retry_count"] + 1,
    ...
}
```

---

## ExecutionRecord

Each pass through `strategy_executor` appends an `ExecutionRecord` to
`GraphState.execution_history`:

```python
@dataclass
class ExecutionRecord:
    strategy: Strategy
    retry_count: int
    timestamp: str
    side_effects: dict            # which side-effects were set
```

`execution_history` accumulates via `operator.add` reducer across all retry passes.
Used by `LearningPolicy` to understand the full resolution history for a document.

---

## Retry loop

When `strategy_executor` selects a retry-family strategy, `op_a_retry` re-extracts
and then feeds the result back through the full resolution cycle:

```
op_a_retry → truth_engine → resolution_planner → strategy_executor → ...
```

This means each retry gets a fresh `TruthReport` and a fresh `ResolutionDecision`.
The retry count is incremented by `op_a_retry_node`. When `retry_count ≥ max_retries`,
`ResolutionPlanner` selects `HITL`.

---

## Integration with LearningPolicy

`ResolutionDecision.learning_candidate` signals to `LearningPolicy` (in `write_output`)
whether this document's extraction is worth embedding for future RAG retrieval.

Criteria for `learning_candidate=True`:
- `ACCEPT` strategy (high-quality extraction)
- OR `HITL` with `hitl_approved=True` (human confirmed correctness)

This ensures the RAG knowledge base only grows from high-quality examples.
