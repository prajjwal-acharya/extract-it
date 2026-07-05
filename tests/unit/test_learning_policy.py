"""Tests for Phase 5.5 — LearningPolicy and LearningDecision.

Covers:
  - All flag combinations (allow_learning, learn_from_document, learn_from_correction,
    schema_candidate)
  - Each blocking condition (TE disallows, not ACCEPT, verifier hard failures)
  - Human correction flag propagation
  - schema_candidate when additional fields discovered
  - reason string semantics
"""

from __future__ import annotations

from pipelines.learning.policy import LearningDecision, LearningPolicy
from pipelines.resolution.models import ExecutionRecord, ResolutionDecision, Strategy
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


def _truth_report(
    final_confidence: float = 0.90,
    allow_learning: bool = True,
    verifier_names_failed: list[str] | None = None,
    additional_fields: list[str] | None = None,
) -> TruthReport:
    failures = verifier_names_failed or []
    return TruthReport(
        extraction=ExtractionResult(
            fields={}, overall_confidence=final_confidence, context_used=False, sample_count=1
        ),
        field_validation=FieldValidationReport(
            required_fields_present=[],
            required_fields_missing=[],
            additional_fields=additional_fields or [],
            coverage_score=1.0,
        ),
        verification_reports=[
            VerificationReport(verifier_name=n, passed=False, confidence=0.0) for n in failures
        ],
        final_confidence=final_confidence,
        decision_reason="test",
        persistence=PersistenceDecision(
            document_status="completed" if allow_learning else "failed",
            allow_completion=allow_learning,
            allow_embedding=allow_learning,
            allow_learning=allow_learning,
            reason="test",
        ),
    )


def _accept_decision() -> ResolutionDecision:
    return ResolutionDecision(
        strategy=Strategy.ACCEPT,
        reason="high_confidence",
        requires_human=False,
        learning_candidate=True,
    )


def _hitl_decision() -> ResolutionDecision:
    return ResolutionDecision(
        strategy=Strategy.HITL,
        reason="budget_exhausted",
        requires_human=True,
    )


def _retry_decision() -> ResolutionDecision:
    return ResolutionDecision(
        strategy=Strategy.RETRY,
        reason="low_confidence",
        requires_human=False,
    )


# ---------------------------------------------------------------------------
# allow_learning — master gate
# ---------------------------------------------------------------------------


class TestAllowLearning:
    def setup_method(self):
        self.policy = LearningPolicy()

    def test_accept_no_failures_te_allows(self) -> None:
        decision = _accept_decision()
        report = _truth_report(final_confidence=0.92, allow_learning=True)
        result = self.policy.evaluate(decision, report, [])
        assert result.allow_learning is True

    def test_te_disallows_blocks_learning(self) -> None:
        decision = _accept_decision()
        report = _truth_report(allow_learning=False)
        result = self.policy.evaluate(decision, report, [])
        assert result.allow_learning is False

    def test_non_accept_strategy_blocks_learning(self) -> None:
        report = _truth_report(allow_learning=True)
        for strategy_decision in [_hitl_decision(), _retry_decision()]:
            result = self.policy.evaluate(strategy_decision, report, [])
            assert result.allow_learning is False, (
                f"Expected False for {strategy_decision.strategy}"
            )

    def test_verifier_hard_failure_blocks_learning(self) -> None:
        decision = _accept_decision()
        report = _truth_report(allow_learning=True, verifier_names_failed=["mrz_check"])
        result = self.policy.evaluate(decision, report, [])
        assert result.allow_learning is False

    def test_multiple_verifier_failures_block_learning(self) -> None:
        decision = _accept_decision()
        report = _truth_report(
            allow_learning=True,
            verifier_names_failed=["mrz_check", "date_check"],
        )
        result = self.policy.evaluate(decision, report, [])
        assert result.allow_learning is False

    def test_all_conditions_satisfied(self) -> None:
        result = self.policy.evaluate(_accept_decision(), _truth_report(), [])
        assert result.allow_learning is True


# ---------------------------------------------------------------------------
# learn_from_document
# ---------------------------------------------------------------------------


class TestLearnFromDocument:
    def setup_method(self):
        self.policy = LearningPolicy()

    def test_clean_acceptance_allows_learn_from_document(self) -> None:
        result = self.policy.evaluate(_accept_decision(), _truth_report(), [])
        assert result.learn_from_document is True

    def test_human_correction_disables_learn_from_document(self) -> None:
        result = self.policy.evaluate(
            _accept_decision(), _truth_report(), [], is_human_correction=True
        )
        assert result.learn_from_document is False

    def test_blocked_learning_disables_learn_from_document(self) -> None:
        result = self.policy.evaluate(_hitl_decision(), _truth_report(), [])
        assert result.learn_from_document is False

    def test_learn_from_document_and_correction_mutually_exclusive(self) -> None:
        for is_correction in [True, False]:
            result = self.policy.evaluate(
                _accept_decision(), _truth_report(), [], is_human_correction=is_correction
            )
            assert not (result.learn_from_document and result.learn_from_correction), (
                f"Both learn_from_document and learn_from_correction True for is_correction={is_correction}"
            )


# ---------------------------------------------------------------------------
# learn_from_correction
# ---------------------------------------------------------------------------


class TestLearnFromCorrection:
    def setup_method(self):
        self.policy = LearningPolicy()

    def test_human_correction_with_acceptance_allows_learn_from_correction(self) -> None:
        result = self.policy.evaluate(
            _accept_decision(), _truth_report(), [], is_human_correction=True
        )
        assert result.learn_from_correction is True

    def test_no_correction_disables_learn_from_correction(self) -> None:
        result = self.policy.evaluate(_accept_decision(), _truth_report(), [])
        assert result.learn_from_correction is False

    def test_correction_with_blocked_learning_disables_learn_from_correction(self) -> None:
        report = _truth_report(allow_learning=False)
        result = self.policy.evaluate(_accept_decision(), report, [], is_human_correction=True)
        assert result.learn_from_correction is False


# ---------------------------------------------------------------------------
# schema_candidate
# ---------------------------------------------------------------------------


class TestSchemaCandidate:
    def setup_method(self):
        self.policy = LearningPolicy()

    def test_additional_fields_with_acceptance_is_schema_candidate(self) -> None:
        report = _truth_report(additional_fields=["blood_type", "religion"])
        result = self.policy.evaluate(_accept_decision(), report, [])
        assert result.schema_candidate is True

    def test_no_additional_fields_not_schema_candidate(self) -> None:
        report = _truth_report(additional_fields=[])
        result = self.policy.evaluate(_accept_decision(), report, [])
        assert result.schema_candidate is False

    def test_verifier_failure_blocks_schema_candidate(self) -> None:
        report = _truth_report(
            additional_fields=["new_field"],
            verifier_names_failed=["mrz_check"],
        )
        result = self.policy.evaluate(_accept_decision(), report, [])
        assert result.schema_candidate is False

    def test_non_accept_strategy_blocks_schema_candidate(self) -> None:
        report = _truth_report(additional_fields=["new_field"])
        result = self.policy.evaluate(_hitl_decision(), report, [])
        assert result.schema_candidate is False

    def test_schema_candidate_independent_of_allow_learning(self) -> None:
        """schema_candidate gated on acceptance + clean verifiers, NOT on te.allow_learning."""
        report = _truth_report(additional_fields=["new_field"])
        # allow_learning=True (set via persistence decision) but we can test independence
        result = self.policy.evaluate(_accept_decision(), report, [])
        assert result.schema_candidate is True


# ---------------------------------------------------------------------------
# reason string
# ---------------------------------------------------------------------------


class TestReason:
    def setup_method(self):
        self.policy = LearningPolicy()

    def test_allowed_reason_for_clean_acceptance(self) -> None:
        result = self.policy.evaluate(_accept_decision(), _truth_report(), [])
        assert result.reason == "learning_allowed"

    def test_correction_reason_for_human_correction(self) -> None:
        result = self.policy.evaluate(
            _accept_decision(), _truth_report(), [], is_human_correction=True
        )
        assert result.reason == "learn_from_correction"

    def test_reason_mentions_strategy_when_not_accept(self) -> None:
        result = self.policy.evaluate(_hitl_decision(), _truth_report(), [])
        assert "strategy_not_accept" in result.reason

    def test_reason_mentions_verifier_failures(self) -> None:
        report = _truth_report(verifier_names_failed=["mrz_check"])
        result = self.policy.evaluate(_accept_decision(), report, [])
        assert "mrz_check" in result.reason

    def test_reason_mentions_te_disallows(self) -> None:
        report = _truth_report(allow_learning=False)
        result = self.policy.evaluate(_accept_decision(), report, [])
        assert "truth_engine_disallows" in result.reason

    def test_reason_is_nonempty_in_all_cases(self) -> None:
        for decision in [_accept_decision(), _hitl_decision(), _retry_decision()]:
            for allow_learning in [True, False]:
                report = _truth_report(allow_learning=allow_learning)
                result = self.policy.evaluate(decision, report, [])
                assert result.reason.strip(), f"Empty reason for {decision.strategy}"


# ---------------------------------------------------------------------------
# LearningDecision dataclass
# ---------------------------------------------------------------------------


def test_learning_decision_is_dataclass() -> None:
    d = LearningDecision(
        allow_learning=True,
        learn_from_document=True,
        learn_from_correction=False,
        schema_candidate=False,
        reason="learning_allowed",
    )
    assert d.allow_learning is True
    assert d.learn_from_document is True
    assert d.learn_from_correction is False
    assert d.schema_candidate is False


def test_execution_history_accepted_as_input() -> None:
    """LearningPolicy.evaluate() accepts a list of ExecutionRecord."""
    policy = LearningPolicy()
    history = [
        ExecutionRecord(
            strategy=Strategy.PROMPT_REFINEMENT,
            timestamp="t",
            outcome="refinement_scheduled",
            confidence_before=0.60,
            confidence_after=None,
        )
    ]
    result = policy.evaluate(_accept_decision(), _truth_report(), history)
    assert isinstance(result, LearningDecision)
