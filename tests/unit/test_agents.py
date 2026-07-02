def test_classify_returns_agent_result() -> None:
    """classify() returns an AgentResult with a non-empty doc_type."""
    raise NotImplementedError


def test_classify_confidence_is_between_zero_and_one() -> None:
    """classify() returns confidence in [0, 1]."""
    raise NotImplementedError


def test_extract_returns_agent_result_for_passport() -> None:
    """extract() with doc_type='passport' returns all required schema fields."""
    raise NotImplementedError


def test_extract_returns_failure_for_unknown_doc_type() -> None:
    """extract() returns success=False when no schema file exists for the doc_type."""
    raise NotImplementedError


def test_validate_returns_issues_for_invalid_fields() -> None:
    """validate() populates validation_issues when required fields are missing."""
    raise NotImplementedError


def test_validate_meets_threshold_true_above_threshold() -> None:
    """meets_threshold() returns True when confidence >= CONFIDENCE_THRESHOLD."""
    raise NotImplementedError


def test_generate_returns_string() -> None:
    """generate() returns a non-empty string for a simple prompt."""
    raise NotImplementedError
