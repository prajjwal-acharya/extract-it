from __future__ import annotations

from dataclasses import dataclass

from pipelines.truth_engine.models import VerificationReport


@dataclass(frozen=True)
class ConfidenceFusionPolicy:
    """Deterministic policy that fuses multiple confidence signals into one score.

    The graph never computes final_confidence directly — it always delegates here.
    Weights must sum to 1.0 (classify + extraction + coverage).

    Verification acts as a modifier, not a weight:
    - Any failure caps the score at verification_failure_cap.
    - All passes add a small verification_pass_bonus.
    """

    classify_weight: float = 0.20
    extraction_weight: float = 0.50
    coverage_weight: float = 0.30
    verification_failure_cap: float = 0.70
    verification_pass_bonus: float = 0.05

    def fuse(
        self,
        classify_confidence: float,
        extraction_confidence: float,
        coverage_score: float,
        verification_reports: list[VerificationReport],
    ) -> tuple[float, str]:
        """Return (final_confidence, decision_reason).

        decision_reason is a human-readable trace of every signal that
        contributed to the score — directly usable in TruthReport.decision_reason.
        """
        base = (
            self.classify_weight * classify_confidence
            + self.extraction_weight * extraction_confidence
            + self.coverage_weight * coverage_score
        )

        parts = [
            f"classify={classify_confidence:.2f}(w={self.classify_weight})",
            f"extraction={extraction_confidence:.2f}(w={self.extraction_weight})",
            f"coverage={coverage_score:.2f}(w={self.coverage_weight})",
            f"base={base:.3f}",
        ]

        failed = [r for r in verification_reports if r.passed is False]
        passed = [r for r in verification_reports if r.passed is True]

        if failed:
            base = min(base, self.verification_failure_cap)
            parts.append(
                f"capped_by_failures=[{','.join(r.verifier_name for r in failed)}]"
                f"→cap={self.verification_failure_cap}"
            )
        elif passed:
            base = min(1.0, base + self.verification_pass_bonus)
            parts.append(
                f"bonus_for_passes=[{','.join(r.verifier_name for r in passed)}]"
                f"+{self.verification_pass_bonus}"
            )

        final = round(min(1.0, max(0.0, base)), 4)
        parts.append(f"final={final}")
        return final, " ".join(parts)
