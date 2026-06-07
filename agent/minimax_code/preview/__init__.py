"""Live preview server — static file serving + SSE hot-reload.

Started alongside the main agent on a separate port (default 8766).
Provides:
  - ``GET /preview/<path>`` — serves workspace files with content-type sniffing
  - ``GET /preview/events``  — SSE stream of file-change events
  - ``GET /preview/health``  — liveness probe
"""
from __future__ import annotations
