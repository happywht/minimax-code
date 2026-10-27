"""Authenticated original-byte downloads under the main application boundary."""
from __future__ import annotations

import logging
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .text_artifacts import get_artifact_service

logger = logging.getLogger(__name__)


def register_artifact_routes(app):
    @app.get("/artifacts/{artifact_id}/content")
    async def content(artifact_id: str, request: Request, project_id: str):
        # ApplicationBoundary already authenticates Host/Origin/bearer/cookie.
        # Query strings may contain only the association, never an auth token.
        if (set(request.query_params) != {"project_id"}
                or len(request.query_params.getlist("project_id")) != 1):
            return JSONResponse({"error": "Only project_id is accepted"}, status_code=400)
        try:
            service = await get_artifact_service()
            row, data = await service.content(project_id, artifact_id)
        except (ValueError, OSError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409,
                                headers={"Cache-Control": "no-store"})
        except Exception:
            logger.exception("Artifact download failed")
            return JSONResponse({"error": "Artifact storage unavailable"}, status_code=503)
        name = quote(PurePosixPath(row["source_path"]).name, safe="")
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename=artifact.txt; filename*=UTF-8''{name}",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Artifact-SHA256": row["sha256"],
            "X-Artifact-Service-Id": row["service_id"],
        })
