"""Tests for setting specs.

Grouped by the four uses a spec has: parsing it, validating a value against it,
turning it into JSON Schema, and handing it to a form.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from celeris_bridge.domain.agent.field_spec import (
    FIELD_TYPES,
    FieldSpec,
    InvalidSettingValue,
    ItemFieldSpec,
    is_valid_setting_key,
    option_values,
    public_field_spec,
    to_json_schema,
    validate_value,
)


def spec(**overrides: object) -> FieldSpec:
    """A minimal valid spec, with the fields under test overridden."""
    return FieldSpec.model_validate({"label": "Example", "type": "string", "default": "", **overrides})


# --------------------------------------------------------------------------
# Parsing a spec
# --------------------------------------------------------------------------


def test_all_nine_types_are_declared() -> None:
    assert FIELD_TYPES == (
        "string",
        "enum",
        "multiselect",
        "boolean",
        "number",
        "url",
        "color",
        "stringList",
        "list",
    )


def test_camel_case_json_is_accepted_and_round_trips() -> None:
    parsed = spec(maxLength=40, minLength=2, default="ok")
    assert parsed.max_length == 40
    assert parsed.min_length == 2
    assert parsed.model_dump(by_alias=True)["maxLength"] == 40


def test_min_and_max_keep_their_json_names() -> None:
    parsed = spec(type="number", default=5, min=1, max=10)
    assert (parsed.minimum, parsed.maximum) == (1, 10)
    dumped = parsed.model_dump(by_alias=True, exclude_none=True)
    assert dumped["min"] == 1 and dumped["max"] == 10


def test_unknown_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        spec(maxLenght=40)  # codespell:ignore


def test_bare_string_options_expand_to_value_and_label() -> None:
    parsed = spec(type="enum", default="friendly", options=["friendly", "formal"])
    assert option_values(parsed) == ["friendly", "formal"]
    assert parsed.options is not None
    assert parsed.options[0].label == "friendly"


def test_mixed_bare_and_full_options_are_both_accepted() -> None:
    parsed = spec(
        type="enum",
        default="a",
        options=["a", {"value": "b", "label": "Bee"}],
    )
    assert option_values(parsed) == ["a", "b"]


@pytest.mark.parametrize("field_type", ["enum", "multiselect"])
def test_choice_types_need_options(field_type: str) -> None:
    with pytest.raises(ValidationError, match="need options"):
        spec(type=field_type, default=[] if field_type == "multiselect" else "a")


def test_duplicate_option_values_are_rejected() -> None:
    with pytest.raises(ValidationError, match="different value"):
        spec(type="enum", default="a", options=["a", "a"])


def test_constraint_that_does_not_fit_the_type_is_rejected() -> None:
    """A boolean has no length. The reference implementation ignored this."""
    with pytest.raises(ValidationError, match='"boolean" settings do not take maxLength'):
        spec(type="boolean", default=True, maxLength=5)


def test_number_constraints_are_rejected_on_a_string() -> None:
    with pytest.raises(ValidationError, match="do not take"):
        spec(min=1, max=4)


def test_contradictory_bounds_are_rejected() -> None:
    with pytest.raises(ValidationError, match="minLength is above maxLength"):
        spec(minLength=10, maxLength=2)
    with pytest.raises(ValidationError, match="min is above max"):
        spec(type="number", default=0, min=10, max=2)


def test_pattern_must_compile() -> None:
    with pytest.raises(ValidationError, match="valid regular expression"):
        spec(pattern="[unterminated")


def test_default_is_required() -> None:
    with pytest.raises(ValidationError, match="default"):
        FieldSpec.model_validate({"label": "Example", "type": "string"})


def test_default_is_validated_against_its_own_spec() -> None:
    with pytest.raises(ValidationError, match="default must be one of"):
        spec(type="enum", default="nope", options=["a", "b"])


def test_default_is_stored_normalised() -> None:
    """So an override equal to the default is recognisable as such."""
    assert spec(default="  hello  ").default == "hello"
    assert spec(type="color", default="#0D8F7C").default == "#0d8f7c"


def test_null_default_requires_nullable() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        spec(default=None)
    assert spec(default=None, nullable=True).default is None


def test_list_needs_an_item_shape() -> None:
    with pytest.raises(ValidationError, match="need an item shape"):
        spec(type="list", default=[])


def test_unique_by_must_name_an_item_field() -> None:
    with pytest.raises(ValidationError, match='uniqueBy "slug" is not an item field'):
        spec(
            type="list",
            default=[],
            item={"key": {"label": "Key", "type": "string"}},
            uniqueBy="slug",
        )


def test_list_items_may_not_nest_a_list() -> None:
    with pytest.raises(ValidationError):
        spec(
            type="list",
            default=[],
            item={"inner": {"label": "Inner", "type": "list"}},
        )


def test_item_field_takes_no_top_level_only_keys() -> None:
    """An item has no default, group or editable flag of its own."""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ItemFieldSpec.model_validate({"label": "Key", "type": "string", "default": "x"})


def test_setting_keys_are_identifiers() -> None:
    assert is_valid_setting_key("companySummary")
    assert is_valid_setting_key("a")
    assert not is_valid_setting_key("company-summary")
    assert not is_valid_setting_key("2fast")
    assert not is_valid_setting_key("")
    assert not is_valid_setting_key("a" * 65)


# --------------------------------------------------------------------------
# Validating a value
# --------------------------------------------------------------------------


def test_string_is_trimmed_and_bounded() -> None:
    assert validate_value(spec(maxLength=5), "  hi  ") == "hi"
    with pytest.raises(InvalidSettingValue, match="at most 5 characters"):
        validate_value(spec(maxLength=5), "far too long")
    with pytest.raises(InvalidSettingValue, match="at least 3 characters"):
        validate_value(spec(minLength=3, default="abc"), "hi")


def test_string_falls_back_to_the_default_cap() -> None:
    validate_value(spec(), "x" * 500)
    with pytest.raises(InvalidSettingValue, match="at most 500 characters"):
        validate_value(spec(), "x" * 501)


def test_string_pattern_is_unanchored_like_json_schema() -> None:
    digits = spec(pattern=r"\d{4}", default="2026")
    assert validate_value(digits, "year 2026 ok") == "year 2026 ok"
    with pytest.raises(InvalidSettingValue, match="expected format"):
        validate_value(digits, "no digits")


def test_enum_rejects_an_unknown_value() -> None:
    tone = spec(type="enum", default="friendly", options=["friendly", "formal"])
    assert validate_value(tone, "formal") == "formal"
    with pytest.raises(InvalidSettingValue, match="must be one of: friendly, formal"):
        validate_value(tone, "sarcastic")


def test_multiselect_rejects_duplicates_and_respects_max_items() -> None:
    channels = spec(
        type="multiselect",
        default=[],
        options=["web", "email", "voice"],
        maxItems=2,
    )
    assert validate_value(channels, ["web", "email"]) == ["web", "email"]
    with pytest.raises(InvalidSettingValue, match='"web" is listed twice'):
        validate_value(channels, ["web", "web"])
    with pytest.raises(InvalidSettingValue, match="at most 2 values"):
        validate_value(channels, ["web", "email", "voice"])


def test_boolean_does_not_accept_truthy_values() -> None:
    flag = spec(type="boolean", default=False)
    assert validate_value(flag, True) is True
    for sneaky in (1, 0, "true", "yes"):
        with pytest.raises(InvalidSettingValue, match="true or false"):
            validate_value(flag, sneaky)


def test_number_rejects_booleans() -> None:
    """bool is a subclass of int in Python, so this needs saying."""
    with pytest.raises(InvalidSettingValue, match="must be a number"):
        validate_value(spec(type="number", default=0), True)


def test_number_bounds_and_integer_coercion() -> None:
    turns = spec(type="number", default=20, min=1, max=100, integer=True)
    assert validate_value(turns, 40) == 40
    assert validate_value(turns, 40.0) == 40
    assert isinstance(validate_value(turns, 40.0), int)
    with pytest.raises(InvalidSettingValue, match="whole number"):
        validate_value(turns, 40.5)
    with pytest.raises(InvalidSettingValue, match="at least 1"):
        validate_value(turns, 0)
    with pytest.raises(InvalidSettingValue, match="at most 100"):
        validate_value(turns, 101)


def test_number_bounds_read_naturally_when_whole() -> None:
    temperature = spec(type="number", default=0.3, min=0, max=1)
    with pytest.raises(InvalidSettingValue, match="at most 1$"):
        validate_value(temperature, 2)


def test_url_accepts_empty_as_not_set() -> None:
    link = spec(type="url", default="")
    assert validate_value(link, "") == ""
    assert validate_value(link, "  https://example.com/privacy  ") == "https://example.com/privacy"


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "ftp://example.com",
        "example.com",
        "https://",
        "file:///etc/passwd",
    ],
)
def test_url_rejects_anything_but_http(bad: str) -> None:
    with pytest.raises(InvalidSettingValue, match="http:// or https://"):
        validate_value(spec(type="url", default=""), bad)


def test_colour_is_lowercased() -> None:
    brand = spec(type="color", default="#0d8f7c")
    assert validate_value(brand, "#0D8F7C") == "#0d8f7c"
    for bad in ("#fff", "0d8f7c", "red", "#0d8f7cc"):
        with pytest.raises(InvalidSettingValue, match="hex colour"):
            validate_value(brand, bad)


def test_string_list_trims_entries_and_rejects_blanks() -> None:
    topics = spec(type="stringList", default=[], maxItems=3, maxLength=10)
    assert validate_value(topics, [" pricing ", "refunds"]) == ["pricing", "refunds"]
    with pytest.raises(InvalidSettingValue, match="item 2 must not be empty"):
        validate_value(topics, ["pricing", "   "])
    with pytest.raises(InvalidSettingValue, match="item 1 must be at most 10"):
        validate_value(topics, ["x" * 11])
    with pytest.raises(InvalidSettingValue, match="at most 3 entries"):
        validate_value(topics, ["a", "b", "c", "d"])


def qualification_fields() -> FieldSpec:
    return spec(
        type="list",
        default=[],
        maxItems=2,
        uniqueBy="key",
        item={
            "key": {"label": "Key", "type": "string", "maxLength": 40},
            "requiredToBook": {"label": "Required to book", "type": "boolean"},
        },
    )


def test_list_rows_are_validated_field_by_field() -> None:
    parsed = validate_value(
        qualification_fields(),
        [{"requiredToBook": True, "key": "  email  "}],
    )
    assert parsed == [{"key": "email", "requiredToBook": True}]


def test_list_row_keys_come_out_in_declared_order() -> None:
    """Two equal rows must serialise identically whatever order they arrived in."""
    rows = qualification_fields()
    one = validate_value(rows, [{"key": "email", "requiredToBook": True}])
    two = validate_value(rows, [{"requiredToBook": True, "key": "email"}])
    assert list(one[0]) == list(two[0]) == ["key", "requiredToBook"]


def test_list_row_must_have_exactly_the_declared_fields() -> None:
    rows = qualification_fields()
    with pytest.raises(InvalidSettingValue, match="is missing fields: requiredToBook"):
        validate_value(rows, [{"key": "email"}])
    with pytest.raises(InvalidSettingValue, match="has unknown fields: label"):
        validate_value(rows, [{"key": "email", "requiredToBook": True, "label": "Email"}])


def test_list_reports_which_row_and_field_failed() -> None:
    with pytest.raises(InvalidSettingValue, match="item 2.requiredToBook must be true or false"):
        validate_value(
            qualification_fields(),
            [
                {"key": "name", "requiredToBook": True},
                {"key": "email", "requiredToBook": "yes"},
            ],
        )


def test_list_unique_by_is_enforced() -> None:
    with pytest.raises(InvalidSettingValue, match='different "key" on every item'):
        validate_value(
            qualification_fields(),
            [
                {"key": "email", "requiredToBook": True},
                {"key": "email", "requiredToBook": False},
            ],
        )


def test_list_max_items_is_enforced() -> None:
    with pytest.raises(InvalidSettingValue, match="at most 2 items"):
        validate_value(
            qualification_fields(),
            [{"key": k, "requiredToBook": False} for k in ("a", "b", "c")],
        )


def test_list_row_must_be_an_object() -> None:
    with pytest.raises(InvalidSettingValue, match="item 1 must be an object"):
        validate_value(qualification_fields(), ["email"])


def test_nullable_accepts_none_everywhere_else_rejects_it() -> None:
    assert validate_value(spec(nullable=True, default=None), None) is None
    with pytest.raises(InvalidSettingValue, match="must not be empty"):
        validate_value(spec(), None)


def test_wrong_python_type_is_reported_not_coerced() -> None:
    with pytest.raises(InvalidSettingValue, match="must be text"):
        validate_value(spec(), 42)
    with pytest.raises(InvalidSettingValue, match="must be a list"):
        validate_value(spec(type="stringList", default=[]), "pricing")


# --------------------------------------------------------------------------
# JSON Schema, for ability inputs
# --------------------------------------------------------------------------


def test_string_schema_always_carries_a_cap() -> None:
    assert to_json_schema(spec()) == {"type": "string", "maxLength": 500}
    assert to_json_schema(spec(maxLength=80, minLength=1, default="x")) == {
        "type": "string",
        "maxLength": 80,
        "minLength": 1,
    }


def test_enum_schema_lists_the_values() -> None:
    assert to_json_schema(spec(type="enum", default="a", options=["a", "b"])) == {
        "type": "string",
        "enum": ["a", "b"],
    }


def test_number_schema_distinguishes_integer() -> None:
    assert to_json_schema(spec(type="number", default=1, integer=True, min=1, max=9)) == {
        "type": "integer",
        "minimum": 1,
        "maximum": 9,
    }
    assert to_json_schema(spec(type="number", default=0.3))["type"] == "number"


def test_description_is_passed_through_for_the_model_to_read() -> None:
    described = spec(description="The visitor's work email address.")
    assert to_json_schema(described)["description"] == "The visitor's work email address."


def test_nullable_schema_allows_null() -> None:
    schema = to_json_schema(spec(nullable=True, default=None))
    assert schema == {"anyOf": [{"type": "string", "maxLength": 500}, {"type": "null"}]}


def test_list_schema_is_a_closed_object_array() -> None:
    schema = to_json_schema(qualification_fields())
    assert schema["type"] == "array"
    assert schema["maxItems"] == 2
    assert schema["items"]["additionalProperties"] is False
    assert schema["items"]["required"] == ["key", "requiredToBook"]
    assert schema["items"]["properties"]["key"] == {"type": "string", "maxLength": 40}


def test_every_type_produces_a_schema() -> None:
    """A new field type must not be able to slip past to_json_schema."""
    samples = {
        "string": spec(),
        "enum": spec(type="enum", default="a", options=["a"]),
        "multiselect": spec(type="multiselect", default=[], options=["a"]),
        "boolean": spec(type="boolean", default=False),
        "number": spec(type="number", default=0),
        "url": spec(type="url", default=""),
        "color": spec(type="color", default="#000000"),
        "stringList": spec(type="stringList", default=[]),
        "list": qualification_fields(),
    }
    assert set(samples) == set(FIELD_TYPES)
    for sample in samples.values():
        assert to_json_schema(sample)


# --------------------------------------------------------------------------
# The shape the admin UI receives
# --------------------------------------------------------------------------


def test_public_spec_is_camel_case_with_the_key_first() -> None:
    public = public_field_spec(
        "companySummary",
        spec(
            label="Company summary",
            group="Company",
            editable=True,
            maxLength=600,
            multiline=True,
        ),
    )
    assert next(iter(public)) == "key"
    assert public == {
        "key": "companySummary",
        "label": "Company summary",
        "type": "string",
        "group": "Company",
        "editable": True,
        "adminOnly": False,
        "nullable": False,
        "maxLength": 600,
        "multiline": True,
        "default": "",
    }


def test_public_spec_omits_unset_constraints() -> None:
    public = public_field_spec("agentName", spec())
    assert "pattern" not in public
    assert "maxItems" not in public
    assert "minLength" not in public


def test_public_spec_keeps_a_null_default() -> None:
    public = public_field_spec("minTeamSize", spec(type="number", default=None, nullable=True))
    assert public["default"] is None


def test_public_spec_still_reports_admin_only_so_callers_can_filter() -> None:
    public = public_field_spec("riskTopics", spec(adminOnly=True))
    assert public["adminOnly"] is True
