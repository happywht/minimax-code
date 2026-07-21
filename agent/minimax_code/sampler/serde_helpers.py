"""Serde wire-normalization helpers (R211, ``xai-grok-sampling-types`` ``serde_helpers.rs``).

R211 lands the ``serde_helpers.rs`` leaf -- the single ``deserialize_with``
hook consumed by two ``Option<String>`` fingerprint fields elsewhere in the
crate: ``types.rs`` ``system_fingerprint`` (on the ChatCompletion response) +
``conversation.rs`` ``model_fingerprint`` (which aliases ``system_fingerprint``
on the wire). Zero external dependency (only the ``serde`` crate, no
``crate::rs`` coupling).

- :func:`empty_string_as_none` -- mirror
  ``crate::serde_helpers::empty_string_as_none``: a ``deserialize_with`` hook
  that runs ``Option::<String>::deserialize`` then ``.filter(|s| !s.is_empty())``
  -- an empty string (``Some("")``) normalizes to ``None`` (some upstream
  services emit an empty string to mean "absent").

This module is no-I/O (a pure value-level normalization). Migration map
(grok -> Python):

- ``pub fn empty_string_as_none<'de, D>(deserializer: D) ->
  Result<Option<String>, D::Error>`` (a serde ``deserialize_with`` hook) -> a
  pure ``def empty_string_as_none(value: str | None) -> str | None`` value-level
  normalizer. The serde hook runs at deserialization time on the wire
  ``Value``; the Python peer runs at ``from_payload`` time on the already-parsed
  ``dict`` value. The empty-string -> ``None`` filter is the same
  (``Some("")`` -> ``None``, ``Some("x")`` -> ``Some("x")``, ``None`` ->
  ``None``); Pythonic ``value or None`` collapses both falsy cases (``""`` and
  ``None``) to ``None``.

Naming: :func:`empty_string_as_none` keeps the grok name verbatim (it is a
free function, no Anthropic Messages API peer collision; the
``empty_string_as_none`` label documents the wire contract precisely).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- the value-level
normalizer covers the parse direction the platform needs.
"""

from __future__ import annotations

__all__ = ["empty_string_as_none"]


def empty_string_as_none(value: str | None) -> str | None:
    """Mirror ``crate::serde_helpers::empty_string_as_none``: normalize an empty
    string to ``None`` (the serde ``deserialize_with`` hook that runs
    ``Option::<String>::deserialize`` then ``.filter(|s| !s.is_empty())``).

    Wire semantics: some upstream services emit an empty string to mean
    "absent"; this hook collapses ``Some("")`` -> ``None`` (and passes
    ``Some("x")`` -> ``"x"``, ``None`` -> ``None`` through unchanged). The
    Pythonic ``value or None`` collapses both falsy cases (``""`` and
    ``None``) to ``None`` -- semantically identical to grok's ``filter``."""
    return value or None
