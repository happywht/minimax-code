"""Shared pydantic wire helpers for workspace struct types (R67).

Workspace wire structs are plain ``#[serde(default)]`` snake_case structs
over ``String`` / ``Vec`` / ``BTreeMap`` / ``Option`` / scalars. Two serde
behaviours must be reproduced exactly:

1. ``BTreeMap<String, _>`` serialises keys in **lexicographic order**;
   Python ``dict`` preserves insertion order. :func:`sort_mappings`
   walks the dumped JSON tree and re-sorts every mapping so the wire
   bytes are stable regardless of how the dict was constructed (this is
   what makes snapshot tests and byte-hashes meaningful).
2. ``#[serde(default)]`` always emits the field (even at its default),
   so :meth:`WireModel.to_wire` includes every field — only structs with
   an explicit ``skip_serializing_if`` (just ``UserQuestionOption.preview``
   in this crate) override :meth:`to_wire`.

``model_dump(mode="json")`` already handles the rest correctly:
``StrEnum`` → value string, ``datetime`` → ISO-8601, ``None`` → ``null``.
``bytes`` is **not** base64 by default — pydantic UTF-8-decodes it, which
corrupts non-UTF-8 payloads; the one struct with a ``bytes`` field
(:class:`~minimax_code.workspace_types.ToolOutputChunk`) overrides this
with a base64 ``field_serializer`` / ``field_validator`` pair to honour
Rust's ``bytes_as_base64`` serde module.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["WireModel", "sort_mappings"]


def sort_mappings(obj: Any) -> Any:
    """Recursively sort mapping keys to mirror ``BTreeMap`` wire order.

    Lists are preserved in order (``Vec`` semantics); mappings have their
    keys sorted lexicographically (``BTreeMap`` semantics); scalars pass
    through untouched.
    """
    if isinstance(obj, Mapping):
        return {k: sort_mappings(obj[k]) for k in sorted(obj)}
    if isinstance(obj, list):
        return [sort_mappings(x) for x in obj]
    return obj


class WireModel(BaseModel):
    """Base for workspace wire structs.

    Provides ``populate_by_name=True`` and a :meth:`to_wire` that dumps
    JSON-safe values with BTreeMap-sorted mapping keys. Subclasses with
    a ``skip_serializing_if`` field override :meth:`to_wire`.
    """

    model_config = ConfigDict(populate_by_name=True)

    def to_wire(self) -> dict[str, Any]:
        """JSON-safe dict with all fields + BTreeMap-sorted mapping keys."""
        return sort_mappings(self.model_dump(mode="json", by_alias=True))

    @classmethod
    def default(cls) -> WireModel:
        """All-default instance — mirrors Rust ``Default::default()``.

        Subclasses with required fields override this to supply a zero
        value (``""`` / ``0``) for each required field, matching how
        Rust's ``#[derive(Default)]`` fills ``String`` with ``""``.
        """
        return cls()
