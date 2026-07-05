"""Tests for Phase 5.3 — PromptRefinementStrategy, PromptBuilder integration,
execution flow, and deduplication safeguards."""

from __future__ import annotations

import unittest.mock as mock

from pipelines.resolution.models import (
    ExecutionRecord,
    PlannerBundle,
    RefinedPrompt,
    ResolutionDecision,
    RetryPlan,
    Strategy,
)
from pipelines.resolution.planner import ResolutionPlanner
from pipelines.resolution.prompt_refinement import PromptRefinementStrategy, failure_variant
from pipelines.truth_engine.models import (
    ExtractionResult,
    FieldValidationReport,
    PersistenceDecision,
    TruthReport,
    VerificationReport,
)


# ---------------------------------------------------------------------------
# Helpers
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
    max_retries: int = 3,
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


def _prior_refinement(variant: str, retry_count: int = 0) -> ExecutionRecord:
    """Build an ExecutionRecord representing a completed PROMPT_REFINEMENT attempt."""
    return ExecutionRecord(
        strategy=Strategy.PROMPT_REFINEMENT,
        timestamp="2024-01-01T00:00:00Z",
        outcome="refinement_scheduled",
        confidence_before=0.60,
        confidence_after=None,
        strategy_metadata={"prompt_variant": variant},
    )


# ---------------------------------------------------------------------------
# failure_variant — shared computation
# ---------------------------------------------------------------------------


def test_failure_variant_verifier_beats_missing_fields() -> None:
    report = _make_report(
        verifier_names_failed=["mrz_check"],
        required_fields_missing=["passport_number"],
    )
    assert failure_variant(report).startswith("verifier_failure:")


def test_failure_variant_missing_fields_beats_low_coverage() -> None:
    report = _make_report(required_fields_missing=["passport_number"], coverage_score=0.50)
    assert failure_variant(report).startswith("missing_fields:")


def test_failure_variant_low_coverage_beats_low_confidence() -> None:
    report = _make_report(coverage_score=0.50, final_confidence=0.40)
    assert failure_variant(report) == "low_coverage"


def test_failure_variant_low_confidence_is_catch_all() -> None:
    report = _make_report(final_confidence=0.40)
    assert failure_variant(report) == "low_confidence"


def test_failure_variant_sorted_verifier_names() -> None:
    report = _make_report(verifier_names_failed=["z_check", "a_check"])
    assert failure_variant(report) == "verifier_failure:a_check,z_check"


def test_failure_variant_sorted_missing_fields() -> None:
    report = _make_report(required_fields_missing=["z_field", "a_field"])
    assert failure_variant(report) == "missing_fields:a_field,z_field"


def test_failure_variant_missing_fields_capped_at_five() -> None:
    many = [f"field_{i}" for i in range(10)]
    report = _make_report(required_fields_missing=many)
    variant = failure_variant(report)
    assert variant.startswith("missing_fields:")
    # At most 5 fields in the variant slug
    fields_part = variant[len("missing_fields:") :]
    assert len(fields_part.split(",")) <= 5


# ---------------------------------------------------------------------------
# PromptRefinementStrategy.generate()
# ---------------------------------------------------------------------------


class TestPromptRefinementStrategy:
    def setup_method(self) -> None:
        self.strategy = PromptRefinementStrategy()

    def test_generate_returns_refined_prompt(self) -> None:
        report = _make_report(final_confidence=0.60)
        result = self.strategy.generate(report)
        assert isinstance(result, RefinedPrompt)

    def test_generate_includes_additional_instructions(self) -> None:
        report = _make_report(final_confidence=0.60)
        result = self.strategy.generate(report)
        assert result.additional_instructions.strip()

    def test_generate_sets_prompt_variant(self) -> None:
        report = _make_report(final_confidence=0.60)
        result = self.strategy.generate(report)
        assert result.prompt_variant == "low_confidence"

    def test_generate_variant_matches_failure_variant(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        result = self.strategy.generate(report)
        assert result.prompt_variant == failure_variant(report)

    # ---- missing field rules ----

    def test_missing_passport_number_mentions_mrz(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        result = self.strategy.generate(report)
        assert (
            "MRZ" in result.additional_instructions
            or "mrz" in result.additional_instructions.lower()
        )

    def test_missing_pan_mentions_pan_format(self) -> None:
        report = _make_report(required_fields_missing=["pan_number"])
        result = self.strategy.generate(report)
        assert (
            "PAN" in result.additional_instructions
            or "alphanumeric" in result.additional_instructions
        )

    def test_missing_fields_listed_in_target_fields(self) -> None:
        report = _make_report(required_fields_missing=["passport_number", "surname"])
        result = self.strategy.generate(report)
        assert "passport_number" in result.target_fields
        assert "surname" in result.target_fields

    def test_missing_unknown_field_still_generates_instruction(self) -> None:
        """Fields not in the directive map must appear in the supplemental context section."""
        report = _make_report(required_fields_missing=["some_novel_field_xyz"])
        result = self.strategy.generate(report)
        # "some_novel_field_xyz" is not in _FIELD_DIRECTIVES → appended as uncovered field
        assert "some_novel_field_xyz" in result.additional_instructions

    # ---- verifier failure rules ----

    def test_verifier_failure_mentions_failed_verifier(self) -> None:
        """Phase 5.4: verifier name appended as specific context after directive instructions."""
        report = _make_report(verifier_names_failed=["mrz_check"])
        result = self.strategy.generate(report)
        # Verifier name is in the supplemental context section
        assert "mrz_check" in result.additional_instructions

    def test_verifier_failure_has_no_target_fields(self) -> None:
        report = _make_report(verifier_names_failed=["mrz_check"])
        result = self.strategy.generate(report)
        assert result.target_fields == []

    def test_unknown_verifier_still_generates_instruction(self) -> None:
        """Unknown verifier name must appear in the supplemental context section."""
        report = _make_report(verifier_names_failed=["custom_verifier_abc"])
        result = self.strategy.generate(report)
        assert "custom_verifier_abc" in result.additional_instructions

    # ---- low coverage rules ----

    def test_low_coverage_instructs_full_document_scan(self) -> None:
        report = _make_report(coverage_score=0.50, final_confidence=0.90)
        result = self.strategy.generate(report)
        assert any(
            kw in result.additional_instructions.lower()
            for kw in ("all sections", "headers", "footers", "margins")
        )

    # ---- low confidence rules ----

    def test_low_confidence_instructs_precision(self) -> None:
        report = _make_report(final_confidence=0.55)
        result = self.strategy.generate(report)
        assert any(
            kw in result.additional_instructions.lower()
            for kw in ("careful", "precise", "verify", "directly")
        )

    # ---- refinement_reason audit ----

    def test_generate_sets_refinement_reason(self) -> None:
        report = _make_report(required_fields_missing=["passport_number"])
        result = self.strategy.generate(report)
        assert result.refinement_reason.strip()


# ---------------------------------------------------------------------------
# Prompt Builder integration
# ---------------------------------------------------------------------------


class TestPromptBuilderIntegration:
    def test_additional_instructions_appended_to_base_prompt(self) -> None:
        from agents.prompt_builder import build_extraction_prompt

        instructions = "Focus on the MRZ zone."
        prompt = build_extraction_prompt("passport", additional_instructions=instructions)
        assert "Focus on the MRZ zone." in prompt

    def test_base_prompt_unchanged_without_additional_instructions(self) -> None:
        from agents.prompt_builder import build_extraction_prompt

        base = build_extraction_prompt("passport")
        refined = build_extraction_prompt("passport", additional_instructions="extra hint")
        # Base content still present
        assert "surname" in refined
        assert "overall_confidence" in refined
        # Refinement section added
        assert "extra hint" in refined
        # Base has no refinement section
        assert "Focused extraction guidance" not in base

    def test_additional_instructions_appear_before_envelope(self) -> None:
        from agents.prompt_builder import build_extraction_prompt

        prompt = build_extraction_prompt("passport", additional_instructions="Inspect the MRZ.")
        refinement_pos = prompt.index("Inspect the MRZ.")
        envelope_pos = prompt.index('"fields"')
        assert refinement_pos < envelope_pos

    def test_additional_instructions_appear_after_schema_guidance(self) -> None:
        from agents.prompt_builder import build_extraction_prompt

        prompt = build_extraction_prompt("passport", additional_instructions="Inspect the MRZ.")
        schema_pos = prompt.index("Business-critical fields")
        refinement_pos = prompt.index("Inspect the MRZ.")
        assert schema_pos < refinement_pos

    def test_context_and_additional_instructions_coexist(self) -> None:
        from agents.prompt_builder import build_extraction_prompt

        context = "Example context from RAG."
        prompt = build_extraction_prompt(
            "passport", context=context, additional_instructions="Focus on MRZ."
        )
        assert context in prompt
        assert "Focus on MRZ." in prompt

    def test_none_additional_instructions_leaves_prompt_clean(self) -> None:
        from agents.prompt_builder import build_extraction_prompt

        prompt = build_extraction_prompt("passport", additional_instructions=None)
        assert "Focused extraction guidance" not in prompt


# ---------------------------------------------------------------------------
# extract_agent — additional_instructions threaded through
# ---------------------------------------------------------------------------


def test_extract_agent_passes_additional_instructions_to_prompt(sample_pdf_bytes) -> None:
    """additional_instructions must appear in the prompt sent to the LLM."""
    from agents.extract_agent import extract

    captured_prompts: list[str] = []

    def fake_generate(prompt, **kwargs):
        captured_prompts.append(prompt)
        return '{"fields": {"surname": "TEST"}, "overall_confidence": 0.95}'

    with mock.patch("agents.extract_agent.generate", side_effect=fake_generate):
        result = extract(
            sample_pdf_bytes,
            "application/pdf",
            "passport",
            additional_instructions="Focus on the MRZ zone.",
        )

    assert result.success is True
    assert any("Focus on the MRZ zone." in p for p in captured_prompts)


def test_extract_agent_without_additional_instructions_is_unchanged(sample_pdf_bytes) -> None:
    """No additional_instructions → prompt unchanged (regression)."""
    from agents.extract_agent import extract

    captured_prompts: list[str] = []

    def fake_generate(prompt, **kwargs):
        captured_prompts.append(prompt)
        return '{"fields": {"surname": "TEST"}, "overall_confidence": 0.95}'

    with mock.patch("agents.extract_agent.generate", side_effect=fake_generate):
        extract(sample_pdf_bytes, "application/pdf", "passport")

    assert any("Focused extraction guidance" not in p for p in captured_prompts)


# ---------------------------------------------------------------------------
# Strategy executor — PROMPT_REFINEMENT node behaviour
# ---------------------------------------------------------------------------


class TestStrategyExecutorPromptRefinement:
    def _make_state(self, truth_report: TruthReport) -> dict:
        decision = ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT,
            reason="test",
            requires_human=False,
            retry_plan=RetryPlan(
                attempt_number=1,
                reason="test",
                retrieval_strategy="similarity_search",
                prompt_strategy="refined",
                prompt_variant="low_confidence",
            ),
        )
        return {
            "resolution_decision": decision,
            "truth_report": truth_report,
            "execution_history": [],
        }

    def test_executor_node_sets_refined_prompt(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node

        report = _make_report(final_confidence=0.60)
        result = strategy_executor_node(self._make_state(report))  # type: ignore[arg-type]
        assert result["refined_prompt"] is not None
        assert isinstance(result["refined_prompt"], RefinedPrompt)

    def test_executor_node_refined_prompt_has_instructions(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node

        report = _make_report(final_confidence=0.60)
        result = strategy_executor_node(self._make_state(report))  # type: ignore[arg-type]
        assert result["refined_prompt"].additional_instructions.strip()

    def test_executor_node_records_refinement_scheduled_outcome(self) -> None:
        from pipelines.nodes.strategy_executor import strategy_executor_node

        report = _make_report(final_confidence=0.60)
        result = strategy_executor_node(self._make_state(report))  # type: ignore[arg-type]
        assert result["execution_history"][0].outcome == "refinement_scheduled"

    def test_executor_node_retry_clears_refined_prompt(self) -> None:
        """RETRY strategy must set refined_prompt=None so it doesn't leak to next pass."""
        from pipelines.nodes.strategy_executor import strategy_executor_node

        report = _make_report(final_confidence=0.60)
        decision = ResolutionDecision(
            strategy=Strategy.RETRY,
            reason="test",
            requires_human=False,
            retry_plan=RetryPlan(1, "test", "similarity_search", "standard"),
        )
        state = {
            "resolution_decision": decision,
            "truth_report": report,
            "execution_history": [],
        }
        result = strategy_executor_node(state)  # type: ignore[arg-type]
        assert result["refined_prompt"] is None

    def test_executor_node_accept_clears_refined_prompt(self) -> None:
        """ACCEPT must also clear refined_prompt from state."""
        from pipelines.nodes.strategy_executor import strategy_executor_node

        report = _make_report(final_confidence=0.95)
        decision = ResolutionDecision(strategy=Strategy.ACCEPT, reason="ok", requires_human=False)
        state = {
            "resolution_decision": decision,
            "truth_report": report,
            "execution_history": [],
        }
        result = strategy_executor_node(state)  # type: ignore[arg-type]
        assert result["refined_prompt"] is None


# ---------------------------------------------------------------------------
# Duplicate refinement prevention
# ---------------------------------------------------------------------------


class TestDuplicateRefinementPrevention:
    def test_same_variant_triggers_better_retrieval_not_refinement(self) -> None:
        """Phase 5.4: after PROMPT_REFINEMENT tried, next untried strategy is BETTER_RETRIEVAL."""
        report = _make_report(final_confidence=0.55)
        variant = failure_variant(report)
        prior = _prior_refinement(variant)
        decision = _plan(report, retry_count=1, execution_history=[prior])
        assert decision.strategy == Strategy.BETTER_RETRIEVAL

    def test_different_variant_can_still_trigger_refinement(self) -> None:
        """A different failure pattern may still be refined even if one was already tried."""
        report_missing = _make_report(required_fields_missing=["passport_number"])

        # Only low_confidence was refined previously
        prior = _prior_refinement("low_confidence")

        # New failure pattern = missing_fields:passport_number → PROMPT_REFINEMENT still applies
        decision = _plan(report_missing, retry_count=1, execution_history=[prior])
        assert decision.strategy == Strategy.PROMPT_REFINEMENT
        assert decision.retry_plan is not None
        assert decision.retry_plan.prompt_variant is not None
        assert decision.retry_plan.prompt_variant.startswith("missing_fields:")

    def test_multiple_prior_refinements_tracked_in_retry_plan(self) -> None:
        """RetryPlan.refinement_history must list all prior variants when refinement triggers."""
        report_b = _make_report(
            required_fields_missing=["surname"]
        )  # missing_fields:surname variant

        prior_a = _prior_refinement("low_confidence")
        # New missing_fields variant → PROMPT_REFINEMENT with prior history
        decision = _plan(report_b, retry_count=1, execution_history=[prior_a])
        assert decision.strategy == Strategy.PROMPT_REFINEMENT
        assert decision.retry_plan is not None
        assert "low_confidence" in decision.retry_plan.refinement_history

    def test_no_infinite_loop_all_strategies_exhausted(self) -> None:
        """After all 4 autonomous strategies exhausted, fall to RETRY (then eventually HITL)."""
        report = _make_report(final_confidence=0.55)
        variant = failure_variant(report)

        def _rec(strategy, variant_key=None):
            return ExecutionRecord(
                strategy=strategy,
                timestamp="t",
                outcome="scheduled",
                confidence_before=0.55,
                confidence_after=None,
                strategy_metadata={"prompt_variant": variant_key} if variant_key else {},
            )

        history = [
            _rec(Strategy.PROMPT_REFINEMENT, variant),
            _rec(Strategy.BETTER_RETRIEVAL, variant),
            _rec(Strategy.IMAGE_PREPROCESS),
            _rec(Strategy.MODEL_ESCALATION),
        ]
        decision = _plan(report, retry_count=4, max_retries=8, execution_history=history)
        # All autonomous strategies tried → generic RETRY
        assert decision.strategy == Strategy.RETRY

    def test_image_preprocess_tried_only_once_per_document(self) -> None:
        """IMAGE_PREPROCESS dedup is variant-agnostic — tried once regardless of failure type."""
        report_b = _make_report(required_fields_missing=["passport_number"])  # missing_fields

        prior_proc = ExecutionRecord(
            strategy=Strategy.IMAGE_PREPROCESS,
            timestamp="t",
            outcome="preprocess_scheduled",
            confidence_before=0.55,
            confidence_after=None,
            strategy_metadata={},
        )
        # Even with a different failure pattern, IMAGE_PREPROCESS is not retried
        decision = _plan(report_b, retry_count=2, max_retries=6, execution_history=[prior_proc])
        assert decision.strategy != Strategy.IMAGE_PREPROCESS

    def test_model_escalation_tried_only_once_per_document(self) -> None:
        """MODEL_ESCALATION is tried at most once regardless of failure pattern."""
        report = _make_report(final_confidence=0.55)
        prior_esc = ExecutionRecord(
            strategy=Strategy.MODEL_ESCALATION,
            timestamp="t",
            outcome="escalation_scheduled",
            confidence_before=0.55,
            confidence_after=None,
            strategy_metadata={},
        )
        decision = _plan(report, retry_count=2, max_retries=6, execution_history=[prior_esc])
        assert decision.strategy != Strategy.MODEL_ESCALATION


# ---------------------------------------------------------------------------
# Retry loop regression — RETRY strategy behaviour unchanged
# ---------------------------------------------------------------------------


class TestRetryStrategyRegression:
    def test_retry_decision_routes_to_op_a_retry(self) -> None:
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.RETRY, reason="low_confidence", requires_human=False
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_a_retry"  # type: ignore

    def test_prompt_refinement_also_routes_to_op_a_retry(self) -> None:
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.PROMPT_REFINEMENT, reason="refinement", requires_human=False
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_a_retry"  # type: ignore

    def test_retry_strategy_metadata_unchanged(self) -> None:
        """RETRY execution records must still carry attempt_number, retrieval_strategy,
        and prompt_strategy — same format as before Phase 5.3."""
        from pipelines.resolution.executor import StrategyExecutor

        plan = RetryPlan(
            attempt_number=2,
            reason="low",
            retrieval_strategy="similarity_search",
            prompt_strategy="standard",
        )
        decision = ResolutionDecision(
            strategy=Strategy.RETRY, reason="low", requires_human=False, retry_plan=plan
        )
        records = StrategyExecutor().execute(decision, confidence_before=0.60)
        meta = records[0].strategy_metadata
        assert meta["attempt_number"] == 2
        assert meta["retrieval_strategy"] == "similarity_search"
        assert meta["prompt_strategy"] == "standard"
        assert "prompt_variant" not in meta  # RETRY has no variant

    def test_hitl_decision_still_routes_to_op_b_hitl(self) -> None:
        from pipelines.router import route_after_executor

        decision = ResolutionDecision(
            strategy=Strategy.HITL, reason="exhausted", requires_human=True
        )
        assert route_after_executor({"resolution_decision": decision}) == "op_b_hitl"  # type: ignore
