"""Read-only, bounded CSV preview with an explicit dialect."""
from __future__ import annotations

import csv
import io
import json


def _parse_csv(data: bytes, delimiter: str) -> tuple[list[str], list[list[str]]]:
    separators = {"comma": ",", "semicolon": ";", "tab": "\t"}
    if not isinstance(delimiter, str) or delimiter not in separators or len(data) > 1024 * 1024:
        raise ValueError("Invalid delimiter or CSV exceeds 1 MiB")
    try:
        text = data.decode("utf-8-sig", errors="strict")
        if "\0" in text:
            raise ValueError("CSV contains NUL")
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=separators[delimiter], strict=True)
        header = next(reader, None)
        if not header or len(header) > 100 or any(not value.strip() for value in header) or len(set(header)) != len(header):
            raise ValueError("CSV needs 1–100 unique nonempty column names")
        if any(len(value) > 4096 for value in header):
            raise ValueError("CSV cell exceeds 4096 characters")
        rows = []
        for values in reader:
            if len(rows) >= 1000 or (len(rows) + 2) * len(header) > 5000:
                raise ValueError("CSV exceeds row or cell limit")
            if len(values) != len(header) or any(len(value) > 4096 for value in values):
                raise ValueError("CSV row width or cell size is invalid")
            rows.append(values)
        return header, rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("CSV is malformed or not UTF-8") from exc


def preview_csv(data: bytes, delimiter: str) -> dict:
    header, all_rows = _parse_csv(data, delimiter)
    size = sum(len(value.encode("utf-8")) for value in header)
    if size > 65536:
        raise ValueError("CSV header exceeds preview limit")
    rows = []
    for values in all_rows:
        row_size = sum(len(value.encode("utf-8")) for value in values)
        if len(rows) >= 100 or size + row_size > 65536:
            break
        rows.append(values)
        size += row_size
    return {"delimiter": delimiter, "headers": header, "rows": rows,
            "total_rows": len(all_rows), "truncated": len(rows) < len(all_rows)}


def read_csv_page(data: bytes, delimiter: str, row_offset: int) -> dict:
    header, rows = _parse_csv(data, delimiter)
    if type(row_offset) is not int or not 0 <= row_offset <= len(rows):
        raise ValueError("CSV row offset is outside the data rows")
    page: list[list[str]] = []
    def wire_size(values):
        return len(json.dumps({"headers": header, "rows": values}, ensure_ascii=True).encode("utf-8"))
    if wire_size(page) > 16384:
        raise ValueError("CSV header exceeds the 16 KiB page limit")
    for row in rows[row_offset:row_offset + 50]:
        if wire_size([*page, row]) > 16384:
            if not page:
                raise ValueError("CSV row exceeds the 16 KiB page limit; no rows were omitted")
            break
        page.append(row)
    end = row_offset + len(page)
    return {"delimiter": delimiter, "headers": header, "rows": page, "row_offset": row_offset,
            "total_rows": len(rows), "next_row_offset": end if end < len(rows) else None}
