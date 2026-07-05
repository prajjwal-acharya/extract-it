"""Tests for Phase 5.4 — Autonomous Recovery.

Covers:
  - DirectiveEngine: evidence → Directive mapping, to_prompt_instructions,
    to_retrieval_queries, to_preprocessing_ops
  - BetterRetrievalStrategy: query construction from evidence
  - ImagePreprocessStrategy: deterministic PIL / PyMuPDF operations
  - ModelEscalationStrategy: model selection and metadata
  - Planner cycling through all 4 autonomous strategies in order
  - Deduplication semantics (VARIANT_DEDUPED vs TRIED_EVER)
  - Strategy budget exhaustion
  - Execution analytics fields (directives, model_used, retrieval_count,
    preprocessing_steps) in ExecutionRecord
  - Regression: RETRY, PROMPT_REFINEMENT, HITL, ACCEPT unchanged
"""

from __future__ import annotations

import io
from typing import cast

from pipelines.resolution.better_retrieval import BetterRetrievalStrategy
from pipelines.resolution.directives import Directive, DirectiveEngine
from pipelines.resolution.image_preprocess import ImagePreprocessStrategy
from pipelines.resolution.model_escalation import ModelEscalationStrategy
from pipelines.resolution.models import (
    ExecutionRecord,
    PlannerBundle,
    ResolutionDecision,
    Strategy,
)
from pipelines.resolution.planner import ResolutionPlanner
from pipelines.resolution.prompt_refinement import failure_variant
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)
from pipelines.state import GraphState


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_report(
    final_confidence: float = 0.60,
    required_fields_missing: list[str] | None = None,
    verifier_names_failed: list[str] | None = None,
    coverage_score: float = 1.0,
) -> TruthReport:
    verification_reports = [
        VerificationReport(verifier_name=n, passed=False, confidence=0.0)
        for n in (verifier_names_failed or [])
    ]
    missing = required_fields_missing or []
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=final_confidence, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=missing,
            additional_fields=[],
            coverage_score=coverage_score,
        ),
        verification_reports=verification_reports,
        final_confidence=final_confidence,
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="failed",
            allow_completion=False,
            allow_embedding=False,
            allow_learning=False,
            reason="test",
        ),
    )


def _plan(
    truth_report: TruthReport | None,
    retry_count: int = 0,
    max_retries: int = 6,
    execution_history: list | None = None,
) -> ResolutionDecision:
    planner = ResolutionPlanner(max_retries=max_retries, confidence_threshold=0.85)
    bundle = PlannerBundle(
        truth_report=truth_report,
        execution_history=execution_history or [],
        retry_count=retry_count,
        remaining_budget=max(0, max_retries - retry_count),
    )
    return planner.plan(bundle)


def _exec_record(strategy: Strategy, variant: str | None = None) -> ExecutionRecord:
    return ExecutionRecord(
        strategy=strategy,
        timestamp="t",
        outcome="scheduled",
        confidence_before=0.60,
        confidence_after=None,
        strategy_metadata={"prompt_variant": variant} if variant else {},
    )


def _all_tried_history(variant: str) -> list[ExecutionRecord]:
    """History with all 4 autonomous strategies tried for the given variant."""
    return [
        _exec_record(Strategy.PROMPT_REFINEMENT, variant),
        _exec_record(Strategy.BETTER_RETRIEVAL, variant),
        _exec_record(Strategy.IMAGE_PREPROCESS),
        _exec_record(Strategy.MODEL_ESCALATION),
    ]


# ---------------------------------------------------------------------------
# DirectiveEngine — evidence → Directive mapping
# ---------------------------------------------------------------------------


class TestDirectiveEngineGenerate:
    def setup_method(self):
        self.engine = DirectiveEngine()

    def test_verifier_failure_produces_recheck_extraction(self) -> None:
        report = _make_report(verifier_names_failed=["mrz_check"])
        directives = self.engine.generate(report)
        assert Directive.RECHECK_EXTRACTION in directives

    def test_multiple_verifier_failures_produce_single_recheck(self) -> None:
        report = _make_report(verifier_names_failed=["mrz_check", "date_check"])
        directives = self.engine.generate(report)
        assert directives.count(Directive.RECHECK_EXTRACTION) == 1

    def test_missing_passport_number_produces_focus_mrz(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        directives = self.engine.generate(report)
        assert Directive.FOCUS_MRZ in directives

    def test_missing_pan_produces_search_pan(self) -> None:
        report = _make_report(required_fields_missing=["pan_number"])
        directives = self.engine.generate(report)
        assert Directive.SEARCH_PAN in directives

    def test_missing_total_amount_produces_verify_totals(self) -> None:
        report = _make_report(required_fields_missing=["total_amount"])
        directives = self.engine.generate(report)
        assert Directive.VERIFY_TOTALS in directives

    def test_missing_signature_produces_check_signature(self) -> None:
        report = _make_report(required_fields_missing=["signature"])
        directives = self.engine.generate(report)
        assert Directive.CHECK_SIGNATURE in directives

    def test_missing_property_address_produces_search_property_metadata(self) -> None:
        report = _make_report(required_fields_missing=["property_address"])
        directives = self.engine.generate(report)
        assert Directive.SEARCH_PROPERTY_METADATA in directives

    def test_low_coverage_produces_inspect_all_sections_and_expand_retrieval(self) -> None:
        report = _make_report(coverage_score=0.50)
        directives = self.engine.generate(report)
        assert Directive.INSPECT_ALL_SECTIONS in directives
        assert Directive.EXPAND_RETRIEVAL in directives

    def test_very_low_confidence_produces_high_contrast_and_escalate_precision(self) -> None:
        report = _make_report(final_confidence=0.30)
        directives = self.engine.generate(report)
        assert Directive.HIGH_CONTRAST_READ in directives
        assert Directive.ESCALATE_PRECISION in directives

    def test_catch_all_escalate_precision_when_no_other_directive(self) -> None:
        # High confidence, full coverage, no verifier failures, no missing fields
        report = _make_report(final_confidence=0.70, coverage_score=0.95)
        directives = self.engine.generate(report)
        assert Directive.ESCALATE_PRECISION in directives

    def test_directives_are_deduplicated(self) -> None:
        # Two missing fields that both map to FOCUS_MRZ → should appear once
        report = _make_report(required_fields_missing=["passport_number", "mrz_line1"])
        directives = self.engine.generate(report)
        assert directives.count(Directive.FOCUS_MRZ) == 1

    def test_unknown_field_produces_no_field_directive(self) -> None:
        report = _make_report(required_fields_missing=["some_novel_field_xyz"])
        directives = self.engine.generate(report)
        # No field-specific directive, but catch-all triggers
        assert len(directives) > 0

    def test_output_is_nonempty_for_any_report(self) -> None:
        for conf in [0.30, 0.60, 0.95]:
            report = _make_report(final_confidence=conf)
            directives = self.engine.generate(report)
            assert len(directives) >= 1


class TestDirectiveEngineToPromptInstructions:
    def setup_method(self):
        self.engine = DirectiveEngine()

    def test_focus_mrz_instruction_mentions_mrz(self) -> None:
        text = self.engine.to_prompt_instructions([Directive.FOCUS_MRZ])
        assert "MRZ" in text

    def test_search_pan_instruction_mentions_pan(self) -> None:
        text = self.engine.to_prompt_instructions([Directive.SEARCH_PAN])
        assert "PAN" in text

    def test_multiple_directives_produce_multiple_lines(self) -> None:
        text = self.engine.to_prompt_instructions(
            [Directive.FOCUS_MRZ, Directive.RECHECK_EXTRACTION]
        )
        lines = [line for line in text.split("\n") if line.strip()]
        assert len(lines) >= 2

    def test_empty_directive_list_produces_empty_string(self) -> None:
        text = self.engine.to_prompt_instructions([])
        assert text == ""


class TestDirectiveEngineToRetrievalQueries:
    def setup_method(self):
        self.engine = DirectiveEngine()

    def test_queries_include_doc_type(self) -> None:
        queries = self.engine.to_retrieval_queries([Directive.FOCUS_MRZ], "passport")
        assert all("passport" in q for q in queries)

    def test_queries_are_nonempty_for_any_directive(self) -> None:
        for d in Directive:
            queries = self.engine.to_retrieval_queries([d], "passport")
            assert queries, f"No query for directive {d}"

    def test_empty_directives_returns_doc_type_fallback(self) -> None:
        queries = self.engine.to_retrieval_queries([], "invoice")
        assert queries == ["invoice"]

    def test_queries_are_deduplicated(self) -> None:
        # Two directives with the same query fragment → one query
        directives = [Directive.FOCUS_MRZ, Directive.FOCUS_MRZ]
        queries = self.engine.to_retrieval_queries(directives, "passport")
        assert len(queries) == len(set(queries))


class TestDirectiveEngineToPreprocessingOps:
    def setup_method(self):
        self.engine = DirectiveEngine()

    def test_high_contrast_read_maps_to_contrast_and_sharpen(self) -> None:
        ops = self.engine.to_preprocessing_ops([Directive.HIGH_CONTRAST_READ])
        assert "contrast_enhance" in ops
        assert "sharpen" in ops

    def test_inspect_all_sections_maps_to_render_hires(self) -> None:
        ops = self.engine.to_preprocessing_ops([Directive.INSPECT_ALL_SECTIONS])
        assert "render_hires" in ops

    def test_ops_are_deduplicated(self) -> None:
        # FOCUS_MRZ and RECHECK_EXTRACTION both map to sharpen
        ops = self.engine.to_preprocessing_ops([Directive.FOCUS_MRZ, Directive.RECHECK_EXTRACTION])
        assert ops.count("sharpen") == 1

    def test_no_preprocessing_directive_returns_empty_list(self) -> None:
        ops = self.engine.to_preprocessing_ops([Directive.CHECK_SIGNATURE])
        assert ops == []


# ---------------------------------------------------------------------------
# BetterRetrievalStrategy
# ---------------------------------------------------------------------------


class TestBetterRetrievalStrategy:
    def setup_method(self):
        self.strategy = BetterRetrievalStrategy()

    def test_returns_nonempty_queries(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        queries, directives, meta = self.strategy.build_queries(report, "passport")
        assert len(queries) >= 1
        assert len(directives) >= 1

    def test_queries_include_doc_type(self) -> None:
        report = _make_report(required_fields_missing=["pan_number"])
        queries, _, _ = self.strategy.build_queries(report, "pan_card")
        assert all("pan_card" in q for q in queries)

    def test_metadata_contains_directives(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        _, directives, meta = self.strategy.build_queries(report, "passport")
        assert "directives" in meta
        assert len(meta["directives"]) == len(directives)

    def test_metadata_contains_query_count(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        queries, _, meta = self.strategy.build_queries(report, "passport")
        assert meta["query_count"] == len(queries)

    def test_metadata_contains_missing_fields(self) -> None:
        report = _make_report(required_fields_missing=["passport_number", "surname"])
        _, _, meta = self.strategy.build_queries(report, "passport")
        assert "passport_number" in meta["missing_fields"]

    def test_metadata_contains_failed_verifiers(self) -> None:
        report = _make_report(verifier_names_failed=["mrz_check"])
        _, _, meta = self.strategy.build_queries(report, "passport")
        assert "mrz_check" in meta["failed_verifiers"]

    def test_verifier_failure_produces_recheck_query(self) -> None:
        report = _make_report(verifier_names_failed=["mrz_check"])
        queries, _, _ = self.strategy.build_queries(report, "passport")
        # RECHECK_EXTRACTION directive maps to re-extraction query fragment
        assert len(queries) >= 1

    def test_multiple_evidence_types_produce_multiple_queries(self) -> None:
        report = _make_report(
            required_fields_missing=["passport_number", "pan_number"],
            verifier_names_failed=["mrz_check"],
        )
        queries, _, _ = self.strategy.build_queries(report, "passport")
        assert len(queries) >= 2


# ---------------------------------------------------------------------------
# ImagePreprocessStrategy
# ---------------------------------------------------------------------------


def _minimal_png() -> bytes:
    """1x1 white PNG."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


class TestImagePreprocessStrategy:
    def setup_method(self):
        self.strategy = ImagePreprocessStrategy()

    def test_empty_ops_returns_original_bytes(self) -> None:
        raw = b"unchanged"
        out_bytes, out_mime, applied = self.strategy.preprocess(raw, "image/png", [])
        assert out_bytes == raw
        assert out_mime == "image/png"
        assert applied == []

    def test_contrast_enhance_on_png(self) -> None:
        png = _minimal_png()
        out_bytes, out_mime, applied = self.strategy.preprocess(
            png, "image/png", ["contrast_enhance"]
        )
        assert "contrast_enhance" in applied
        assert out_mime == "image/png"
        assert len(out_bytes) > 0

    def test_sharpen_on_png(self) -> None:
        png = _minimal_png()
        out_bytes, out_mime, applied = self.strategy.preprocess(png, "image/png", ["sharpen"])
        assert "sharpen" in applied

    def test_denoise_on_png(self) -> None:
        png = _minimal_png()
        out_bytes, out_mime, applied = self.strategy.preprocess(png, "image/png", ["denoise"])
        assert "denoise" in applied

    def test_multiple_ops_applied_in_order(self) -> None:
        png = _minimal_png()
        ops = ["contrast_enhance", "sharpen", "denoise"]
        _, _, applied = self.strategy.preprocess(png, "image/png", ops)
        # All three must be applied, preserving order
        assert applied == ["contrast_enhance", "sharpen", "denoise"]

    def test_unknown_op_is_skipped_without_error(self) -> None:
        png = _minimal_png()
        out_bytes, out_mime, applied = self.strategy.preprocess(
            png, "image/png", ["contrast_enhance", "nonexistent_op"]
        )
        assert "contrast_enhance" in applied
        assert "nonexistent_op" not in applied

    def test_output_mime_type_is_png_after_pil_ops(self) -> None:
        png = _minimal_png()
        _, out_mime, _ = self.strategy.preprocess(png, "image/png", ["sharpen"])
        assert out_mime == "image/png"

    def test_jpeg_input_accepted(self) -> None:
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (2, 2), color=(128, 128, 128)).save(buf, format="JPEG")
        jpeg = buf.getvalue()
        out_bytes, _, applied = self.strategy.preprocess(jpeg, "image/jpeg", ["contrast_enhance"])
        assert "contrast_enhance" in applied


# ---------------------------------------------------------------------------
# ModelEscalationStrategy
# ---------------------------------------------------------------------------


class TestModelEscalationStrategy:
    def test_escalate_returns_model_name(self) -> None:
        strategy = ModelEscalationStrategy(escalation_model="gemini-3.1-pro")
        model_name, _ = strategy.escalate()
        assert model_name == "gemini-3.1-pro"

    def test_escalate_metadata_contains_base_and_escalation_model(self) -> None:
        strategy = ModelEscalationStrategy(escalation_model="gemini-3.1-pro")
        _, metadata = strategy.escalate()
        assert "base_model" in metadata
        assert "escalation_model" in metadata
        assert metadata["escalation_model"] == "gemini-3.1-pro"

    def test_escalation_model_falls_back_to_settings(self) -> None:
        from config.settings import settings

        strategy = ModelEscalationStrategy()
        model_name, _ = strategy.escalate()
        assert model_name == settings.GEMINI_ESCALATION_MODEL

    def test_escalate_is_deterministic(self) -> None:
        strategy = ModelEscalationStrategy(escalation_model="gemini-3.1-pro")
        result_a = strategy.escalate()
        result_b = strategy.escalate()
        assert result_a == result_b


# ---------------------------------------------------------------------------
# Planner — autonomous strategy cycle (Rules 4 / _AUTONOMOUS_STRATEGY_ORDER)
# ---------------------------------------------------------------------------


class TestPlannerAutonomousCycle:
    def test_first_failure_triggers_prompt_refinement(self) -> None:
        report = _make_report(final_confidence=0.60)
        decision = _plan(report, retry_count=0)
        assert decision.strategy == Strategy.PROMPT_REFINEMENT

    def test_after_prompt_refinement_tried_triggers_better_retrieval(self) -> None:
        report = _make_report(final_confidence=0.60)
        variant = failure_variant(report)
        history = [_exec_record(Strategy.PROMPT_REFINEMENT, variant)]
        decision = _plan(report, retry_count=1, execution_history=history)
        assert decision.strategy == Strategy.BETTER_RETRIEVAL

    def test_after_better_retrieval_tried_triggers_image_preprocess(self) -> None:
        report = _make_report(final_confidence=0.60)
        variant = failure_variant(report)
        history = [
            _exec_record(Strategy.PROMPT_REFINEMENT, variant),
            _exec_record(Strategy.BETTER_RETRIEVAL, variant),
        ]
        decision = _plan(report, retry_count=2, execution_history=history)
        assert decision.strategy == Strategy.IMAGE_PREPROCESS

    def test_after_image_preprocess_tried_triggers_model_escalation(self) -> None:
        report = _make_report(final_confidence=0.60)
        variant = failure_variant(report)
        history = [
            _exec_record(Strategy.PROMPT_REFINEMENT, variant),
            _exec_record(Strategy.BETTER_RETRIEVAL, variant),
            _exec_record(Strategy.IMAGE_PREPROCESS),
        ]
        decision = _plan(report, retry_count=3, execution_history=history)
        assert decision.strategy == Strategy.MODEL_ESCALATION

    def test_after_all_autonomous_strategies_tried_falls_to_retry(self) -> None:
        report = _make_report(final_confidence=0.60)
        variant = failure_variant(report)
        history = _all_tried_history(variant)
        decision = _plan(report, retry_count=4, max_retries=8, execution_history=history)
        assert decision.strategy == Strategy.RETRY

    def test_accept_before_budget_check(self) -> None:
        """A passing extraction must always ACCEPT even at max retries."""
        from pipelines.truth_engine.models import PersistenceDecision as PD

        report = TruthReport(
            extraction=ExtractionResult(
                fields={}, overall_confidence=0.95, context_used=False, sample_count=1
            ),
            field_validation=FieldValidationReport(
                required_fields_present=[],
                required_fields_missing=[],
                additional_fields=[],
                coverage_score=1.0,
            ),
            verification_reports=[],
            final_confidence=0.95,
            decision_reason="ok",
            persistence=PD(
                document_status="completed",
                allow_completion=True,
                allow_embedding=True,
                allow_learning=True,
                reason="ok",
            ),
        )
        # At exact budget (retry_count == max_retries) — must still ACCEPT
        decision = _plan(report, retry_count=6, max_retries=6)
        assert decision.strategy == Strategy.ACCEPT
        assert not decision.requires_human


# ---------------------------------------------------------------------------
# Planner — deduplication semantics
# ---------------------------------------------------------------------------


class TestPlannerDeduplication:
    def test_prompt_refinement_deduped_by_variant(self) -> None:
        """Different failure variant for same strategy → PROMPT_REFINEMENT allowed again."""
        report_missing = _make_report(required_fields_missing=["passport_number"])
        prior = _exec_record(Strategy.PROMPT_REFINEMENT, "low_confidence")
        decision = _plan(report_missing, retry_count=1, execution_history=[prior])
        assert decision.strategy == Strategy.PROMPT_REFINEMENT

    def test_better_retrieval_deduped_by_variant(self) -> None:
        """Different failure variant → BETTER_RETRIEVAL allowed again for new variant."""
        report_missing = _make_report(required_fields_missing=["pan_number"])
        variant_missing = failure_variant(report_missing)
        # Prior BETTER_RETRIEVAL was for a different (low_confidence) variant
        prior = _exec_record(Strategy.BETTER_RETRIEVAL, "low_confidence")
        # PROMPT_REFINEMENT was also tried for the new variant
        prior_refine = _exec_record(Strategy.PROMPT_REFINEMENT, variant_missing)
        decision = _plan(report_missing, retry_count=2, execution_history=[prior, prior_refine])
        assert decision.strategy == Strategy.BETTER_RETRIEVAL

    def test_image_preprocess_deduped_per_document_not_per_variant(self) -> None:
        """IMAGE_PREPROCESS tried once per document regardless of failure pattern."""
        report_b = _make_report(required_fields_missing=["passport_number"])
        variant_b = failure_variant(report_b)

        prior_refine_b = _exec_record(Strategy.PROMPT_REFINEMENT, variant_b)
        prior_retrieval_b = _exec_record(Strategy.BETTER_RETRIEVAL, variant_b)
        prior_proc = _exec_record(Strategy.IMAGE_PREPROCESS)  # no variant in record

        # Even with different failure pattern, IMAGE_PREPROCESS not tried again
        decision = _plan(
            report_b,
            retry_count=3,
            execution_history=[prior_refine_b, prior_retrieval_b, prior_proc],
        )
        assert decision.strategy != Strategy.IMAGE_PREPROCESS

    def test_model_escalation_deduped_per_document_not_per_variant(self) -> None:
        """MODEL_ESCALATION tried once per document regardless of failure pattern."""
        report = _make_report(final_confidence=0.55)
        variant = failure_variant(report)
        history = [
            _exec_record(Strategy.PROMPT_REFINEMENT, variant),
            _exec_record(Strategy.BETTER_RETRIEVAL, variant),
            _exec_record(Strategy.IMAGE_PREPROCESS),
            _exec_record(Strategy.MODEL_ESCALATION),
        ]
        # Now hit a different failure pattern — still can't re-escalate
        decision = _plan(report, retry_count=5, max_retries=8, execution_history=history)
        assert decision.strategy != Strategy.MODEL_ESCALATION

    def test_hitl_when_budget_exhausted(self) -> None:
        report = _make_report(final_confidence=0.60)
        variant = failure_variant(report)
        history = _all_tried_history(variant)
        # retry_count == max_retries → budget zero
        decision = _plan(report, retry_count=4, max_retries=4, execution_history=history)
        assert decision.strategy == Strategy.HITL
        assert decision.requires_human


# ---------------------------------------------------------------------------
# Execution analytics — ExecutionRecord fields
# ---------------------------------------------------------------------------


class TestExecutionAnalytics:
    def test_strategy_executor_node_records_directives(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision, RetryPlan

        report = _make_report(required_fields_missing=["passport_number"])
        decision = ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT,
            reason="test",
            requires_human=False,
            retry_plan=RetryPlan(
                attempt_number=1,
                reason="test",
                retrieval_strategy="similarity_search",
                prompt_strategy="refined",
                prompt_variant="missing_fields:passport_number",
            ),
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        record = result["execution_history"][0]
        assert isinstance(record.directives, list)
        assert len(record.directives) >= 1
        assert all(isinstance(d, str) for d in record.directives)

    def test_model_escalation_records_model_used(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision

        report = _make_report(final_confidence=0.55)
        decision = ResolutionDecision(
            strategy=Strategy.MODEL_ESCALATION,
            reason="test",
            requires_human=False,
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        record = result["execution_history"][0]
        assert record.model_used is not None
        assert len(record.model_used) > 0

    def test_better_retrieval_records_retrieval_count(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision

        report = _make_report(required_fields_missing=["passport_number"])
        decision = ResolutionDecision(
            strategy=Strategy.BETTER_RETRIEVAL,
            reason="test",
            requires_human=False,
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        record = result["execution_history"][0]
        assert record.retrieval_count >= 1

    def test_image_preprocess_records_preprocessing_steps(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision

        report = _make_report(final_confidence=0.30)  # triggers HIGH_CONTRAST_READ → ops
        decision = ResolutionDecision(
            strategy=Strategy.IMAGE_PREPROCESS,
            reason="test",
            requires_human=False,
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {
                    "resolution_decision": decision,
                    "truth_report": report,
                    "execution_history": [],
                    "raw_bytes": b"",
                    "filename": "doc.pdf",
                },
            )
        )
        record = result["execution_history"][0]
        assert isinstance(record.preprocessing_steps, list)

    def test_prompt_refinement_records_no_model_used(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision, RetryPlan

        report = _make_report(final_confidence=0.60)
        decision = ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT,
            reason="test",
            requires_human=False,
            retry_plan=RetryPlan(1, "test", "similarity_search", "refined"),
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        record = result["execution_history"][0]
        assert record.model_used is None

    def test_record_timestamps_are_iso8601(self) -> None:
        from pipelines.resolution.executor import StrategyExecutor
        from pipelines.resolution.models import ResolutionDecision

        executor = StrategyExecutor()
        for strategy in [
            Strategy.BETTER_RETRIEVAL,
            Strategy.IMAGE_PREPROCESS,
            Strategy.MODEL_ESCALATION,
        ]:
            decision = ResolutionDecision(strategy=strategy, reason="t", requires_human=False)
            records = executor.execute(decision, confidence_before=0.60)
            ts = records[0].timestamp
            assert "T" in ts, f"Not ISO 8601 for {strategy}"


# ---------------------------------------------------------------------------
# State field lifecycle — written by executor_node, cleared by op_a_retry_node
# ---------------------------------------------------------------------------


class TestStateFieldLifecycle:
    def test_prompt_refinement_sets_refined_prompt_clears_others(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision, RetryPlan

        report = _make_report(final_confidence=0.60)
        decision = ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT,
            reason="test",
            requires_human=False,
            retry_plan=RetryPlan(1, "test", "similarity_search", "refined"),
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        assert result["refined_prompt"] is not None
        assert result["better_retrieval_queries"] is None
        assert result["preprocessed_bytes"] is None
        assert result["model_override"] is None

    def test_better_retrieval_sets_queries_clears_others(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision

        report = _make_report(final_confidence=0.60)
        decision = ResolutionDecision(
            strategy=Strategy.BETTER_RETRIEVAL,
            reason="test",
            requires_human=False,
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        assert result["better_retrieval_queries"] is not None
        assert result["refined_prompt"] is None
        assert result["preprocessed_bytes"] is None
        assert result["model_override"] is None

    def test_model_escalation_sets_model_override_clears_others(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision

        report = _make_report(final_confidence=0.55)
        decision = ResolutionDecision(
            strategy=Strategy.MODEL_ESCALATION,
            reason="test",
            requires_human=False,
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        assert result["model_override"] is not None
        assert result["refined_prompt"] is None
        assert result["better_retrieval_queries"] is None
        assert result["preprocessed_bytes"] is None

    def test_retry_clears_all_strategy_fields(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node
        from pipelines.resolution.models import ResolutionDecision, RetryPlan

        report = _make_report(final_confidence=0.60)
        decision = ResolutionDecision(
            strategy=Strategy.RETRY,
            reason="test",
            requires_human=False,
            retry_plan=RetryPlan(1, "test", "similarity_search", "standard"),
        )
        result = strategy_executor_node(
            cast(
                GraphState,
                {"resolution_decision": decision, "truth_report": report, "execution_history": []},
            )
        )
        assert result["refined_prompt"] is None
        assert result["better_retrieval_queries"] is None
        assert result["preprocessed_bytes"] is None
        assert result["model_override"] is None


# ---------------------------------------------------------------------------
# Regression — existing behavior (RETRY, PROMPT_REFINEMENT, HITL, ACCEPT)
# ---------------------------------------------------------------------------


class TestPhase5xRegressions:
    def test_hitl_when_no_truth_report(self) -> None:
        decision = _plan(None, retry_count=0)
        assert decision.strategy == Strategy.HITL
        assert decision.requires_human

    def test_accept_high_confidence(self) -> None:
        from pipelines.truth_engine.models import PersistenceDecision as PD

        report = TruthReport(
            extraction=ExtractionResult(
                fields={}, overall_confidence=0.95, context_used=False, sample_count=1
            ),
            field_validation=FieldValidationReport(
                required_fields_present=[],
                required_fields_missing=[],
                additional_fields=[],
                coverage_score=1.0,
            ),
            verification_reports=[],
            final_confidence=0.95,
            decision_reason="ok",
            persistence=PD(
                document_status="completed",
                allow_completion=True,
                allow_embedding=True,
                allow_learning=True,
                reason="ok",
            ),
        )
        decision = _plan(report, retry_count=0)
        assert decision.strategy == Strategy.ACCEPT
        assert not decision.requires_human

    def test_prompt_refinement_routes_to_op_a_retry(self) -> None:
        from pipelines.resolution.models import ResolutionDecision
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT, reason="r", requires_human=False
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_a_retry"  # type: ignore[arg-type,typeddict-item]

    def test_better_retrieval_routes_to_op_a_retry(self) -> None:
        from pipelines.resolution.models import ResolutionDecision
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.BETTER_RETRIEVAL, reason="r", requires_human=False
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_a_retry"  # type: ignore[arg-type,typeddict-item]

    def test_image_preprocess_routes_to_op_a_retry(self) -> None:
        from pipelines.resolution.models import ResolutionDecision
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.IMAGE_PREPROCESS, reason="r", requires_human=False
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_a_retry"  # type: ignore[arg-type,typeddict-item]

    def test_model_escalation_routes_to_op_a_retry(self) -> None:
        from pipelines.resolution.models import ResolutionDecision
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.MODEL_ESCALATION, reason="r", requires_human=False
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_a_retry"  # type: ignore[arg-type,typeddict-item]

    def test_hitl_routes_to_op_b_hitl(self) -> None:
        from pipelines.resolution.models import ResolutionDecision
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(strategy=Strategy.HITL, reason="r", requires_human=True)
        assert route_after_executor({"resolution_decision": decision}) == "op_b_hitl"  # type: ignore[arg-type,typeddict-item]

    def test_accept_routes_to_normalize(self) -> None:
        from pipelines.resolution.models import ResolutionDecision
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(strategy=Strategy.ACCEPT, reason="r", requires_human=False)
        assert route_after_executor({"resolution_decision": decision}) == "normalize"  # type: ignore[arg-type,typeddict-item]
