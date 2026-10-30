"""JSON-RPC 2.0 message envelopes.

We model the wire format with Pydantic so we get validation for free
and the type stubs double as documentation.

Convention
----------
- ``Request``:    has ``id`` and ``method``
- ``Response``:   has ``id`` and either ``result`` or ``error``
- ``Notification``: has ``method`` but no ``id`` (caller does not
  expect a reply)
- ``Event``:      has ``event`` and ``data`` (no id, no method) —
  used for *push* streams like ``agent.message_chunk``

In practice the Rust bridge re-emits the raw JSON to the webview, so
this module is the canonical type reference for the project.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..office_analysis import Chart, Summary
from ..office_xlsx import XlsxSpec
from ..office_xlsx import filename as office_filename


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


McpTransport = Literal["stdio", "sse", "streamable_http"]


class DataBackupListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target_dir: str | None = Field(default=None, min_length=1, max_length=4096)


class ResearchConfigGetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ResearchConfigSetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    search_url: str | None = Field(max_length=2048)
    provider: Literal["searxng", "brave"] = "searxng"
    brave_key: str | None = Field(default=None, max_length=512)
    clear_brave_key: bool = False


class CheckpointRestoreParams(_Base):
    """Optional file selection leaves Git and other snapshot files untouched."""
    checkpoint_id: str = Field(min_length=1)
    cwd: str | None = None
    legacy_source_confirmed: bool | None = None
    untracked_file: str | None = Field(default=None, min_length=1)


class CheckpointUntrackedPreview(_Base):
    path: str
    status: Literal["available", "existing", "missing_snapshot", "unsafe_path"]


class CheckpointRebindParams(BaseModel):
    """Explicit user-authorized checkpoint migration target."""
    model_config = ConfigDict(extra="forbid", strict=True)
    checkpoint_id: str = Field(min_length=1, max_length=128)
    cwd: str = Field(min_length=1, max_length=4096)
    confirm_rebind: Literal[True]
    confirm_trusted_bundle: Literal[True]


class InvokeMcpToolResult(_Base):
    """MCP tool result; structuredContent preserves any JSON value, including null."""
    ok: bool
    server_name: str
    tool_name: str
    text: str | None
    content: list[dict[str, Any]] | None
    isError: bool
    structuredContent: Any = None


class ArtifactListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(min_length=1, max_length=128)


class ArtifactScopedListParams(ArtifactListParams):
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    cursor: str | None = Field(default=None, min_length=1, max_length=8192)
    query: str | None = Field(default=None, min_length=1, max_length=240)


class ArtifactRegisterParams(ArtifactListParams):
    source_path: str = Field(min_length=1, max_length=1024)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_service_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=32, max_length=32)


class ArtifactCandidatesParams(ArtifactListParams):
    run_id: str = Field(min_length=1, max_length=128)
    expected_session_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)


class ArtifactGetParams(ArtifactListParams):
    artifact_id: str = Field(pattern=r"^art_[0-9a-f]{32}$")


class AttachmentListParams(ArtifactListParams):
    """Project association only; caller host/path authority is not accepted."""
    cursor: str | None = Field(default=None, min_length=1, max_length=8192)
    query: str | None = Field(default=None, min_length=1, max_length=240)


class AttachmentXlsxParams(ArtifactListParams):
    session_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    attachment_id: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttachmentExtractXlsxParams(AttachmentXlsxParams):
    sheet_name: str = Field(min_length=1, max_length=31)


class AttachmentReadDocumentParams(ArtifactListParams):
    session_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    attachment_id: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: int = Field(ge=1, le=10000)
    offset: int = Field(ge=0, le=1024 * 1024)


class AttachmentOcrParams(ArtifactListParams):
    session_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    attachment_id: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: Literal["eng", "chi_sim", "eng+chi_sim"]


class AttachmentPdfOcrParams(AttachmentOcrParams):
    page: int = Field(ge=1, le=200)


class DocxBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["paragraph", "table"]
    text: str | None = Field(default=None, max_length=4096)
    rows: list[list[str]] | None = Field(default=None, min_length=1, max_length=100)
    repeat_header: bool = False

    @model_validator(mode="after")
    def check_content(self):
        if self.kind == "paragraph":
            if self.text is None or self.rows is not None or self.repeat_header:
                raise ValueError("Paragraph requires text only")
        else:
            if self.text is not None or not self.rows:
                raise ValueError("Table requires rows only")
            width = len(self.rows[0])
            if (not 1 <= width <= 20 or any(len(row) != width for row in self.rows)
                    or any(len(cell) > 4096 for row in self.rows for cell in row)):
                raise ValueError("Table rows must have equal width, at most 20 columns and 4096 characters per cell")
        return self


class DocxSection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    heading: str = Field(min_length=1, max_length=200)
    blocks: list[DocxBlock] = Field(min_length=1, max_length=100)


class ReportSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    accessed_at: str | None = Field(default=None, min_length=1, max_length=64)
    excerpt: str | None = Field(default=None, max_length=1000)
    excerpt_truncated: bool = False
    title_truncated: bool = False

    @model_validator(mode="after")
    def check_source(self):
        if not self.title.strip() or any(ord(char) < 32 or ord(char) == 127 for char in self.title):
            raise ValueError("Source title must be nonempty single-line text")
        if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in self.url) or "\\" in self.url:
            raise ValueError("Source URL cannot contain whitespace or controls")
        try:
            parsed = urlsplit(self.url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                raise ValueError
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Source URL must be an HTTP(S) address without credentials") from exc
        if self.accessed_at is not None:
            try:
                if len(self.accessed_at) == 10:
                    date.fromisoformat(self.accessed_at)
                else:
                    value = datetime.fromisoformat(self.accessed_at.replace("Z", "+00:00"))
                    if value.tzinfo is None or value.utcoffset() is None:
                        raise ValueError
            except ValueError as exc:
                raise ValueError("Source access time must be an ISO date or timezone-aware timestamp") from exc
        return self


class AttachmentGenerateDocxParams(ArtifactListParams):
    expected_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    filename: str = Field(min_length=6, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    paragraphs: list[str] | None = Field(default=None, min_length=1, max_length=100)
    sections: list[DocxSection] | None = Field(default=None, min_length=1, max_length=50)
    sources: list[ReportSource] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def check_structure(self):
        if (self.paragraphs is None) == (self.sections is None):
            raise ValueError("Provide either paragraphs or sections")
        if self.sections:
            if sum(len(section.blocks) for section in self.sections) > 100:
                raise ValueError("Report exceeds 100 blocks")
            cells = sum(sum(len(row) for row in block.rows or [])
                        for section in self.sections for block in section.blocks)
            if cells > 2000:
                raise ValueError("Report exceeds 2000 table cells")
        return self


class PageExcerptRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page_source_id: str = Field(min_length=1, max_length=128)
    start: int = Field(ge=0, le=8000)
    end: int = Field(ge=1, le=8000)


class DocumentExcerptRef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    attachment_id: str = Field(pattern=r"^att_[0-9a-f]{32}$")
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unit: int = Field(ge=1, le=10000)
    start: int = Field(ge=0, le=10 * 1024 * 1024)
    end: int = Field(ge=1, le=10 * 1024 * 1024)


class AttachmentGenerateWithResearchParams(AttachmentGenerateDocxParams):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    page_source_ids: list[str] = Field(default_factory=list, max_length=30)
    page_excerpt_ranges: list[PageExcerptRange] = Field(default_factory=list, max_length=30)
    document_excerpts: list[DocumentExcerptRef] = Field(default_factory=list, max_length=10)


class AttachmentGenerateReportPairParams(AttachmentGenerateDocxParams):
    filename: str = Field(min_length=1, max_length=115)
    formats: list[Literal["docx", "pdf"]] = Field(default_factory=lambda: ["docx", "pdf"], min_length=1, max_length=2)

    @model_validator(mode="after")
    def check_formats(self):
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Report formats must be unique")
        if self.filename.lower().endswith((".docx", ".pdf")):
            raise ValueError("Use a filename stem without an extension")
        return self


class AttachmentGetParams(ArtifactListParams):
    attachment_id: str = Field(pattern=r"^att_[0-9a-f]{32}$")


class OfficeGenerateParams(ArtifactListParams):
    expected_service_id: str = Field(pattern=r"^svc_[0-9a-f]{32}$")
    request_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    filename: str = Field(min_length=6, max_length=120)
    spec: XlsxSpec

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value):
        return office_filename(value)


class OfficeAnalysisGenerateParams(OfficeGenerateParams):
    summaries: list[Summary] = Field(default_factory=list, max_length=20)
    chart: Chart | None = None
    register_chart_delivery: bool = False


class OfficeListParams(ArtifactListParams):
    cursor: str | None = Field(default=None, min_length=1, max_length=8192)
    query: str | None = Field(default=None, min_length=1, max_length=240)


class OfficeGetParams(ArtifactListParams):
    delivery_id: str = Field(pattern=r"^off_[0-9a-f]{32}$")


class AttachmentParserMetadata(BaseModel):
    """Schema043 fields; v1 preserved. inert_text includes bounded PDF page text."""
    model_config = ConfigDict(extra="forbid", strict=True)
    parser_version: Literal[1, 2]
    parser_scope: Literal["legacy_v1", "inert_text", "docx_body_paragraphs_tables", "original_only"]
    parser_reason: Literal[
        "legacy_v1", "ok", "no_extracted_body_text_no_ocr", "preview_limit",
        "invalid_package", "unsafe_package", "package_limit", "unsupported_feature",
        "missing_dependency", "limits_unavailable", "queue_timeout", "parser_timeout",
        "parser_crash", "output_limit", "invalid_parser_output", "invalid_text",
        "type_mismatch", "unsupported_type", "ocr_not_attempted",
    ]


class SendMessageParams(_Base):
    """Additive task mode; plan is enforced by execution/tool policy."""

    session_id: str | None = None
    content: str | list[dict[str, Any]]
    mode: Literal["chat", "plan", "execute"] = "chat"
    capture_file_changes: bool = False
    restrict_network_tools: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    # M2 — per-run token budget. None defers to the env default
    # (MINIMAX_CODE_RUN_BUDGET_TOKENS); 0 disables budgeting for this
    # run; a positive value caps the run (and its sub-agents).
    budget_tokens: int | None = Field(default=None, ge=0)


class ScheduleContext(_Base):
    """Versioned local Native authority snapshot, built by the scheduler only."""
    version: Literal[1] = 1
    host: Literal["local"] = "local"
    engine: Literal["native"] = "native"
    project_id: str
    workspace: str
    directory_identity: list[int]
    provider_id: str
    protocol: str
    provider_fingerprint: str
    model: str
    reasoning_effort: str | None = None
    mode: Literal["chat", "execute", "plan"]
    budget_tokens: int = Field(gt=0, strict=True)
    permission_policy_version: Literal[1] = 1
    permission_rules: list[dict[str, Any]]
    hooks_policy: Literal["disabled"] = "disabled"
    validate_command: str = ""
    timezone: str = "Asia/Shanghai"
    restart_missed_policy: Literal["coalesce", "skip"] = "coalesce"


class ScheduleRunNowParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    job_id: str
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(gt=0)


class ScheduleOccurrence(_Base):
    id: str
    job_id: str
    revision: int
    source: Literal["cron", "manual"]
    context: ScheduleContext
    session_id: str
    task_id: str
    run_id: str | None = None
    status: str
    scheduled_for: str | None = None
    coalesced_through: str | None = None
    verification: dict[str, Any] | None = None


class PermissionRequestData(_Base):
    request_id: str
    tool: str
    args: dict[str, Any]
    session_id: str | None = None
    approval_policy: Literal["explicit_once"] | None = None


class RunStartParams(SendMessageParams):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    # M4 — engine pinned at admission. "codex" routes to the Codex
    # App Server adapter; unknown/unavailable values are named in the
    # error instead of silently falling back to native.
    engine: Literal["native", "codex"] | None = None


class RunIdParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)


class ScheduledRunCancelScope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    occurrence_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    revision: int = Field(ge=1)


class RunCancelParams(RunIdParams):
    schedule_scope: ScheduledRunCancelScope | None = None


class RunSteerParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=8000)


class RunChildWorkspaceParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    team_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    action: Literal["preview", "collect", "resolve_text", "read_backup"]
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    content: str | None = Field(default=None, max_length=16384)
    on_conflict: Literal["fail", "skip"] = "fail"
    attempt_id: str | None = Field(default=None, min_length=1, max_length=128)
    revision: str | None = Field(default=None, min_length=64, max_length=64)
    expected_service_id: str | None = Field(default=None, min_length=1, max_length=128)
    confirmed_team_stopped: bool = False
    expected_team_step_id: str | None = Field(default=None, min_length=1, max_length=128)


class RunChildListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)


class RunVerifyCollectedParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)
    team_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    child_run_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=64, max_length=64)
    expected_command: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=128)
    confirmed: Literal[True]


class RunOutputPageParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    output_ref: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class RunOutputInventoryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)
    offset: int = Field(default=0, ge=0, le=10000)


class RunOutputClearParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    output_ref: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttachmentGeneratePairWithResearchParams(AttachmentGenerateReportPairParams):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)
    page_source_ids: list[str] = Field(default_factory=list, max_length=30)
    page_excerpt_ranges: list[PageExcerptRange] = Field(default_factory=list, max_length=30)
    document_excerpts: list[DocumentExcerptRef] = Field(default_factory=list, max_length=10)


class RuntimeWorkspaceCheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)
    project_id: str | None


class RunResearchListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    query: str = Field(default="", max_length=200)
    offset: int = Field(default=0, ge=0, le=10000)


class RunProjectSummaryParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    project_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    history_query: str | None = Field(default=None, max_length=200)
    history_offset: int = Field(default=0, ge=0, le=10000)


class RunResearchSearchParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=500)


class RunResearchReadParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=128)
    project_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=4096)
    focus: str | None = Field(default=None, min_length=1, max_length=200)


class RunResearchGetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    expected_service_id: str = Field(min_length=1)
    page_source_id: str = Field(min_length=1, max_length=128)


class ContinueRunParams(_Base):
    """Pin retries to the same truncated block; omission keeps legacy latest selection."""

    session_id: str = Field(min_length=1)
    run_id: str | None = Field(default=None, min_length=1)


class RunRecoveryLookupParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    project_id: str | None = Field(max_length=128)
    service_id: str = Field(min_length=1, max_length=128)


class RunResumeParams(RunIdParams):
    idempotency_key: str = Field(min_length=1, max_length=128)
    reviewed: bool = False
    handoff: str = Field(default="", max_length=4000)
    # None preserves the original cumulative cap, including an unset cap.
    # A supplied value may preserve or raise it; the ledger rejects reductions.
    budget_tokens: int | None = Field(default=None, ge=0)
    expected_service_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_project_id: str | None = Field(default=None, max_length=128)
    expected_cursor: int | None = Field(default=None, ge=0, le=2**53 - 1)


class BudgetGetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str | None = None
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class BudgetSettingsGetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class BudgetSettingsSetParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_service_id: str = Field(min_length=1, max_length=128)
    limit_tokens: int | None = Field(gt=0, le=2**53 - 1)
    expected_saved_limit: int | None = Field(default=None, gt=0, le=2**53 - 1)


class BudgetReconcileParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: Literal["day", "family", "attempt"]
    target_id: str = Field(min_length=1)
    liability_tokens: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    acknowledged: Literal[True]


class VerificationEvidence(_Base):
    version: Literal[1] = 1
    verdict: Literal["passed", "failed", "unknown", "not_applicable"] = "unknown"
    reason: str = "legacy_unverified"
    requires_review: bool = False
    tool_calls: int = 0
    failed_tool_calls: int = 0
    failures: list[dict[str, str]] = Field(default_factory=list)
    validation: dict[str, Any] | None = None


class SendMessageResult(_Base):
    session_id: str
    message_id: str
    text: str
    run_id: str | None = None
    cancelled: bool = False
    iterations: int | None = None
    # Missing engine usage is unknown, never inferred to be zero.
    tokens_in: int | None = None
    tokens_out: int | None = None
    run_status: Literal["completed", "failed", "interrupted", "cancelled", "budget_paused"] | None = None
    external_turn_id: str | None = None
    usage_certainty: Literal["unknown", "observed", "uncertain", "recovery_interval"] | None = None
    tokens_total: int | None = None
    verification: VerificationEvidence | None = None
    # Derived from the authoritative saved v3 profile; never request overrides.
    execution_target: dict[str, Any] | None = None
    execution_target_hash: str | None = None
    execution_installation_id: str | None = None


class MessageMetadata(_Base):
    """Persisted and streamed metadata; old messages may omit run linkage."""

    run_id: str | None = None
    run_status: str | None = None
    engine: Literal["native", "codex"] | None = None
    external_turn_id: str | None = None
    status: str | None = None
    partial: bool = False
    usage_certainty: str | None = None
    verification: VerificationEvidence | None = None
    thinking_count: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class RunStreamContext(_Base):
    """Chat notification context; older emitters may omit the durable run ID."""

    session_id: str
    run_id: str | None = None


class ChildWorkspaceSummary(_Base):
    """Optional result field on real sub-agent and team member execution."""

    run_id: str
    path: str
    kind: Literal["git_worktree", "directory"]
    state: Literal["preparing", "running", "completed", "failed", "cancelled", "needs_review", "collected"]
    files_written: list[str] = Field(default_factory=list)
    omitted: list[str] = Field(default_factory=list)
    collection_required: bool = True


class PreviewAccessResult(_Base):
    """HTTP-only read capability; never a general application auth token."""

    path: str
    expires_in: int = Field(ge=0, le=900)


class RunEventsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)
    after_seq: int = Field(default=0, ge=0, le=2**53 - 1)
    limit: int = Field(default=100, ge=1, le=500)


class NativeModelSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["native"] = "native"
    provider_id: str | None
    model: str
    protocol: str | None
    reasoning_effort: str | None
    context_window: int


class RunEvent(_Base):
    id: str
    run_id: str
    seq: int = Field(ge=1)
    type: Literal["created", "status", "step_created", "step_updated", "recovered", "budget"]
    created_at: str
    data: dict[str, Any]


class RunEventsResult(_Base):
    events: list[RunEvent]
    next_seq: int = Field(ge=0)
    has_more: bool


class RunSnapshotResult(_Base):
    run: dict[str, Any]
    steps: list[dict[str, Any]]
    cursor: int = Field(ge=0)
    pending_ask_user: dict[str, Any] | None = None
    pending_ask_users: list[dict[str, Any]] = Field(default_factory=list)
    pending_native_permissions: list[dict[str, Any]] = Field(default_factory=list)
    pending_codex_permissions: list[dict[str, Any]] = Field(default_factory=list)
    live_stream: dict[str, Any] | None = None


# ---- Error object ---------------------------------------------------------


class HostServiceStartParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host_id: str = Field(min_length=1, max_length=128)
    expected_service_id: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=255)
    username: str = Field(min_length=1, max_length=128)
    port: int = Field(ge=1, le=65535)
    identity_file: str = Field(max_length=4096)
    remote_port: int = Field(ge=1, le=65535)
    unit: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.service$")


class HostTransferStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host_id: str = Field(min_length=1)
    remote_path: str = Field(min_length=1)
    transfer_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class HostPushParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host_id: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    remote_path: str = Field(min_length=1)
    transfer_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class HostTransferResult(_Base):
    ok: bool
    exit_code: int | None
    stderr: str
    remote_path: str
    transfer_id: str | None = None
    publication_state: Literal["staged", "publishing", "published", "aborted", "unknown", "not_published"] | None = None
    backup_path: str | None = None


class RPCError(_Base):
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any | None = None


# ---- Standard error codes -------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-defined range (-32000 to -32099 reserved by spec).
TOOL_EXECUTION_ERROR = -32001
PERMISSION_DENIED = -32002
LLM_ERROR = -32003
STORAGE_ERROR = -32004
NOT_IMPLEMENTED = -32005
NOT_FOUND = -32006


# ---- Outbound (Python -> stdio) ------------------------------------------


class Response(_Base):
    """Reply to a previously-issued request."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    result: Any = None
    error: RPCError | None = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def to_bytes(self) -> bytes:
        return self.to_line().encode("utf-8") + b"\n"


class Notification(_Base):
    """Outbound notification (no reply expected)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def to_bytes(self) -> bytes:
        return self.to_line().encode("utf-8") + b"\n"


class Event(_Base):
    """Push event for streamed responses.

    Example::

        {"event": "agent.message_chunk",
         "data": {"session_id": "abc", "delta": "Hi", "done": false}}
    """

    jsonrpc: Literal["2.0"] = "2.0"
    event: str
    data: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def to_bytes(self) -> bytes:
        return self.to_line().encode("utf-8") + b"\n"


# ---- Inbound (stdio -> Python) -------------------------------------------


class Request(_Base):
    """JSON-RPC 2.0 request from the frontend."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: str
    params: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)


class AgentSkillsConfig(_Base):
    """Ordered dependency declarations; executable binding is server-only."""
    model_config = ConfigDict(extra="forbid", strict=True)
    skills: list[str]

    @field_validator("skills")
    @classmethod
    def valid_skills(cls, value):
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("skills require unique non-blank IDs or names")
        return value


class AgentExecutionProfileConfig(_Base):
    """Persistent configured ID; no per-invocation execution override."""
    model_config = ConfigDict(extra="forbid", strict=True)
    execution_profile_id: str | None = Field(default=None, min_length=1, max_length=64,
                                             pattern=r"^[a-z][a-z0-9_-]*$")


class ExecutionProfileCapability(_Base):
    id: str
    label: str
    mode: Literal["host", "podman"]
    available: bool
    reason: str | None = None
    unattended_supported: Literal[False] = False
    boundary_summary: str


class AgentInvokeParams(_Base):
    """Public root intent; family, instructions and execution IDs are private."""
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    request: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    agent_id: str | None = None
    parent_session_id: str | None = None
    context_message_id: str | None = None
    display_name: str | None = None
    run_id: str | None = None
    model: str | None = None
    budget_tokens: int | None = Field(default=None, gt=0)
    admission_only: bool = False

    @field_validator("name", "request", "session_id", "idempotency_key")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A non-blank string is required")
        return value


class SkillInvokeParams(_Base):
    """Client intent only; executable skill authority is resolved server-side."""
    model_config = ConfigDict(extra="forbid", strict=True)
    skill_id: str = Field(min_length=1)
    request: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    diff: str | None = None
    model: str | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=100)
    budget_tokens: int | None = Field(default=None, gt=0)
    expected_workspace: str | None = None
    label: Literal["overview", "security", "performance", "style"] | None = None
    admission_only: bool = False
    expected_service_id: str | None = Field(default=None, min_length=1, max_length=128)
    expected_project_id: str | None = None

    @field_validator("skill_id", "request", "session_id", "idempotency_key")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A non-blank string is required")
        return value


class InboundNotification(_Base):
    """JSON-RPC 2.0 notification from the frontend (no reply expected)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)


# ---- Helpers --------------------------------------------------------------


def parse_envelope(line: str) -> dict[str, Any]:
    """Parse a single line into its raw JSON object.

    Raises ``ValueError`` on malformed input. Callers should wrap with
    :class:`RPCError` to surface the failure to the frontend.
    """
    line = line.strip()
    if not line:
        raise ValueError("empty line")
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def make_error_response(req_id: str | int | None, code: int, message: str, data: Any = None) -> Response:
    return Response(
        id=req_id if req_id is not None else 0,
        error=RPCError(code=code, message=message, data=data),
    )


__all__ = [
    "Event",
    "InboundNotification",
    "Notification",
    "Request",
    "Response",
    "RPCError",
    "parse_envelope",
    "make_error_response",
    # Standard codes
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    # App codes
    "TOOL_EXECUTION_ERROR",
    "PERMISSION_DENIED",
    "LLM_ERROR",
    "STORAGE_ERROR",
    "NOT_IMPLEMENTED",
]
