import unittest.mock as mock

from agents.schema_diff_agent import (
    SchemaDiff,
    _bump_version,
    apply_diff,
    diff_schema,
    discover_fields,
    normalize_key,
)


# ---------------------------------------------------------------------------
# normalize_key
# ---------------------------------------------------------------------------


def test_normalize_key_snake_cases_mixed_input() -> None:
    assert normalize_key("Account Holder Name") == "account_holder_name"


def test_normalize_key_strips_non_alphanumeric() -> None:
    assert normalize_key("date-of-birth!") == "date_of_birth"


# ---------------------------------------------------------------------------
# diff_schema
# ---------------------------------------------------------------------------

_SCALAR_FIELDS = [
    {"name": "account_holder", "type": "string", "required": True},
    {"name": "account_number", "type": "string", "required": True},
    {"name": "bank_name", "type": "string", "required": True},
]

_ARRAY_FIELD = {
    "name": "transactions",
    "type": "array",
    "required": False,
    "items": [
        {"name": "date", "type": "date"},
        {"name": "description", "type": "string"},
    ],
}


def test_diff_schema_matches_fuzzy_variant_as_no_addition() -> None:
    # "Acct Holder" normalises to "acct_holder" — fuzzy-close to "account_holder"
    discovered = {"Acct Holder": "Jane Doe", "account_number": "12345", "bank_name": "HDFC"}
    result = diff_schema(discovered, _SCALAR_FIELDS)
    assert result.additions == []


def test_diff_schema_flags_new_field_as_addition() -> None:
    discovered = {
        "account_holder": "Jane Doe",
        "account_number": "12345",
        "bank_name": "HDFC",
        "IFSC Code": "HDFC0001234",
    }
    result = diff_schema(discovered, _SCALAR_FIELDS)
    added_names = [a["name"] for a in result.additions]
    assert "ifsc_code" in added_names


def test_diff_schema_flags_missing_required_field_as_relaxed() -> None:
    # Provide all fields except bank_name
    discovered = {"account_holder": "Jane", "account_number": "99"}
    result = diff_schema(discovered, _SCALAR_FIELDS)
    assert "bank_name" in result.relaxed_fields


def test_diff_schema_skips_array_type_fields_from_relaxation() -> None:
    # transactions is array-type and absent from discovery — must NOT appear in relaxed_fields
    fields = _SCALAR_FIELDS + [_ARRAY_FIELD]
    discovered = {"account_holder": "Jane", "account_number": "99", "bank_name": "SBI"}
    result = diff_schema(discovered, fields)
    assert "transactions" not in result.relaxed_fields


def test_diff_schema_empty_discovered_relaxes_all_required() -> None:
    result = diff_schema({}, _SCALAR_FIELDS)
    assert set(result.relaxed_fields) == {"account_holder", "account_number", "bank_name"}
    assert result.is_empty is False


def test_schema_diff_is_empty_true_when_no_changes() -> None:
    assert SchemaDiff().is_empty is True


# ---------------------------------------------------------------------------
# _bump_version
# ---------------------------------------------------------------------------


def test_bump_version_increments_minor() -> None:
    assert _bump_version("1.0") == "1.1"


def test_bump_version_handles_unexpected_format() -> None:
    result = _bump_version("v1-alpha")
    assert result.endswith(".next")
    # must not raise


# ---------------------------------------------------------------------------
# discover_fields
# ---------------------------------------------------------------------------


def test_discover_fields_parses_valid_json() -> None:
    payload = '{"account_holder": "Jane Doe", "bank_name": "HDFC"}'
    with mock.patch("agents.schema_diff_agent.generate", return_value=payload):
        result = discover_fields(b"bytes", "application/pdf")
    assert result == {"account_holder": "Jane Doe", "bank_name": "HDFC"}


def test_discover_fields_strips_markdown_fences() -> None:
    payload = '```json\n{"field_a": "val_a"}\n```'
    with mock.patch("agents.schema_diff_agent.generate", return_value=payload):
        result = discover_fields(b"bytes", "application/pdf")
    assert result == {"field_a": "val_a"}


def test_discover_fields_returns_empty_dict_on_malformed_json() -> None:
    with mock.patch("agents.schema_diff_agent.generate", return_value="not json at all"):
        result = discover_fields(b"bytes", "application/pdf")
    assert result == {}


# ---------------------------------------------------------------------------
# apply_diff
# ---------------------------------------------------------------------------


def _make_active_row(fields_json: list[dict] | None = None) -> mock.MagicMock:
    row = mock.MagicMock()
    row.doc_type = "bank_statement"
    row.version = "1.0"
    row.fields_json = fields_json if fields_json is not None else list(_SCALAR_FIELDS)
    row.universal_mapping_json = {}
    row.is_active = True
    return row


def test_apply_diff_deactivates_old_row_and_activates_new() -> None:
    session = mock.MagicMock()
    active_row = _make_active_row()
    diff = SchemaDiff(additions=[{"name": "ifsc_code", "type": "string", "required": False}])

    # session.refresh is a no-op MagicMock — new_version is the SchemaVersion constructor result;
    # capture it from session.add calls.
    added_objects: list = []
    session.add.side_effect = lambda obj: added_objects.append(obj)

    apply_diff(session, active_row, diff, origin_document_id="doc-1")

    assert active_row.is_active is False
    # flush must be called at least once before the second session.add
    session.flush.assert_called()
    session.commit.assert_called_once()


def test_apply_diff_merges_additions_without_duplicates() -> None:
    session = mock.MagicMock()
    # active already has "account_holder"
    active_row = _make_active_row()
    # addition with name that already exists — must not duplicate
    diff = SchemaDiff(additions=[{"name": "account_holder", "type": "string", "required": False}])

    apply_diff(session, active_row, diff, origin_document_id="doc-1")

    # session.refresh was called on the new SchemaVersion object
    refreshed = session.refresh.call_args[0][0]
    names = [f["name"] for f in refreshed.fields_json if f.get("type") != "array"]
    assert names.count("account_holder") == 1


def test_apply_diff_marks_relaxed_fields_not_required() -> None:
    session = mock.MagicMock()
    active_row = _make_active_row()
    diff = SchemaDiff(relaxed_fields=["bank_name"])

    apply_diff(session, active_row, diff, origin_document_id="doc-1")

    refreshed = session.refresh.call_args[0][0]
    bank_name_field = next(f for f in refreshed.fields_json if f["name"] == "bank_name")
    assert bank_name_field["required"] is False


def test_apply_diff_new_version_source_is_auto_discovered() -> None:
    session = mock.MagicMock()
    active_row = _make_active_row()
    diff = SchemaDiff(additions=[{"name": "ifsc_code", "type": "string", "required": False}])

    apply_diff(session, active_row, diff, origin_document_id="doc-42")

    refreshed = session.refresh.call_args[0][0]
    assert refreshed.source == "auto_discovered"
    assert refreshed.origin_document_id == "doc-42"
