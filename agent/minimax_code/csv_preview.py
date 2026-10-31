"""Read-only, bounded CSV preview with an explicit dialect."""
from __future__ import annotations

import csv
import io


def preview_csv(data: bytes, delimiter: str) -> dict:
    separators = {"comma": ",", "semicolon": ";", "tab": "\t"}
    if delimiter not in separators or len(data) > 1024 * 1024:
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
        size = sum(len(value.encode("utf-8")) for value in header)
        if size > 65536:
            raise ValueError("CSV header exceeds preview limit")
        rows, count, truncated = [], 0, False
        for values in reader:
            count += 1
            if count > 1000 or (count + 1) * len(header) > 5000:
                raise ValueError("CSV exceeds row or cell limit")
            if len(values) != len(header) or any(len(value) > 4096 for value in values):
                raise ValueError("CSV row width or cell size is invalid")
            row_size = sum(len(value.encode("utf-8")) for value in values)
            if not truncated and len(rows) < 100 and size + row_size <= 65536:
                rows.append(values)
                size += row_size
            else:
                truncated = True
        return {"delimiter": delimiter, "headers": header, "rows": rows,
                "total_rows": count, "truncated": truncated}
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError("CSV is malformed or not UTF-8") from exc
