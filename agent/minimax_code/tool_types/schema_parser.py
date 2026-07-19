"""JSON Schema → :class:`ToolArgument` lossy parser (R65).

Fusion of grok-build's ``xai-tool-types::schema_utils`` — a deliberately
minimal subset of JSON Schema, just enough for render tools (which don't
speak full JSON Schema) to surface flat top-level parameters.

The parser resolves the two ``schemars`` patterns Rust generates for enums
and ``Option<Enum>``:

* ``anyOf: [{$ref: "#/$defs/X"}, {type: "null"}]`` — optional enum (the
  ``$ref`` branch is followed into ``$defs``; the ``null`` branch is the
  nullable marker).
* ``{$ref: "#/$defs/X"}`` — required enum.
* ``$defs`` entries come in two shapes: compact ``{enum: [...]}`` or
  ``oneOf: [{const: ...}, ...]``.

Composite types (``array`` / ``object``) keep their full sub-schema in
:meth:`ToolArgument.schema` so downstream consumers can inspect nested
structure. Everything else is mapped onto the flat :class:`ToolArgument`
fields. Unsupported keywords (``allOf``, ``if/then/else``, ``pattern``,
``items``, …) are silently dropped — see the source crate's
``# Not supported`` table.

This module is the dependency that :meth:`ToolDescription.to_arguments_lossy`
delegates to; without it that method would be dead code (the reason R65
migrates ``schema_utils.rs`` alongside ``types.rs`` to close the loop).
"""

from __future__ import annotations

from typing import Any

from .types import ArgumentType, SchemaType, ToolArgument

__all__ = ["parse_arguments_from_schema_lossy"]


def _infer_arg_type(sample: Any) -> ArgumentType:
    """Infer an :class:`ArgumentType` from a sample enum value.

    Mirrors Rust ``infer_arg_type``: string → String, integer (i64/u64) →
    Integer, other number → Number, bool → Boolean, anything else
    (including ``None`` / JSON null) → String fallback.

    ``bool`` is checked **before** ``int`` because Python ``bool`` is an
    ``int`` subclass — serde_json's ``Value::Bool`` arm likewise fires
    ahead of the ``Value::Number`` arms.
    """
    if isinstance(sample, bool):
        return ArgumentType.BOOLEAN
    if isinstance(sample, int):  # i64 / u64 → Integer
        return ArgumentType.INTEGER
    if isinstance(sample, float):
        return ArgumentType.NUMBER
    if isinstance(sample, str):
        return ArgumentType.STRING
    return ArgumentType.STRING  # None / null / other → String fallback


def _extract_enum_from_def(
    enum_def: Any,
) -> tuple[ArgumentType | None, list[Any] | None, Any]:
    """Extract ``(type, allowed_values, first_value)`` from a ``$defs`` entry.

    Handles the two schemars shapes:

    * Compact: ``{"type": "string", "enum": ["a", "b"]}``
    * oneOf:   ``{"oneOf": [{"const": "a"}, {"const": "b"}]}``

    Returns ``(None, None, None)`` when the entry is not a dict, has no
    usable ``enum`` / ``oneOf``, or the collection is empty.
    """
    if not isinstance(enum_def, dict):
        return (None, None, None)

    # Compact form: non-empty "enum" array.
    values = enum_def.get("enum")
    if isinstance(values, list) and values:
        first = values[0]
        return (_infer_arg_type(first), list(values), first)

    # oneOf form: collect "const" values from each branch.
    one_of = enum_def.get("oneOf")
    if not isinstance(one_of, list):
        return (None, None, None)

    collected: list[Any] = []
    first_value: Any = None
    for variant in one_of:
        if isinstance(variant, dict) and "const" in variant:
            const_val = variant["const"]
            if first_value is None:
                first_value = const_val
            collected.append(const_val)

    if not collected:
        return (None, None, None)
    return (_infer_arg_type(first_value), collected, first_value)


def _resolve_ref_type(
    prop: Any,
    defs: dict[str, Any] | None,
) -> tuple[SchemaType | None, list[Any] | None, Any, dict[str, Any] | None]:
    """Resolve type info from a property, following ``$ref``/``anyOf``.

    Returns ``(type, allowed_values, default, raw_schema)``:

    * ``anyOf`` with a ``$ref`` branch → resolve the referenced ``$defs``
      enum (type as ``Single``, the enum's values, the enum's first value
      as default, no raw schema).
    * ``anyOf`` without ``$ref`` (and no sibling ``type``) → infer a union
      :class:`SchemaType` from the branches and store the whole property
      object as the raw schema.
    * direct ``$ref`` → same as the ``anyOf`` ``$ref`` case.
    * otherwise → ``(None, None, None, None)``.
    """
    if not isinstance(prop, dict):
        return (None, None, None, None)

    # Pattern 1: anyOf — schemars emits this for Option<Enum> / union types.
    any_of = prop.get("anyOf")
    if isinstance(any_of, list):
        # Look for a $ref branch into $defs.
        for item in any_of:
            ref_path = item.get("$ref") if isinstance(item, dict) else None
            resolved = _resolve_ref_in_defs(ref_path, defs)
            if resolved is not None:
                ty, vals, default = resolved
                return (
                    SchemaType.single(ty) if ty is not None else None,
                    vals,
                    default,
                    None,
                )

        # No $ref found — infer a union type from the branches' type fields.
        if "type" not in prop:
            types: list[ArgumentType] = []
            for branch in any_of:
                t_str = branch.get("type") if isinstance(branch, dict) else None
                if isinstance(t_str, str):
                    t = ArgumentType.from_schema_type(t_str)
                    if t is not None:
                        types.append(t)
            if len(types) == 1:
                return (SchemaType.single(types[0]), None, None, dict(prop))
            if len(types) > 1:
                return (SchemaType.multiple(types), None, None, dict(prop))

    # Pattern 2: direct $ref (no anyOf wrapper) — required (non-Option) enum.
    resolved = _resolve_ref_in_defs(prop.get("$ref"), defs)
    if resolved is not None:
        ty, vals, default = resolved
        return (
            SchemaType.single(ty) if ty is not None else None,
            vals,
            default,
            None,
        )

    return (None, None, None, None)


def _resolve_ref_in_defs(
    ref_path: Any,
    defs: dict[str, Any] | None,
) -> tuple[ArgumentType | None, list[Any] | None, Any] | None:
    """Follow a ``#/$defs/<name>`` ref into ``defs``; extract enum info.

    Returns ``None`` when the ref is absent, doesn't use the ``#/$defs/``
    prefix, ``defs`` is missing, or the named entry is absent.
    """
    if not isinstance(ref_path, str) or defs is None:
        return None
    prefix = "#/$defs/"
    if not ref_path.startswith(prefix):
        return None
    enum_name = ref_path[len(prefix):]
    enum_def = defs.get(enum_name)
    if enum_def is None:
        return None
    return _extract_enum_from_def(enum_def)


def _number(prop: dict[str, Any], key: str) -> int | float | None:
    """Read a numeric bound from ``prop[key]``; non-numbers → ``None``.

    ``bool`` is rejected (it is an ``int`` subclass in Python but not a
    JSON number — serde_json's ``as_number`` likewise rejects ``Value::Bool``).
    """
    v = prop.get(key)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    return None


def parse_arguments_from_schema_lossy(schema: Any) -> list[ToolArgument]:
    """Parse a JSON Schema parameters object into flat :class:`ToolArgument`s.

    Extracts top-level ``properties`` (each key → one argument), honouring
    ``required`` (top-level), ``$defs`` (top-level, resolved via ``$ref``),
    and the per-property keywords ``type`` / ``description`` / ``default`` /
    ``enum`` / ``allowed_values`` / ``minimum`` / ``maximum`` /
    ``exclusiveMinimum`` / ``exclusiveMaximum`` / ``$ref`` / ``anyOf``.

    Composite (``array`` / ``object``) arguments store their full sub-schema
    in :attr:`ToolArgument.schema`; primitives do not.

    Returns ``[]`` when ``schema`` has no ``properties`` object (or it isn't
    a dict, or ``schema`` itself isn't a dict).
    """
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []

    # Top-level "required" → membership set for per-argument required flags.
    required: set[str] = set()
    required_raw = schema.get("required")
    if isinstance(required_raw, list):
        for v in required_raw:
            if isinstance(v, str):
                required.add(v)

    # Top-level "$defs" → dict for $ref resolution (None when absent/non-dict).
    defs_raw = schema.get("$defs")
    defs: dict[str, Any] | None = defs_raw if isinstance(defs_raw, dict) else None

    args: list[ToolArgument] = []
    for name, prop in properties.items():
        resolved_type, resolved_values, resolved_default, resolved_schema = _resolve_ref_type(
            prop, defs
        )

        # arg_type: resolved $ref type > explicit "type" > default (String).
        if resolved_type is not None:
            arg_type = resolved_type
        elif isinstance(prop, dict) and "type" in prop:
            arg_type = SchemaType.from_value(prop["type"])
        else:
            arg_type = SchemaType.default()

        if isinstance(prop, dict):
            desc_raw = prop.get("description")
            description = desc_raw if isinstance(desc_raw, str) else ""
        else:
            description = ""

        # default: resolved (enum first variant) > explicit "default".
        default = resolved_default if resolved_default is not None else (
            prop.get("default") if isinstance(prop, dict) else None
        )
        is_required = name in required

        # allowed_values: resolved (from $defs enum) > explicit enum/allowed_values.
        if resolved_values is not None:
            allowed_values: list[Any] = resolved_values
        elif isinstance(prop, dict):
            av = prop.get("allowed_values")
            if av is None:
                av = prop.get("enum")
            allowed_values = list(av) if isinstance(av, list) else []
        else:
            allowed_values = []

        # schema: resolved raw > composite stores prop clone > None.
        if resolved_schema is not None:
            schema_field: Any = resolved_schema
        elif arg_type.is_composite() and isinstance(prop, dict):
            schema_field = dict(prop)
        else:
            schema_field = None

        minimum = _number(prop, "minimum") if isinstance(prop, dict) else None
        maximum = _number(prop, "maximum") if isinstance(prop, dict) else None
        exclusive_minimum = (
            _number(prop, "exclusiveMinimum") if isinstance(prop, dict) else None
        )
        exclusive_maximum = (
            _number(prop, "exclusiveMaximum") if isinstance(prop, dict) else None
        )

        arg = (
            ToolArgument.new(name, description)
            .with_type(arg_type)
            .with_allowed_values(allowed_values)
        )
        if not is_required:
            arg = arg.set_optional()
        if default is not None:
            arg = arg.with_default(default)
        if schema_field is not None:
            arg = arg.with_schema(schema_field)
        if minimum is not None:
            arg = arg.with_minimum(minimum)
        if maximum is not None:
            arg = arg.with_maximum(maximum)
        if exclusive_minimum is not None:
            arg = arg.with_exclusive_minimum(exclusive_minimum)
        if exclusive_maximum is not None:
            arg = arg.with_exclusive_maximum(exclusive_maximum)
        args.append(arg)

    return args
