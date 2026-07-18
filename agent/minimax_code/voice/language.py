"""Grok Speech-to-Text language codes (R31).

Ports ``xai-grok-voice/src/language.rs`` — the host-agnostic slice of grok's
voice crate. Source of truth for the ``language`` query/form parameter on the
xAI STT endpoints (``api.x.ai/v1/stt``). Pure data + pure functions: no IO,
no network, no audio. The 25-language catalog is pinned to the public docs
(docs.x.ai) and locked by tests against drift.

Per the docs, the model can transcribe these languages regardless of the
parameter; setting ``language`` enables Inverse Text Normalization (numbers,
currencies, units → written form) for that language. The STT API does **not**
accept ``auto`` (unlike TTS) — clients must send a concrete code. Use
:func:`language_for_api` to resolve a stored preference (including the
client-only ``auto`` sentinel) before connecting.

Product fusion: this is the language foundation for any future voice-input
feature in MiniMax Code (mic → streaming STT → transcript into the prompt
box). The catalog and the canonicalization helpers are language-agnostic
building blocks — BCP-47 / POSIX locale parsing, alias mapping — reusable
beyond STT. The heavier voice slices (audio capture, streaming STT client,
auth, pipeline, probe) are host-integration layers and remain future rounds.

Struct → frozen dataclass mapping
---------------------------------
grok's ``SttLanguage { code: &'static str, name: &'static str }`` derives
``Copy + Eq``. The faithful Python analogue is ``@dataclass(frozen=True,
slots=True)``: immutable, hashable, value-equal, and pass-by-reference with
value semantics — exactly what Rust's ``Copy`` guarantees for a two-pointer
struct. Instances from the module-level :data:`STT_LANGUAGES` tuple are
interned string literals, so ``lang.code`` returned to callers is the stable
``&'static str`` equivalent.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

__all__ = [
    "SttLanguage",
    "STT_LANGUAGE_AUTO",
    "STT_LANGUAGE_DEFAULT",
    "STT_LANGUAGES",
    "stt_language_by_code",
    "canonicalize_stt_language",
    "language_for_api",
]


@dataclass(frozen=True, slots=True)
class SttLanguage:
    """One supported STT language from the public API catalog.

    ``code`` is the ISO / BCP-47 primary code sent as the ``language``
    parameter (e.g. ``"en"``); ``name`` is the English display name for UIs.
    """

    code: str
    name: str


#: Client-only sentinel meaning "resolve from the process locale at connect time".
#: Never send this value to the STT API — use :func:`language_for_api`.
STT_LANGUAGE_AUTO: str = "auto"

#: Default STT language when unset or unrecognized.
STT_LANGUAGE_DEFAULT: str = "en"


#: Official Grok STT languages (docs.x.ai), sorted by English name.
#:
#: Keep this list in lockstep with the public docs. Adding a code that the API
#: does not list will not break transcription, but Inverse Text Normalization
#: formatting may not apply.
STT_LANGUAGES: tuple[SttLanguage, ...] = (
    SttLanguage(code="ar", name="Arabic"),
    SttLanguage(code="cs", name="Czech"),
    SttLanguage(code="da", name="Danish"),
    SttLanguage(code="nl", name="Dutch"),
    SttLanguage(code="en", name="English"),
    SttLanguage(code="fil", name="Filipino"),
    SttLanguage(code="fr", name="French"),
    SttLanguage(code="de", name="German"),
    SttLanguage(code="hi", name="Hindi"),
    SttLanguage(code="id", name="Indonesian"),
    SttLanguage(code="it", name="Italian"),
    SttLanguage(code="ja", name="Japanese"),
    SttLanguage(code="ko", name="Korean"),
    SttLanguage(code="mk", name="Macedonian"),
    SttLanguage(code="ms", name="Malay"),
    SttLanguage(code="fa", name="Persian"),
    SttLanguage(code="pl", name="Polish"),
    SttLanguage(code="pt", name="Portuguese"),
    SttLanguage(code="ro", name="Romanian"),
    SttLanguage(code="ru", name="Russian"),
    SttLanguage(code="es", name="Spanish"),
    SttLanguage(code="sv", name="Swedish"),
    SttLanguage(code="th", name="Thai"),
    SttLanguage(code="tr", name="Turkish"),
    SttLanguage(code="vi", name="Vietnamese"),
)


def stt_language_by_code(code: str) -> SttLanguage | None:
    """Look up a catalog entry by exact (case-sensitive) code.

    Returns ``None`` for unknown codes, ``"auto"``, or wrong-case variants —
    mirroring grok's exact-match semantics. Callers wanting case-insensitive
    resolution use :func:`canonicalize_stt_language`.
    """
    for lang in STT_LANGUAGES:
        if lang.code == code:
            return lang
    return None


def canonicalize_stt_language(value: str | None) -> str:
    """Map a user/config string to a catalog code or :data:`STT_LANGUAGE_AUTO`.

    Resolution order (mirrors grok):

    * ``None`` / blank / unknown → :data:`STT_LANGUAGE_DEFAULT` (``"en"``)
    * ``"auto"`` (any case) → :data:`STT_LANGUAGE_AUTO`
    * Exact catalog code (any case) → that code
    * BCP-47 / locale forms (``en-US``, ``pt_BR.UTF-8``) → primary subtag when supported
    * Common aliases: ``tl`` → ``fil`` (Tagalog → Filipino)
    """
    raw = (value or "").strip()
    if not raw:
        return STT_LANGUAGE_DEFAULT
    if raw.lower() == STT_LANGUAGE_AUTO:
        return STT_LANGUAGE_AUTO

    matched = _match_supported_code(raw)
    if matched is not None:
        return matched

    primary = _primary_language_subtag(raw)
    matched = _match_supported_code(primary)
    if matched is not None:
        return matched
    aliased = _alias_to_supported(primary)
    if aliased is not None:
        return aliased

    return STT_LANGUAGE_DEFAULT


def language_for_api(stored: str) -> str:
    """Concrete language code to send on the STT wire.

    Resolves :data:`STT_LANGUAGE_AUTO` from the process locale; never returns
    ``"auto"``. Use this immediately before opening the STT connection.
    """
    canonical = canonicalize_stt_language(stored)
    if canonical == STT_LANGUAGE_AUTO:
        resolved = _system_stt_language()
        return resolved if resolved is not None else STT_LANGUAGE_DEFAULT
    return canonical


def _system_stt_language() -> str | None:
    """Best-effort system locale → supported STT code (``None`` if unset/unsupported).

    POSIX precedence (``LC_ALL`` > ``LC_MESSAGES`` > ``LANG``), treating
    set-but-empty vars as unset — an empty ``LC_ALL`` must not mask a usable
    ``LANG``. ``C`` / ``POSIX`` locales resolve to ``None``.
    """
    loc: str | None = None
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(var)
        if val:
            loc = val
            break
    if loc is None:
        return None
    if loc.lower() in ("c", "posix"):
        return None
    primary = _primary_language_subtag(loc)
    matched = _match_supported_code(primary)
    if matched is not None:
        return matched
    return _alias_to_supported(primary)


# Split on the first BCP-47 / POSIX locale separator (``_`` / ``-`` / ``.``).
_SEP_PATTERN = re.compile(r"[_\-.]")


def _primary_language_subtag(raw: str) -> str:
    """First subtag of a BCP-47 / POSIX locale string."""
    return _SEP_PATTERN.split(raw, maxsplit=1)[0].strip()


def _match_supported_code(raw: str) -> str | None:
    """Case-insensitive exact match against a catalog code (returns the code)."""
    lowered = raw.lower()
    for lang in STT_LANGUAGES:
        if lang.code.lower() == lowered:
            return lang.code
    return None


def _alias_to_supported(primary: str) -> str | None:
    """Map common non-catalog primaries onto a supported code.

    Tagalog (``tl``) is the usual system locale; the API uses Filipino (``fil``).
    """
    if primary.lower() == "tl":
        return "fil"
    return None
