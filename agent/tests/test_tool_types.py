"""Tests for :mod:`minimax_code.tool_types` (R65).

Fusion of grok-build's ``xai-tool-types`` — the tool-plane wire vocabulary
for tools, their arguments, and JSON-Schema argument schemas. Coverage:

* :class:`ArgumentType` lowercase wire fidelity + classification predicates.
* :class:`SchemaType` untagged-union round-trips (single/multiple/nullable).
* :class:`ToolArgument` builder chain + precise ``to_wire`` skip semantics.
* :class:`ToolDescription` validation + schema accessors.
* :func:`parse_arguments_from_schema_lossy` — the 15 grok schema fixtures
  (basic / enum / composite / primitives / required / null / nullable /
  multi-type / anyOf-ref / direct-ref / compact-enum / union-without-ref /
  no-defs / numeric bounds).
"""

from __future__ import annotations

from minimax_code.tool_types import (
    ArgumentType,
    SchemaType,
    ToolArgument,
    ToolDescription,
    ValidationError,
    ValidationErrors,
    parse_arguments_from_schema_lossy,
)

# ---------------------------------------------------------------------------
# ArgumentType — lowercase wire fidelity + classification
# ---------------------------------------------------------------------------


def test_argument_type_wire_values_are_lowercase():
    assert ArgumentType.STRING.value == "string"
    assert ArgumentType.INTEGER.value == "integer"
    assert ArgumentType.NUMBER.value == "number"
    assert ArgumentType.BOOLEAN.value == "boolean"
    assert ArgumentType.ARRAY.value == "array"
    assert ArgumentType.OBJECT.value == "object"
    assert ArgumentType.NULL.value == "null"


def test_argument_type_strenum_compares_equal_to_bare_string():
    # StrEnum: member == its value, so wire round-trips through json untouched.
    assert ArgumentType.STRING == "string"
    assert ArgumentType.NULL == "null"


def test_argument_type_from_schema_type_known_tags():
    assert ArgumentType.from_schema_type("string") is ArgumentType.STRING
    assert ArgumentType.from_schema_type("integer") is ArgumentType.INTEGER
    assert ArgumentType.from_schema_type("array") is ArgumentType.ARRAY


def test_argument_type_from_schema_type_unknown_returns_none():
    # Unknown tags return None (not raise) — SchemaType.from_value falls back.
    assert ArgumentType.from_schema_type("datetime") is None
    assert ArgumentType.from_schema_type("") is None


def test_argument_type_as_str_matches_value():
    assert ArgumentType.OBJECT.as_str() == "object"
    assert ArgumentType.NUMBER.as_str() == "number"


def test_argument_type_is_primitive_classification():
    assert ArgumentType.STRING.is_primitive()
    assert ArgumentType.INTEGER.is_primitive()
    assert ArgumentType.NULL.is_primitive()
    assert not ArgumentType.ARRAY.is_primitive()
    assert not ArgumentType.OBJECT.is_primitive()


def test_argument_type_is_numeric_only_int_and_number():
    assert ArgumentType.INTEGER.is_numeric()
    assert ArgumentType.NUMBER.is_numeric()
    assert not ArgumentType.STRING.is_numeric()
    assert not ArgumentType.BOOLEAN.is_numeric()


def test_argument_type_is_composite_only_array_and_object():
    assert ArgumentType.ARRAY.is_composite()
    assert ArgumentType.OBJECT.is_composite()
    assert not ArgumentType.STRING.is_composite()
    assert not ArgumentType.INTEGER.is_composite()


# ---------------------------------------------------------------------------
# SchemaType — untagged union (string | array) round-trips
# ---------------------------------------------------------------------------


def test_schema_type_single_constructor():
    st = SchemaType.single(ArgumentType.STRING)
    assert st.is_single
    assert not st.is_multiple
    assert st.types == (ArgumentType.STRING,)


def test_schema_type_multiple_preserves_single_element_variant():
    # Multiple([t]) stays Multiple (variant preserved verbatim) — distinct
    # from Single([t]); this matters for serde round-trips.
    st = SchemaType.multiple([ArgumentType.STRING])
    assert st.is_multiple
    assert not st.is_single
    assert st.types == (ArgumentType.STRING,)


def test_schema_type_from_value_bare_string_known():
    st = SchemaType.from_value("integer")
    assert st.is_single
    assert st == ArgumentType.INTEGER


def test_schema_type_from_value_unknown_string_falls_back_to_default():
    st = SchemaType.from_value("datetime")
    assert st == ArgumentType.STRING  # default


def test_schema_type_from_value_empty_array_is_default():
    assert SchemaType.from_value([]) == ArgumentType.STRING


def test_schema_type_from_value_single_element_array_normalised_to_single():
    st = SchemaType.from_value(["integer"])
    assert st.is_single
    assert st == ArgumentType.INTEGER


def test_schema_type_from_value_multi_element_array_is_multiple():
    st = SchemaType.from_value(["string", "null"])
    assert st.is_multiple
    assert st.contains(ArgumentType.STRING)
    assert st.contains(ArgumentType.NULL)


def test_schema_type_from_value_non_string_non_array_is_default():
    assert SchemaType.from_value(42) == ArgumentType.STRING
    assert SchemaType.from_value(None) == ArgumentType.STRING


def test_schema_type_default_is_single_string():
    st = SchemaType.default()
    assert st.is_single
    assert st == ArgumentType.STRING


def test_schema_type_primary_type_single():
    assert SchemaType.single(ArgumentType.INTEGER).primary_type() is ArgumentType.INTEGER


def test_schema_type_primary_type_skips_null_in_union():
    st = SchemaType.from_value(["string", "null"])
    assert st.primary_type() is ArgumentType.STRING


def test_schema_type_primary_type_all_null_is_null():
    assert SchemaType.single(ArgumentType.NULL).primary_type() is ArgumentType.NULL


def test_schema_type_is_nullable():
    assert SchemaType.from_value(["string", "null"]).is_nullable()
    assert SchemaType.single(ArgumentType.NULL).is_nullable()
    assert not SchemaType.single(ArgumentType.STRING).is_nullable()
    assert not SchemaType.from_value(["string", "integer"]).is_nullable()


def test_schema_type_contains():
    st = SchemaType.from_value(["string", "integer"])
    assert st.contains(ArgumentType.STRING)
    assert st.contains(ArgumentType.INTEGER)
    assert not st.contains(ArgumentType.NULL)


def test_schema_type_is_primitive_all_members():
    assert SchemaType.from_value(["string", "null"]).is_primitive()
    assert not SchemaType.from_value(["string", "array"]).is_primitive()


def test_schema_type_is_composite_any_member():
    assert SchemaType.from_value(["string", "array"]).is_composite()
    assert not SchemaType.from_value(["string", "null"]).is_composite()


def test_schema_type_is_numeric_any_member():
    assert SchemaType.from_value(["string", "integer"]).is_numeric()
    assert not SchemaType.from_value(["string", "null"]).is_numeric()


def test_schema_type_to_schema_value_single_is_string():
    assert SchemaType.single(ArgumentType.STRING).to_schema_value() == "string"


def test_schema_type_to_schema_value_multiple_is_array():
    assert SchemaType.from_value(["string", "null"]).to_schema_value() == ["string", "null"]


def test_schema_type_eq_with_bare_argument_type_only_when_single():
    # Rust PartialEq<ArgumentType>: Single(t) == t; Multiple never equals bare.
    assert SchemaType.single(ArgumentType.STRING) == ArgumentType.STRING
    assert SchemaType.multiple([ArgumentType.STRING]) != ArgumentType.STRING
    assert SchemaType.from_value(["string", "null"]) != ArgumentType.STRING


def test_schema_type_eq_with_schema_type_uses_types_and_variant():
    a = SchemaType.multiple([ArgumentType.STRING])
    b = SchemaType.multiple([ArgumentType.STRING])
    c = SchemaType.single(ArgumentType.STRING)  # same types, different variant
    assert a == b
    assert a != c


def test_schema_type_is_hashable():
    # variant-pinned hash → usable as dict keys / set members.
    s = {SchemaType.single(ArgumentType.STRING), SchemaType.single(ArgumentType.STRING)}
    assert len(s) == 1


def test_schema_type_repr_and_str():
    assert repr(SchemaType.single(ArgumentType.STRING)) == "SchemaType.single(<ArgumentType.STRING: 'string'>)"
    assert str(SchemaType.from_value(["string", "null"])) == "string, null"


# ---------------------------------------------------------------------------
# ValidationError / ValidationErrors
# ---------------------------------------------------------------------------


def test_validation_error_str_format():
    assert str(ValidationError(field="name", message="must not be empty")) == "name: must not be empty"


def test_validation_errors_iter_len_empty_str():
    empty = ValidationErrors()
    assert empty.is_empty()
    assert len(empty) == 0
    assert list(empty) == []
    assert str(empty) == ""


def test_validation_errors_collects_multiple():
    errs = ValidationErrors(
        [
            ValidationError(field="name", message="empty"),
            ValidationError(field="namespace", message="bad chars"),
        ]
    )
    assert not errs.is_empty()
    assert len(errs) == 2
    assert str(errs) == "name: empty; namespace: bad chars"


# ---------------------------------------------------------------------------
# ToolArgument — constructor + builder chain + wire
# ---------------------------------------------------------------------------


def test_tool_argument_new_defaults():
    arg = ToolArgument.new("query", "Search query")
    assert arg.name == "query"
    assert arg.description == "Search query"
    assert arg.arg_type == ArgumentType.STRING
    assert arg.required is True
    assert arg.allowed_values == []
    assert arg.default is None
    assert arg.schema is None


def test_tool_argument_with_type_accepts_argument_type():
    arg = ToolArgument.new("n", "d").with_type(ArgumentType.INTEGER)
    assert arg.arg_type == ArgumentType.INTEGER
    assert arg.arg_type.is_single


def test_tool_argument_with_type_accepts_schema_type_passthrough():
    st = SchemaType.from_value(["string", "null"])
    arg = ToolArgument.new("n", "d").with_type(st)
    assert arg.arg_type is st
    assert arg.arg_type.is_nullable()


def test_tool_argument_builder_chain_full():
    arg = (
        ToolArgument.new("level", "Verbosity")
        .with_type(ArgumentType.INTEGER)
        .set_optional()
        .with_default(1)
        .with_allowed_values([1, 2, 3])
        .with_minimum(1)
        .with_maximum(3)
        .with_exclusive_minimum(0)
        .with_exclusive_maximum(4)
    )
    assert arg.required is False
    assert arg.default == 1
    assert arg.allowed_values == [1, 2, 3]
    assert arg.minimum == 1
    assert arg.maximum == 3
    assert arg.exclusive_minimum == 0
    assert arg.exclusive_maximum == 4


def test_tool_argument_to_wire_minimal_omits_optional_fields():
    # required=True omitted, allowed_values empty omitted, all None omitted.
    wire = ToolArgument.new("query", "Search").to_wire()
    assert wire == {"name": "query", "description": "Search", "type": "string"}


def test_tool_argument_to_wire_emits_required_false():
    wire = ToolArgument.new("q", "d").set_optional().to_wire()
    assert wire["required"] is False


def test_tool_argument_to_wire_emits_allowed_values_when_non_empty():
    wire = ToolArgument.new("c", "d").with_allowed_values(["a", "b"]).to_wire()
    assert wire["allowed_values"] == ["a", "b"]


def test_tool_argument_to_wire_emits_schema_and_default_and_bounds():
    arg = (
        ToolArgument.new("n", "d")
        .with_type(ArgumentType.INTEGER)
        .set_optional()
        .with_default(5)
        .with_schema({"type": "integer"})
        .with_minimum(1)
        .with_maximum(9)
    )
    wire = arg.to_wire()
    assert wire["type"] == "integer"
    assert wire["required"] is False
    assert wire["default"] == 5
    assert wire["schema"] == {"type": "integer"}
    assert wire["minimum"] == 1
    assert wire["maximum"] == 9


def test_tool_argument_serde_round_trip_through_to_wire():
    # to_wire emits "type" alias → model_validate renormalises via the
    # before-validator; single-element arrays etc. all round-trip.
    original = (
        ToolArgument.new("mode", "m")
        .with_type(SchemaType.from_value(["string", "null"]))
        .set_optional()
        .with_default("auto")
        .with_allowed_values(["auto", "manual"])
    )
    wire = original.to_wire()
    restored = ToolArgument.model_validate(wire)
    assert restored.name == "mode"
    assert restored.arg_type.is_nullable()
    assert restored.required is False
    assert restored.default == "auto"
    assert restored.allowed_values == ["auto", "manual"]


def test_tool_argument_field_validator_normalises_str_and_list():
    # The before-validator accepts both a bare "type" string and a list.
    arg_str = ToolArgument.model_validate({"name": "n", "description": "d", "type": "integer"})
    assert arg_str.arg_type == ArgumentType.INTEGER
    arg_list = ToolArgument.model_validate(
        {"name": "n", "description": "d", "type": ["string", "null"]}
    )
    assert arg_list.arg_type.is_nullable()
    assert arg_list.arg_type.primary_type() is ArgumentType.STRING


# ---------------------------------------------------------------------------
# ToolDescription — validation + schema accessors
# ---------------------------------------------------------------------------


def test_tool_description_new_defaults():
    desc = ToolDescription.new("search", "Search the web")
    assert desc.name == "search"
    assert desc.description == "Search the web"
    assert desc.namespace is None
    assert desc.kind is None
    assert desc.title is None
    assert desc.arguments_schema is None


def test_tool_description_builder_chain():
    desc = (
        ToolDescription.new("search", "Search")
        .with_namespace("web")
        .with_kind("render")
        .with_title("Web Search")
        .with_arguments_schema({"type": "object", "properties": {}})
    )
    assert desc.namespace == "web"
    assert desc.kind == "render"
    assert desc.title == "Web Search"
    assert desc.arguments_schema == {"type": "object", "properties": {}}


def test_tool_description_validate_valid_is_empty():
    desc = ToolDescription.new("good_name", "d").with_namespace("ns")
    assert desc.validate().is_empty()


def test_tool_description_validate_empty_name():
    errs = ToolDescription.new("", "d").validate()
    assert not errs.is_empty()
    assert any(e.field == "name" and "empty" in e.message for e in errs)


def test_tool_description_validate_invalid_name_chars():
    # CJK / spaces / punctuation are rejected — only [a-zA-Z0-9_-] allowed.
    errs = ToolDescription.new("中文名", "d").validate()
    assert any(e.field == "name" and "invalid characters" in e.message for e in errs)
    errs2 = ToolDescription.new("bad name", "d").validate()
    assert any(e.field == "name" for e in errs2)


def test_tool_description_validate_name_allows_dash_and_underscore():
    assert ToolDescription.new("my_tool-1", "d").validate().is_empty()


def test_tool_description_validate_empty_namespace_when_set():
    # An explicit empty namespace is flagged; None namespace is fine.
    errs = ToolDescription.new("n", "d").with_namespace("").validate()
    assert any(e.field == "namespace" and "empty" in e.message for e in errs)


def test_tool_description_to_arguments_lossy_empty_without_schema():
    assert ToolDescription.new("n", "d").to_arguments_lossy() == []


def test_tool_description_to_input_schema_returns_attached_deepcopy():
    schema = {"type": "object", "properties": {"q": {"type": "string"}}, "$defs": {}}
    desc = ToolDescription.new("n", "d").with_arguments_schema(schema)
    result = desc.to_input_schema()
    assert result == schema
    result["properties"]["q"]["type"] = "integer"  # mutating copy must not touch original
    assert schema["properties"]["q"]["type"] == "string"


def test_tool_description_to_input_schema_synthesises_empty_when_unset():
    assert ToolDescription.new("n", "d").to_input_schema() == {
        "type": "object",
        "properties": {},
        "required": [],
    }


def test_tool_description_str_with_namespace_and_description():
    desc = ToolDescription.new("search", "Search the web").with_namespace("web")
    # Em dash (U+2014) — matches grok's Display impl exactly.
    assert str(desc) == "web.search — Search the web"


def test_tool_description_str_without_namespace():
    assert str(ToolDescription.new("ping", "")) == "ping"


# ---------------------------------------------------------------------------
# parse_arguments_from_schema_lossy — the 15 grok schema fixtures
# ---------------------------------------------------------------------------


def _arg(args: list[ToolArgument], name: str) -> ToolArgument:
    return next(a for a in args if a.name == name)


def test_parse_schema_basic_primitives_with_required_and_default():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "description": "Max results", "default": 10},
        },
        "required": ["query"],
    }
    args = parse_arguments_from_schema_lossy(schema)
    assert len(args) == 2
    query = _arg(args, "query")
    assert query.arg_type == ArgumentType.STRING
    assert query.required is True
    limit = _arg(args, "limit")
    assert limit.arg_type == ArgumentType.INTEGER
    assert limit.required is False  # not in required
    assert limit.default == 10


def test_parse_schema_enum_values_inline():
    schema = {
        "properties": {"color": {"type": "string", "enum": ["red", "blue"]}},
    }
    color = _arg(parse_arguments_from_schema_lossy(schema), "color")
    assert color.allowed_values == ["red", "blue"]
    assert color.arg_type == ArgumentType.STRING


def test_parse_schema_composite_types_store_schema():
    schema = {
        "properties": {
            "filters": {"type": "object", "description": "filter map"},
            "tags": {"type": "array", "description": "tag list"},
        }
    }
    args = parse_arguments_from_schema_lossy(schema)
    assert _arg(args, "filters").schema is not None
    assert _arg(args, "tags").schema is not None


def test_parse_schema_primitives_do_not_store_schema():
    schema = {"properties": {"flag": {"type": "boolean"}}}
    assert _arg(parse_arguments_from_schema_lossy(schema), "flag").schema is None


def test_parse_schema_empty_or_missing_properties_returns_empty():
    assert parse_arguments_from_schema_lossy({}) == []
    assert parse_arguments_from_schema_lossy({"type": "object"}) == []
    assert parse_arguments_from_schema_lossy({"properties": {}}) == []
    assert parse_arguments_from_schema_lossy(None) == []
    assert parse_arguments_from_schema_lossy("nope") == []


def test_parse_schema_required_and_default_are_orthogonal():
    # An arg can be required AND have a default; another can be optional with none.
    schema = {
        "properties": {
            "a": {"type": "string", "default": "x"},
            "b": {"type": "string"},
        },
        "required": ["a"],
    }
    args = parse_arguments_from_schema_lossy(schema)
    a = _arg(args, "a")
    b = _arg(args, "b")
    assert a.required is True and a.default == "x"
    assert b.required is False and b.default is None


def test_parse_schema_null_type():
    schema = {"properties": {"n": {"type": "null"}}}
    arg = _arg(parse_arguments_from_schema_lossy(schema), "n")
    assert arg.arg_type == ArgumentType.NULL


def test_parse_schema_nullable_string_type_array():
    schema = {"properties": {"s": {"type": ["string", "null"]}}}
    arg = _arg(parse_arguments_from_schema_lossy(schema), "s")
    assert arg.arg_type.is_nullable()
    assert arg.arg_type.primary_type() is ArgumentType.STRING


def test_parse_schema_multi_type_union_without_null():
    schema = {"properties": {"m": {"type": ["string", "integer"]}}}
    arg = _arg(parse_arguments_from_schema_lossy(schema), "m")
    assert arg.arg_type.is_multiple
    assert arg.arg_type.contains(ArgumentType.STRING)
    assert arg.arg_type.contains(ArgumentType.INTEGER)
    assert not arg.arg_type.is_nullable()


def test_parse_schema_any_of_ref_resolves_enum():
    schema = {
        "$defs": {"Color": {"type": "string", "enum": ["a", "b"]}},
        "properties": {
            "mode": {
                "anyOf": [{"$ref": "#/$defs/Color"}, {"type": "null"}],
                "default": None,
            }
        },
    }
    mode = _arg(parse_arguments_from_schema_lossy(schema), "mode")
    assert mode.arg_type == ArgumentType.STRING
    assert mode.allowed_values == ["a", "b"]
    # Resolved default (enum first variant) overrides the prop's explicit null.
    assert mode.default == "a"
    assert mode.required is False
    assert mode.schema is None


def test_parse_schema_direct_ref_resolves_enum():
    schema = {
        "$defs": {"Color": {"type": "string", "enum": ["red", "blue"]}},
        "properties": {"color": {"$ref": "#/$defs/Color"}},
        "required": ["color"],
    }
    color = _arg(parse_arguments_from_schema_lossy(schema), "color")
    assert color.arg_type == ArgumentType.STRING
    assert color.allowed_values == ["red", "blue"]
    assert color.default == "red"  # first enum variant
    assert color.required is True


def test_parse_schema_compact_enum_in_defs_optional_and_required():
    schema = {
        "$defs": {"Dur": {"type": "string", "enum": ["auto", "manual"]}},
        "properties": {
            "dur_optional": {"anyOf": [{"$ref": "#/$defs/Dur"}, {"type": "null"}]},
            "dur_required": {"$ref": "#/$defs/Dur"},
        },
        "required": ["dur_required"],
    }
    args = parse_arguments_from_schema_lossy(schema)
    opt = _arg(args, "dur_optional")
    req = _arg(args, "dur_required")
    for arg in (opt, req):
        assert arg.arg_type == ArgumentType.STRING
        assert arg.allowed_values == ["auto", "manual"]
        assert arg.default == "auto"
    assert opt.required is False
    assert req.required is True


def test_parse_schema_any_of_union_type_without_ref():
    # anyOf with no $ref and no sibling type → infer union, store raw schema.
    schema = {
        "properties": {
            "to": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            }
        }
    }
    arg = _arg(parse_arguments_from_schema_lossy(schema), "to")
    assert arg.arg_type.contains(ArgumentType.STRING)
    assert arg.arg_type.contains(ArgumentType.ARRAY)
    assert arg.arg_type.primary_type() is ArgumentType.STRING
    assert arg.schema is not None  # raw property object stored


def test_parse_schema_missing_defs_falls_back_to_explicit_type():
    # A $ref that can't be resolved (no $defs) falls back to the sibling type.
    schema = {"properties": {"x": {"$ref": "#/$defs/Missing", "type": "string"}}}
    arg = _arg(parse_arguments_from_schema_lossy(schema), "x")
    assert arg.arg_type == ArgumentType.STRING


def test_parse_schema_numeric_constraints():
    schema = {
        "properties": {
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "exclusiveMinimum": 0,
                "exclusiveMaximum": 200,
            }
        }
    }
    arg = _arg(parse_arguments_from_schema_lossy(schema), "n")
    assert arg.arg_type == ArgumentType.INTEGER
    assert arg.minimum == 1
    assert arg.maximum == 100
    assert arg.exclusive_minimum == 0
    assert arg.exclusive_maximum == 200


# ---------------------------------------------------------------------------
# ToolDescription.to_arguments_lossy — end-to-end delegation
# ---------------------------------------------------------------------------


def test_to_arguments_lossy_delegates_to_parser():
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string", "description": "Query"}},
        "required": ["q"],
    }
    desc = ToolDescription.new("search", "s").with_arguments_schema(schema)
    args = desc.to_arguments_lossy()
    assert len(args) == 1
    assert args[0].name == "q"
    assert args[0].required is True
    assert args[0].arg_type == ArgumentType.STRING
