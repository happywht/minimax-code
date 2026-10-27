"""Authenticated bounded raw upload and original download; no multipart parser."""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from .attachments import MAX_BYTES, download_name, get_attachment_service
from .ipc.protocol import AttachmentListParams

logger = logging.getLogger(__name__)


def _error(message, status):
    return JSONResponse({"error": message}, status_code=status, headers={"Cache-Control": "no-store"})


def register_attachment_routes(app):
    @app.post("/attachments")
    async def upload(request: Request):
        # ApplicationBoundary authenticates Host/Origin/bearer/cookie before body reads.
        if (set(request.query_params) != {"project_id", "filename"}
                or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
            return _error("Only one project_id and filename are accepted", 400)
        expected = request.headers.getlist("x-attachment-service-id")
        if len(expected) != 1 or len(expected[0]) > 128:
            return _error("X-Attachment-Service-Id is required", 400)
        lengths = request.headers.getlist("content-length")
        if lengths and (len(lengths) != 1 or len(lengths[0]) > 20
                        or not lengths[0].isascii() or not lengths[0].isdigit()):
            return _error("Invalid Content-Length", 400)
        if lengths and int(lengths[0]) > MAX_BYTES:
            return _error("Attachment exceeds 10 MiB", 413)
        try:
            params = AttachmentListParams.model_validate({"project_id": request.query_params["project_id"]})
            service = await get_attachment_service()
            result = await service.upload(params.project_id, request.query_params["filename"],
                                          expected[0], request.stream())
        except OverflowError:
            return _error("Attachment exceeds 10 MiB", 413)
        except TimeoutError:
            return _error("Attachment upload deadline exceeded", 408)
        except ClientDisconnect:
            return _error("Attachment upload interrupted", 400)
        except ValidationError:
            return _error("Invalid attachment project", 400)
        except (ValueError, OSError) as exc:
            return _error(str(exc), 409)
        except Exception:
            logger.exception("Attachment upload failed")
            return _error("Attachment storage unavailable", 503)
        return JSONResponse({"attachment": result}, status_code=201, headers={"Cache-Control": "no-store"})

    @app.get("/attachments/{attachment_id}/content")
    async def content(attachment_id: str, request: Request, project_id: str):
        if (set(request.query_params) != {"project_id"}
                or len(request.query_params.getlist("project_id")) != 1):
            return _error("Only project_id is accepted", 400)
        try:
            AttachmentListParams.model_validate({"project_id": project_id})
            service = await get_attachment_service()
            row, data = await service.content(project_id, attachment_id)
        except ValidationError:
            return _error("Invalid attachment project", 400)
        except (ValueError, OSError) as exc:
            return _error(str(exc), 409)
        except Exception:
            logger.exception("Attachment download failed")
            return _error("Attachment storage unavailable", 503)
        name = quote(download_name(row["original_name"]), safe="")
        return Response(data, media_type="application/octet-stream", headers={
            "Content-Disposition": f"attachment; filename=attachment.bin; filename*=UTF-8''{name}",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Attachment-SHA256": row["sha256"], "X-Attachment-Service-Id": row["service_id"],
        })
