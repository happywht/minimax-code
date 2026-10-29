"""Conservative interpretation of stored numeric formats for XLSX import."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from .attachment_docx import require

_DATE_IDS = {14, 15, 16, 17, 22}
_NUMERIC_IDS = {*range(1, 14), *range(37, 45), 48, 49}
_DATE = re.compile(
    r"(?:y{4}[-/]m{1,2}[-/]d{1,2}|m{1,2}[-/]d{1,2}[-/]y{2,4}|d{1,2}[-/]m{1,2}[-/]y{2,4})"
    r"(?:[ t]h{1,2}:m{1,2}(?::s{1,2}(?:\.0{1,3})?)?)?\Z", re.IGNORECASE)


def _custom_numeric(code):
    # Strip quoted and escaped display literals, but do not interpret them as values.
    output, i, currency = [], 0, False
    while i < len(code):
        char = code[i]
        if char == '"':
            end = code.find('"', i + 1)
            require(end != -1, "formatted_number")
            literal = code[i + 1:end].strip()
            require(not literal or literal.upper() in {"USD", "CNY", "RMB", "EUR", "GBP", "JPY"}
                    or all(unicodedata.category(c) == "Sc" for c in literal), "formatted_number")
            currency |= bool(literal)
            i = end + 1
        elif char in "\\\\_*":
            require(i + 1 < len(code), "formatted_number")
            require(not (char == "\\" and code[i + 1].isdigit()), "padded_identifier")
            currency |= char == "\\" and unicodedata.category(code[i + 1]) == "Sc"
            i += 2
        elif char == "[":
            end = code.find("]", i + 1)
            require(end != -1, "formatted_number")
            value = code[i + 1:end]
            color = value.lower() in {"black", "blue", "cyan", "green", "magenta", "red", "white", "yellow"}
            currency_marker = re.fullmatch(r"\$[^\d\s\]\-]{0,4}(?:-[0-9a-fA-F]{1,8})?", value) is not None
            require(color or currency_marker, "formatted_number")
            currency |= currency_marker and bool(value[1:].split("-", 1)[0])
            i = end + 1
        else:
            output.append(char)
            i += 1
    simple = "".join(output)
    require(any(c in simple for c in "0#?"), "formatted_number")
    for section in simple.split(";"):
        integer = re.split(r"[.Ee]", section, maxsplit=1)[0]
        require(re.search(r"(?<!0)0{2,}", integer) is None, "padded_identifier")
    require(all(c in "0#?,.%() +-;/Ee" or unicodedata.category(c) == "Sc" for c in simple), "formatted_number")
    currency |= any(unicodedata.category(c) == "Sc" for c in simple)
    percent = "%" in simple
    require(not (currency and percent), "formatted_number")
    return "currency" if currency else "percentage" if percent else "number"


def number_kind(format_id, custom_formats):
    if format_id in _DATE_IDS:
        return "date"
    if format_id in {9, 10}:
        return "percentage"
    if format_id in {5, 6, 7, 8, 42, 44}:
        return "currency"
    if format_id == 0 or format_id in _NUMERIC_IDS:
        return "number"
    code = custom_formats.get(format_id)
    require(code is not None and 0 < len(code) <= 512, "formatted_number")
    if _DATE.fullmatch(code):
        return "date"
    return _custom_numeric(code)


def excel_date(raw, epoch):
    try:
        value = Decimal(raw)
        require(value.is_finite() and 0 <= value <= 2958466, "unsupported_date")
        days = int(value)
        require(days <= 2958465, "unsupported_date")
        if epoch == "1900":
            require(days >= 1 and days != 60, "unsupported_date")
            base = datetime(1899, 12, 31)
            days -= int(days > 60)
        else:
            base = datetime(1904, 1, 1)
        milliseconds = (value - int(value)) * Decimal(86400000)
        rounded = milliseconds.to_integral_value(rounding=ROUND_HALF_UP)
        require(abs(milliseconds - rounded) <= Decimal("0.001"), "unsupported_date")
        result = base + timedelta(days=days, milliseconds=int(rounded))
        if result.time().isoformat() == "00:00:00":
            return result.date().isoformat()
        return result.isoformat(timespec="milliseconds" if result.microsecond else "seconds")
    except (InvalidOperation, ValueError, OverflowError) as exc:
        if hasattr(exc, "reason"):
            raise
        require(False, "unsupported_date")
