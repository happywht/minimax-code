"""Explicit authenticated operator API, not a model tool or run admission."""
from __future__ import annotations

import asyncio
import hashlib
import logging

from pydantic import ValidationError

from ..office_deliveries import get_office_service
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    OfficeListParams,
    OfficeGenerateParams,
    OfficeAnalysisGenerateParams,
    OfficeGetParams,
)

logger = logging.getLogger(__name__)


def register_office_handlers(server):
    async def generate_analysis(params, ctx):
        from ..execution import require_write_access
        from ..office_analysis import AnalysisSpec, materialize
        from ..office_deliveries import TOTAL_SECONDS, _slot
        from ..office_process import generate_chart_xlsx
        from ..storage.dao.attachments import metadata

        try:
            request = OfficeAnalysisGenerateParams.model_validate(params)
            require_write_access()
            original = request.spec
            combined = materialize(original, request.summaries)
            service = await get_office_service()
            plain = OfficeGenerateParams.model_validate({**request.model_dump(exclude={"summaries", "chart", "register_chart_delivery"}),
                                                          "spec": combined.model_dump()})
            analysis = None
            if request.chart is not None:
                names = {column.name for column in original.columns}
                if request.chart.category_column not in names or request.chart.value_column not in names:
                    raise ValueError("Chart columns must belong to the original data")
                analysis = AnalysisSpec(spec=combined, chart=request.chart, source_rows=len(original.rows))
            if analysis is not None and not request.register_chart_delivery:
                if request.operator_notes is not None:
                    raise ValueError("Operator notes require a registered chart delivery")
                if request.run_id is not None:
                    raise ValueError("Chart attachments cannot be associated with a run")
                async with asyncio.timeout(TOTAL_SECONDS), _slot():
                    project = await service._identity(request.project_id, request.expected_service_id)
                    data, report = await generate_chart_xlsx(analysis)

                    async def chunks():
                        yield data

                    row = await service.originals.upload(request.project_id, request.filename,
                        request.expected_service_id, chunks(), require_parsed=True)
                    if (await service._identity(request.project_id, request.expected_service_id) != project or
                            row["sha256"] != hashlib.sha256(data).hexdigest() or
                            report["file_sha256"] != row["sha256"] or report["size_bytes"] != row["size_bytes"]):
                        raise ValueError("Chart publication identity changed; inspect project attachments")
                    await ctx.reply({"kind": "attachment", "attachment": metadata(row), "validation_report": report,
                                     "analysis_spec": analysis.model_dump()})
                    return
            result = await service.generate(plain, analysis=analysis,
                request_context=request.model_dump(exclude={"request_key", "operator_notes"}) if analysis else None)
            await ctx.reply({"kind": "delivery", **result, "materialized_spec": combined.model_dump(),
                             **({"analysis_spec": analysis.model_dump()} if analysis else {})})
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "Invalid table, summary or chart parameters")
        except (ValueError, PermissionError) as error:
            await ctx.reply_error(-32000, str(error))
        except (OSError, TimeoutError):
            await ctx.reply_error(-32000, "Office generation unavailable; inspect project attachments before retrying legacy charts")
        except Exception as error:
            logger.warning("Office analysis operation failed (%s)", type(error).__name__)
            await ctx.reply_error(INTERNAL_ERROR, "Office generation failed; inspect project attachments before retrying legacy charts")

    def handler(model, operation):
        async def handle(params, ctx):
            try:
                request = model.model_validate(params)
                service = await get_office_service()
                result = (await service.generate(request) if operation == "generate"
                          else await getattr(service, operation)(**request.model_dump()))
                await ctx.reply(result)
            except ValidationError:
                await ctx.reply_error(INVALID_PARAMS, "Invalid plain-cell XLSX parameters")
            except (ValueError, PermissionError) as error:
                await ctx.reply_error(-32000, str(error))
            except (OSError, TimeoutError):
                await ctx.reply_error(-32000, "Office storage or generation deadline unavailable; refresh before retry")
            except Exception as error:
                # Never log inputs, document values, response bodies or exception text.
                logger.warning("Office operation failed (%s)", type(error).__name__)
                await ctx.reply_error(INTERNAL_ERROR, "Office operation failed; refresh before retry")
        return handle

    server.register("office.generate_xlsx", handler(OfficeGenerateParams, "generate"))
    server.register("office.generate_analysis", generate_analysis)
    server.register("office.list", handler(OfficeListParams, "list"))
    server.register("office.get", handler(OfficeGetParams, "get"))
