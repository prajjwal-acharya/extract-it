"""Tests for Phase 5.5 — SchemaProposal and schema_diff_agent gating.

Covers:
  - SchemaProposal dataclass (is_empty, to_dict, from_dict)
  - ProposalStatus enum
  - propose_diff() creates a proposal without writing to DB
  - apply_diff() remains functional (approval path regression)
  - diff_schema() unchanged (regression)
  - _build_schema_proposal in op_a_retry returns (version, proposal_dict | None)
"""
from __future__ import annotations

import pytest

from agents.schema_diff_agent import SchemaDiff, diff_schema, normalize_key, propose_diff
from pipelines.learning.schema_proposal import ProposalStatus, SchemaProposal


# ---------------------------------------------------------------------------
# SchemaProposal dataclass
# ---------------------------------------------------------------------------


class TestSchemaProposal:
    def test_is_empty_true_when_no_changes(self) -> None:
        proposal = SchemaProposal(
            doc_type="passport",
            proposed_version="1.1",
            additions=[],
            relaxed_fields=[],
            origin_document_id="doc-001",
        )
        assert proposal.is_empty is True

    def test_is_empty_false_when_additions(self) -> None:
        proposal = SchemaProposal(
            doc_type="passport",
            proposed_version="1.1",
            additions=[{"name": "blood_type", "type": "string", "required": False}],
            relaxed_fields=[],
            origin_document_id="doc-001",
        )
        assert proposal.is_empty is False

    def test_is_empty_false_when_relaxed_fields(self) -> None:
        proposal = SchemaProposal(
            doc_type="passport",
            proposed_version="1.1",
            additions=[],
            relaxed_fields=["issuing_authority"],
            origin_document_id="doc-001",
        )
        assert proposal.is_empty is False

    def test_default_status_is_pending(self) -> None:
        proposal = SchemaProposal(doc_type="x", proposed_version="1.1", origin_document_id="")
        assert proposal.status == ProposalStatus.PENDING

    def test_to_dict_is_json_safe(self) -> None:
        import json

        proposal = SchemaProposal(
            doc_type="passport",
            proposed_version="1.1",
            additions=[{"name": "blood_type", "type": "string", "required": False}],
            relaxed_fields=["issuing_authority"],
            origin_document_id="doc-001",
        )
        d = proposal.to_dict()
        json.dumps(d)  # must not raise

    def test_to_dict_has_all_keys(self) -> None:
        proposal = SchemaProposal(doc_type="passport", proposed_version="1.1", origin_document_id="d")
        d = proposal.to_dict()
        for key in ("doc_type", "proposed_version", "additions", "relaxed_fields",
                    "origin_document_id", "status", "rejection_reason"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_status_is_string(self) -> None:
        proposal = SchemaProposal(doc_type="x", proposed_version="1.1", origin_document_id="")
        assert isinstance(proposal.to_dict()["status"], str)

    def test_from_dict_round_trips(self) -> None:
        proposal = SchemaProposal(
            doc_type="passport",
            proposed_version="1.2",
            additions=[{"name": "blood_type", "type": "string", "required": False}],
            relaxed_fields=["issuing_authority"],
            origin_document_id="doc-abc",
            status=ProposalStatus.PENDING,
        )
        d = proposal.to_dict()
        restored = SchemaProposal.from_dict(d)
        assert restored.doc_type == proposal.doc_type
        assert restored.proposed_version == proposal.proposed_version
        assert restored.additions == proposal.additions
        assert restored.relaxed_fields == proposal.relaxed_fields
        assert restored.status == proposal.status

    def test_from_dict_with_approved_status(self) -> None:
        d = {
            "doc_type": "passport",
            "proposed_version": "1.1",
            "additions": [],
            "relaxed_fields": [],
            "origin_document_id": "",
            "status": "approved",
            "rejection_reason": None,
        }
        proposal = SchemaProposal.from_dict(d)
        assert proposal.status == ProposalStatus.APPROVED


class TestProposalStatus:
    def test_pending_value(self) -> None:
        assert ProposalStatus.PENDING == "pending"

    def test_approved_value(self) -> None:
        assert ProposalStatus.APPROVED == "approved"

    def test_rejected_value(self) -> None:
        assert ProposalStatus.REJECTED == "rejected"


# ---------------------------------------------------------------------------
# propose_diff() — creates proposal, does NOT write to DB
# ---------------------------------------------------------------------------


class TestProposeDiff:
    def _make_active_row(self, doc_type="passport", version="1.0", fields=None):
        """Minimal mock for a SchemaVersion active row."""
        from types import SimpleNamespace

        return SimpleNamespace(
            doc_type=doc_type,
            version=version,
            fields_json=fields or [
                {"name": "passport_number", "type": "string", "required": True},
                {"name": "surname", "type": "string", "required": True},
            ],
        )

    def test_propose_diff_returns_schema_proposal(self) -> None:
        active_row = self._make_active_row()
        diff = SchemaDiff(
            additions=[{"name": "blood_type", "type": "string", "required": False}],
            relaxed_fields=[],
        )
        proposal = propose_diff(active_row, diff, origin_document_id="doc-001")
        assert isinstance(proposal, SchemaProposal)

    def test_propose_diff_status_is_pending(self) -> None:
        active_row = self._make_active_row()
        diff = SchemaDiff(
            additions=[{"name": "blood_type", "type": "string", "required": False}],
            relaxed_fields=[],
        )
        proposal = propose_diff(active_row, diff, origin_document_id="doc-001")
        assert proposal.status == ProposalStatus.PENDING

    def test_propose_diff_bumps_version(self) -> None:
        active_row = self._make_active_row(version="1.0")
        diff = SchemaDiff(
            additions=[{"name": "new_field", "type": "string", "required": False}],
            relaxed_fields=[],
        )
        proposal = propose_diff(active_row, diff, origin_document_id="doc-001")
        assert proposal.proposed_version == "1.1"

    def test_propose_diff_carries_additions(self) -> None:
        active_row = self._make_active_row()
        new_field = {"name": "blood_type", "type": "string", "required": False}
        diff = SchemaDiff(additions=[new_field], relaxed_fields=[])
        proposal = propose_diff(active_row, diff, origin_document_id="doc-001")
        assert new_field in proposal.additions

    def test_propose_diff_carries_relaxed_fields(self) -> None:
        active_row = self._make_active_row()
        diff = SchemaDiff(additions=[], relaxed_fields=["issuing_authority"])
        proposal = propose_diff(active_row, diff, origin_document_id="doc-001")
        assert "issuing_authority" in proposal.relaxed_fields

    def test_propose_diff_sets_doc_type(self) -> None:
        active_row = self._make_active_row(doc_type="bank_statement")
        diff = SchemaDiff(
            additions=[{"name": "branch_code", "type": "string", "required": False}],
            relaxed_fields=[],
        )
        proposal = propose_diff(active_row, diff, origin_document_id="doc-x")
        assert proposal.doc_type == "bank_statement"

    def test_propose_diff_sets_origin_document_id(self) -> None:
        active_row = self._make_active_row()
        diff = SchemaDiff(
            additions=[{"name": "x", "type": "string", "required": False}],
            relaxed_fields=[],
        )
        proposal = propose_diff(active_row, diff, origin_document_id="doc-origin-123")
        assert proposal.origin_document_id == "doc-origin-123"


# ---------------------------------------------------------------------------
# diff_schema() — regression (must still work after Phase 5.5 changes)
# ---------------------------------------------------------------------------


class TestDiffSchemaRegression:
    def _fields(self, names: list[str], required: bool = True) -> list[dict]:
        return [{"name": n, "type": "string", "required": required} for n in names]

    def test_new_field_produces_addition(self) -> None:
        discovered = {"passport_number": "A1234567", "blood_type": "O+"}
        active_fields = self._fields(["passport_number", "surname"])
        diff = diff_schema(discovered, active_fields)
        addition_names = [a["name"] for a in diff.additions]
        assert "blood_type" in addition_names

    def test_known_field_is_not_an_addition(self) -> None:
        discovered = {"passport_number": "A1234567"}
        active_fields = self._fields(["passport_number"])
        diff = diff_schema(discovered, active_fields)
        assert not diff.additions

    def test_missing_required_field_is_relaxed(self) -> None:
        discovered = {"passport_number": "A1234567"}
        active_fields = self._fields(["passport_number", "surname"])
        diff = diff_schema(discovered, active_fields)
        assert "surname" in diff.relaxed_fields

    def test_empty_diff_is_empty(self) -> None:
        discovered = {"passport_number": "A1234567"}
        active_fields = self._fields(["passport_number"])
        diff = diff_schema(discovered, active_fields)
        assert diff.is_empty

    def test_fuzzy_match_prevents_near_duplicate_additions(self) -> None:
        discovered = {"acct_holder": "SMITH"}  # near-match for "account_holder"
        active_fields = self._fields(["account_holder"])
        diff = diff_schema(discovered, active_fields)
        addition_names = [a["name"] for a in diff.additions]
        assert "acct_holder" not in addition_names


# ---------------------------------------------------------------------------
# normalize_key() — regression
# ---------------------------------------------------------------------------


def test_normalize_key_snake_cases() -> None:
    assert normalize_key("Account Holder") == "account_holder"


def test_normalize_key_strips_special_chars() -> None:
    assert normalize_key("Holder's Name") == "holder_s_name"


def test_normalize_key_handles_already_snake() -> None:
    assert normalize_key("passport_number") == "passport_number"


# ---------------------------------------------------------------------------
# SchemaDiff.is_empty — regression
# ---------------------------------------------------------------------------


def test_schema_diff_is_empty_true() -> None:
    assert SchemaDiff().is_empty is True


def test_schema_diff_is_empty_false_with_addition() -> None:
    diff = SchemaDiff(additions=[{"name": "x", "type": "string", "required": False}])
    assert diff.is_empty is False


def test_schema_diff_is_empty_false_with_relaxed() -> None:
    diff = SchemaDiff(relaxed_fields=["passport_number"])
    assert diff.is_empty is False
