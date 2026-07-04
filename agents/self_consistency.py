from collections import Counter

from agents.base import AgentResult

# Confidence band that triggers self-consistency voting.
SC_LOW = 0.6
SC_HIGH = 0.85


def should_vote(confidence: float) -> bool:
    return SC_LOW <= confidence < SC_HIGH


def vote(results: list[AgentResult]) -> AgentResult:
    """Per-field mode vote across results; fall back to highest-confidence sample on ties."""
    if not results:
        raise ValueError("vote() requires at least one result")
    if len(results) == 1:
        return results[0]

    all_keys = {k for r in results for k in r.data}
    voted_data: dict = {}

    for key in all_keys:
        values = [r.data.get(key) for r in results]
        hashable = []
        has_unhashable = False
        for v in values:
            try:
                hash(v)
                hashable.append(v)
            except TypeError:
                has_unhashable = True
                break

        if has_unhashable:
            # Arrays/dicts: fall back to highest-confidence sample's value
            best = max(results, key=lambda r: r.confidence)
            voted_data[key] = best.data.get(key)
            continue

        counts = Counter(hashable)
        majority = len(results) // 2 + 1
        winner, top_count = counts.most_common(1)[0]
        if top_count >= majority:
            voted_data[key] = winner
        else:
            # Tie: use highest-confidence sample
            best = max(results, key=lambda r: r.confidence)
            voted_data[key] = best.data.get(key)

    best_result = max(results, key=lambda r: r.confidence)
    return AgentResult(
        success=True,
        confidence=best_result.confidence,
        data=voted_data,
        tool_calls_made=sum(r.tool_calls_made for r in results),
        verification_passed=best_result.verification_passed,
    )
