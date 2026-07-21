"""Tests for ``sampler.conversation_content_part`` (R221,
``xai-grok-sampling-types`` ``conversation.rs``).

Covers the conversation-layer content-part tagged union -- the sampler
package's first **internally-tagged mixed enum with data-carrying struct
variants** (the fourth serde representation shape, after R84's ``untagged``
:class:`JsonRpcId`, R89's ``internally-tagged`` :class:`HookEvent`, and R220's
``externally-tagged`` :class:`ConversationToolChoice`). ``#[serde(tag = "type",
rename_all = "snake_case")]`` means each struct variant serializes as a single
object carrying the snake_case variant name under the ``"type"`` discriminator
plus the variant's own field:

- :class:`TextPart` -> ``{"type": "text", "text": <text>}``
- :class:`ImagePart` -> ``{"type": "image", "url": <url>}``

This is the standard OpenAI content-part wire shape, hoisted into the
conversation layer. Distinct from the wire-layer :class:`ContentBlock` family
(types.rs / messages.rs, Anthropic surface): the ``part`` vs ``block`` suffix
keeps the two families distinct, so -- unlike the R219
:class:`ConversationStopReason` / R220 :class:`ConversationToolChoice` renames
-- no ``Conversation`` prefix is needed; the ``<Type>Part`` variant naming
mirrors the R202 ``<Type>Block`` precedent. This leaf unblocks the conversation
consumer layer (``UserItem`` / ``AssistantItem`` carry ``Vec<ContentPart>``
bodies).
"""

from __future__ import annotations

import pytest

import minimax_code.sampler.conversation_content_part as _ccp
from minimax_code.sampler import (
    ContentPart,
    ImagePart,
    TextPart,
)


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_content_part symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert ContentPart is _ccp.ContentPart
        assert TextPart is _ccp.TextPart
        assert ImagePart is _ccp.ImagePart


# ---------------------------------------------------------------------------
# as_payload: internally-tagged serialization (variant -> wire shape).
# ---------------------------------------------------------------------------


class TestAsPayloadSerializesToInternallyTaggedWire:
    """``#[serde(tag="type", rename_all="snake_case")]`` -> each struct variant
    serializes as ``{"type": <snake_case tag>, <field>: <value>}``."""

    def test_text_serializes_to_type_plus_text(self) -> None:
        assert TextPart(text="hello").as_payload() == {
            "type": "text",
            "text": "hello",
        }

    def test_image_serializes_to_type_plus_url(self) -> None:
        assert ImagePart(url="https://example.com/x.png").as_payload() == {
            "type": "image",
            "url": "https://example.com/x.png",
        }

    def test_text_preserves_arbitrary_content(self) -> None:
        # The text is opaque to this layer -- any string survives verbatim.
        assert TextPart(text="").as_payload() == {"type": "text", "text": ""}
        assert TextPart(text="multi\nline\n").as_payload() == {
            "type": "text",
            "text": "multi\nline\n",
        }

    def test_image_preserves_arbitrary_url(self) -> None:
        # A base64 data URI is as valid as an https URL at this layer.
        assert ImagePart(url="data:image/png;base64,iVBORw0KGgo=").as_payload() == {
            "type": "image",
            "url": "data:image/png;base64,iVBORw0KGgo=",
        }


# ---------------------------------------------------------------------------
# from_payload: internally-tagged deserialization (wire shape -> variant).
# ---------------------------------------------------------------------------


class TestFromPayloadParsesInternallyTaggedWire:
    """The strict (no catch-all) inverse of :meth:`as_payload`: a dict with a
    string ``"type"`` discriminator + the variant's string field -> the
    matching variant; anything else -> ``ValueError`` (parity with the R220
    strict :class:`ConversationToolChoice`; contrast the R218 ``UNKNOWN``
    catch-all enums which never raise)."""

    def test_text_dict_returns_text_part(self) -> None:
        result = ContentPart.from_payload({"type": "text", "text": "hi"})
        assert isinstance(result, TextPart)
        assert result.text == "hi"

    def test_image_dict_returns_image_part(self) -> None:
        result = ContentPart.from_payload(
            {"type": "image", "url": "https://x.test/y.jpg"}
        )
        assert isinstance(result, ImagePart)
        assert result.url == "https://x.test/y.jpg"

    def test_extra_keys_tolerated(self) -> None:
        # serde's default ignores unknown fields on a struct variant -- the
        # wire may carry forward-compat keys (e.g. cache_control) the
        # conversation layer does not model yet.
        result = ContentPart.from_payload(
            {"type": "text", "text": "ok", "cache_control": {"type": "ephemeral"}}
        )
        assert isinstance(result, TextPart)
        assert result.text == "ok"

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "text", ["text"], ("text",)):
            with pytest.raises(ValueError):
                ContentPart.from_payload(raw)

    def test_missing_type_raises(self) -> None:
        with pytest.raises(ValueError):
            ContentPart.from_payload({"text": "hi"})

    def test_non_string_type_raises(self) -> None:
        for bad_tag in (None, 42, True, ["text"], {"nested": 1}):
            with pytest.raises(ValueError):
                ContentPart.from_payload({"type": bad_tag, "text": "hi"})

    def test_unknown_tag_raises(self) -> None:
        # No #[serde(other)] catch-all -- an unknown discriminator fails (the
        # caller decides forward-compat), parity with the strict enums.
        with pytest.raises(ValueError):
            ContentPart.from_payload({"type": "audio", "audio": "..."})
        with pytest.raises(ValueError):
            ContentPart.from_payload({"type": ""})

    def test_text_missing_field_raises(self) -> None:
        with pytest.raises(ValueError):
            ContentPart.from_payload({"type": "text"})

    def test_text_non_string_field_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"nested": 1}):
            with pytest.raises(ValueError):
                ContentPart.from_payload({"type": "text", "text": bad})

    def test_image_missing_field_raises(self) -> None:
        with pytest.raises(ValueError):
            ContentPart.from_payload({"type": "image"})

    def test_image_non_string_field_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"nested": 1}):
            with pytest.raises(ValueError):
                ContentPart.from_payload({"type": "image", "url": bad})

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError):
            ContentPart.from_payload({})


# ---------------------------------------------------------------------------
# Round-trip: from_payload(as_payload(x)) == x and the wire inverse.
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Every variant round-trips through its wire shape in both directions."""

    def test_variant_round_trips_through_payload(self) -> None:
        # from_payload(as_payload(x)) == x for every variant.
        for part in (
            TextPart(text="hello"),
            TextPart(text=""),
            ImagePart(url="https://x.test/y.png"),
            ImagePart(url="data:image/png;base64,AAAA"),
        ):
            again = ContentPart.from_payload(part.as_payload())
            assert again == part

    def test_wire_round_trips_through_variant(self) -> None:
        # as_payload(from_payload(raw)) == raw for every well-formed payload.
        for raw in (
            {"type": "text", "text": "hello"},
            {"type": "image", "url": "https://x.test/y.png"},
        ):
            assert ContentPart.from_payload(raw).as_payload() == raw

    def test_extra_keys_dropped_on_round_trip(self) -> None:
        # The variant models only type + its field; an extra key on the wire
        # is parsed (tolerated) but not re-emitted -- the round-trip collapses
        # to the canonical shape.
        canonical = ContentPart.from_payload(
            {"type": "text", "text": "ok", "extra": 1}
        ).as_payload()
        assert canonical == {"type": "text", "text": "ok"}


# ---------------------------------------------------------------------------
# Value semantics: frozen+slots union -- structural equality.
# ---------------------------------------------------------------------------


class TestValueSemantics:
    """frozen dataclass -> structural equality (not identity); same variant +
    same field -> equal; different variant or field -> not equal."""

    def test_same_text_parts_equal(self) -> None:
        assert TextPart(text="a") == TextPart(text="a")

    def test_different_text_not_equal(self) -> None:
        assert TextPart(text="a") != TextPart(text="b")

    def test_same_image_parts_equal(self) -> None:
        assert ImagePart(url="u") == ImagePart(url="u")

    def test_different_image_not_equal(self) -> None:
        assert ImagePart(url="u") != ImagePart(url="v")

    def test_text_not_equal_to_image(self) -> None:
        # Different variants are never equal, even if their opaque string
        # payload happens to match.
        assert TextPart(text="u") != ImagePart(url="u")
        assert ImagePart(url="u") != TextPart(text="u")

    def test_all_variants_are_content_part(self) -> None:
        # The union base: every variant isinstance-checks against it.
        for part in (TextPart(text="x"), ImagePart(url="u")):
            assert isinstance(part, ContentPart)


class TestSlotsAndImmutability:
    """frozen+slots -> the variants carry no ``__dict__`` and are immutable
    after construction; the union base is field-less, :class:`TextPart`
    carries exactly ``text``, :class:`ImagePart` carries exactly ``url``."""

    def test_base_is_fieldless(self) -> None:
        assert set(ContentPart.__slots__) == set()

    def test_text_has_single_text_slot(self) -> None:
        assert set(TextPart.__slots__) == {"text"}

    def test_image_has_single_url_slot(self) -> None:
        assert set(ImagePart.__slots__) == {"url"}

    def test_instances_have_no_dict(self) -> None:
        # slots -> no __dict__ on the instance (no ad-hoc attribute stash).
        for part in (TextPart(text="x"), ImagePart(url="u")):
            assert not hasattr(part, "__dict__")

    def test_field_values_round_trip(self) -> None:
        assert TextPart(text="hello").text == "hello"
        assert ImagePart(url="https://x.test/y").url == "https://x.test/y"

    def test_text_is_frozen(self) -> None:
        # Variable attribute name keeps the B010 check honest if the field
        # ever renames; frozen rejects the assignment regardless.
        part = TextPart(text="x")
        attr = "text"
        with pytest.raises(AttributeError):
            setattr(part, attr, "y")

    def test_image_is_frozen(self) -> None:
        part = ImagePart(url="u")
        attr = "url"
        with pytest.raises(AttributeError):
            setattr(part, attr, "v")
