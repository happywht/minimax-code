"""Fixed Office generator bootstrap: resource gate before input or third parties."""
from __future__ import annotations

import os
import sys


def main():
    try:
        if len(sys.argv) < 4 or sys.argv[-3] != "xlsx_generate":
            raise RuntimeError("Invalid fixed operation")
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _attachment_parser_worker import establish_limits

        job = establish_limits()
    except Exception:
        return 126
    assert job or sys.platform == "linux"
    sys.path.extend(sys.argv[-2:])
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import base64
    import json

    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        return 127
    try:
        from minimax_code.office_xlsx import XlsxSpec, generate, verify

        spec = XlsxSpec.model_validate_json(raw)
        data = generate(spec)
        report = verify(data, spec)
        result = {"data_base64": base64.b64encode(data).decode("ascii"), "report": report}
    except ImportError:
        result = {"error": "missing_dependency"}
    except BaseException:
        # Document values, paths and library exception text never cross this boundary.
        result = {"error": "generation_failed"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=True, allow_nan=False).encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
