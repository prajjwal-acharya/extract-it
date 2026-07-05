"""BETTER_RETRIEVAL strategy — evidence-driven adaptive RAG query construction.

Builds targeted retrieval query strings from TruthReport evidence via directives
rather than embedding raw extracted_fields JSON. The queries are stored in state
and consumed by op_a_retry_node, which holds the existing DB session logic.
"""

from __future__ import annotations

from pipelines.resolution.directives import Directive, DirectiveEngine
from pipelines.truth_engine.models import TruthReport

_engine = DirectiveEngine()


class BetterRetrievalStrategy:
    """Builds targeted retrieval queries from TruthReport evidence.

    The strategy does NOT execute the DB query — that stays in op_a_retry_node,
    which already holds the session. Instead, it returns a list of query strings
    that op_a_retry_node uses to embed and search, plus the list of directives
    and a retrieval metadata dict for recording in ExecutionRecord.
    """

    def build_queries(
        self,
        truth_report: TruthReport,
        doc_type: str,
    ) -> tuple[list[str], list[Directive], dict]:
        """Compute targeted query strings and retrieval metadata.

        Returns:
          queries        — ordered query strings for similarity_search
          directives     — directive labels that drove the queries
          metadata       — dict for ExecutionRecord.strategy_metadata
        """
        directives = _engine.generate(truth_report)
        queries = _engine.to_retrieval_queries(directives, doc_type)

        # Build retrieval metadata for analytics
        metadata: dict = {
            "directives": [d.value for d in directives],
            "query_count": len(queries),
            "missing_fields": truth_report.field_validation.required_fields_missing,
            "failed_verifiers": [
                r.verifier_name for r in truth_report.verification_reports if r.passed is False
            ],
        }
        return queries, directives, metadata
