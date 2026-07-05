from __future__ import annotations

import logging

from pipelines.resolution.better_retrieval import BetterRetrievalStrategy
from pipelines.resolution.directives import DirectiveEngine
from pipelines.resolution.executor import StrategyExecutor
from pipelines.resolution.image_preprocess import ImagePreprocessStrategy
from pipelines.resolution.model_escalation import ModelEscalationStrategy
from pipelines.resolution.models import Strategy
from pipelines.resolution.prompt_refinement import PromptRefinementStrategy
from pipelines.state import GraphState
from pipelines.truth_engine.models import TruthReport
from shared.utils.mime import mime_from_filename

log = logging.getLogger(__name__)

_executor = StrategyExecutor()
_directive_engine = DirectiveEngine()
_refinement_strategy = PromptRefinementStrategy()
_retrieval_strategy = BetterRetrievalStrategy()
_preprocess_strategy = ImagePreprocessStrategy()
_escalation_strategy = ModelEscalationStrategy()


def _snapshot_evidence(truth_report: TruthReport | None) -> dict | None:
    if truth_report is None:
        return None
    return {
        "final_confidence": truth_report.final_confidence,
        "coverage_score": truth_report.field_validation.coverage_score,
        "required_fields_missing": truth_report.field_validation.required_fields_missing,
        "verifier_failures": [
            r.verifier_name for r in truth_report.verification_reports if r.passed is False
        ],
        "verifier_version": truth_report.verifier_version,
    }


def strategy_executor_node(state: GraphState) -> dict:
    """Execute the strategy chosen by resolution_planner_node.

    For every strategy:
      - Directives are computed from TruthReport evidence (DirectiveEngine)
      - Analytics (directives, model_used, retrieval_count, preprocessing_steps)
        are recorded in ExecutionRecord via StrategyExecutor.execute()
      - Strategy-specific side effects are stored in state fields:
          PROMPT_REFINEMENT → refined_prompt
          BETTER_RETRIEVAL  → better_retrieval_queries
          IMAGE_PREPROCESS  → preprocessed_bytes, preprocessed_mime_type
          MODEL_ESCALATION  → model_override
      - Non-selected strategy state fields are explicitly set to None so
        they do not leak from a prior pass to the current one.
    """
    decision = state["resolution_decision"]
    if decision is None:
        return {}
    truth_report = state.get("truth_report")
    confidence = truth_report.final_confidence if truth_report else 0.0
    evidence = _snapshot_evidence(truth_report)
    doc_type = state.get("doc_type") or ""
    filename = state.get("filename", "")

    # Generate directives from evidence (shared across all strategies)
    directives = _directive_engine.generate(truth_report) if truth_report is not None else []
    directive_values = [d.value for d in directives]

    # Strategy-specific side effects
    refined_prompt = None
    better_retrieval_queries: list[str] | None = None
    preprocessed_bytes: bytes | None = None
    preprocessed_mime_type: str | None = None
    model_override: str | None = None
    retrieval_count = 0
    preprocessing_steps: list[str] = []
    model_used: str | None = None

    if decision.strategy == Strategy.PROMPT_REFINEMENT and truth_report is not None:
        refined_prompt = _refinement_strategy.generate(truth_report)
        log.info(
            "event=PromptRefined variant=%s directives=%s target_fields=%s",
            refined_prompt.prompt_variant,
            directive_values,
            refined_prompt.target_fields,
        )

    elif decision.strategy == Strategy.BETTER_RETRIEVAL and truth_report is not None:
        queries, _, retrieval_meta = _retrieval_strategy.build_queries(truth_report, doc_type)
        better_retrieval_queries = queries
        retrieval_count = len(queries)
        log.info(
            "event=BetterRetrievalPlanned queries=%d directives=%s",
            len(queries),
            directive_values,
        )

    elif decision.strategy == Strategy.IMAGE_PREPROCESS:
        raw_bytes = state.get("raw_bytes") or b""
        mime_type = mime_from_filename(filename) if filename else "application/pdf"
        ops = _directive_engine.to_preprocessing_ops(directives) or ["contrast_enhance", "sharpen"]
        proc_bytes, proc_mime, ops_applied = _preprocess_strategy.preprocess(
            raw_bytes, mime_type, ops
        )
        preprocessed_bytes = proc_bytes
        preprocessed_mime_type = proc_mime
        preprocessing_steps = ops_applied
        log.info(
            "event=ImagePreprocessed ops_applied=%s directives=%s",
            ops_applied,
            directive_values,
        )

    elif decision.strategy == Strategy.MODEL_ESCALATION:
        escalation_model, esc_meta = _escalation_strategy.escalate()
        model_override = escalation_model
        model_used = escalation_model
        log.info(
            "event=ModelEscalated base_model=%s escalation_model=%s directives=%s",
            esc_meta["base_model"],
            escalation_model,
            directive_values,
        )

    records = _executor.execute(
        decision,
        confidence,
        evidence_before=evidence,
        directives=directive_values,
        model_used=model_used,
        retrieval_count=retrieval_count,
        preprocessing_steps=preprocessing_steps,
    )

    log.info(
        "event=StrategyExecuted strategy=%s outcome=%s confidence_before=%.4f directives=%s",
        decision.strategy.value,
        records[0].outcome if records else "none",
        confidence,
        directive_values,
    )

    return {
        "execution_history": records,
        "refined_prompt": refined_prompt,
        "better_retrieval_queries": better_retrieval_queries,
        "preprocessed_bytes": preprocessed_bytes,
        "preprocessed_mime_type": preprocessed_mime_type,
        "model_override": model_override,
    }
