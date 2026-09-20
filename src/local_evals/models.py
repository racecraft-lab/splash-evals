"""Typed records shared by the local-only evaluation core.

The records in this module are deliberately immutable.  Runtime code may build a
new record when state advances, but callers cannot silently rewrite discovery or
rollback evidence after it has been captured.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class FrozenModel(BaseModel):
    """Strict immutable base for evidence records."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class LocalityStatus(StrEnum):
    """Whether model execution, not merely the HTTP listener, is local."""

    VERIFIED_LOCAL = "verified_local"
    AMBIGUOUS_LM_LINK = "ambiguous_lm_link"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    UNKNOWN = "unknown"


class SettingEvidenceStatus(StrEnum):
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    VERIFIED = "verified"
    UNKNOWN = "unknown"


class ModelRecordSource(StrEnum):
    UNKNOWN = "unknown"
    LMS_CLI_LOADED = "lms_cli_loaded"
    LMS_CLI_DOWNLOADED = "lms_cli_downloaded"
    NATIVE_REST = "native_rest"
    OPENAI_COMPAT = "openai_compat"


class RestoreStatus(StrEnum):
    READY = "ready"
    NO_CHANGES = "no_changes"
    BLOCKED_INTERVENING_CHANGE = "blocked_intervening_change"


class QualifiedEndpoint(FrozenModel):
    url: str
    host: str
    port: int
    resolved_addresses: tuple[str, ...]


class LocalityEvidence(FrozenModel):
    status: LocalityStatus
    endpoint_loopback: bool
    lm_link_state: str = "unknown"
    local_instance_evidence: bool = False
    execution_device: str | None = None
    reasons: tuple[str, ...] = ()


class CapabilityRecord(FrozenModel):
    name: str
    status: CapabilityStatus = CapabilityStatus.UNKNOWN
    interface: str | None = None
    evidence: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class ModelRecord(FrozenModel):
    """Non-sensitive identity for a downloaded or loaded model."""

    key: str
    source: ModelRecordSource = ModelRecordSource.UNKNOWN
    instance_id: str | None = None
    display_name: str | None = None
    publisher: str | None = None
    architecture: str | None = None
    model_format: str | None = None
    model_type: str | None = None
    quantization: str | None = None
    selected_variant: str | None = None
    file_revision: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    loaded: bool = False
    capabilities: tuple[CapabilityRecord, ...] = ()


class DiscoveryIssue(FrozenModel):
    source: str
    code: str
    detail: str
    blocking: bool = False


class DiscoveryReport(FrozenModel):
    schema_version: int = 1
    collected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    endpoint: QualifiedEndpoint
    locality: LocalityEvidence
    cli_version: str | None = None
    app_version: str | None = None
    models: tuple[ModelRecord, ...] = ()
    capabilities: tuple[CapabilityRecord, ...] = ()
    issues: tuple[DiscoveryIssue, ...] = ()
    source_status: dict[str, str] = Field(default_factory=dict)


class ServerConfig(FrozenModel):
    origin: str = "http://127.0.0.1:1234"
    openai_base_url: str = "http://127.0.0.1:1234/v1"
    api_key_env: str = "LM_STUDIO_API_KEY"
    require_local_execution: bool = True


class ModelConfig(FrozenModel):
    key: str | None = None
    instance_id: str | None = None
    selected_variant: str | None = None
    file_revision: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    execution_device: str | None = None


class ExecutionConfig(FrozenModel):
    max_in_flight_requests: int = Field(default=1, ge=1, le=1)
    transport_retries: int = Field(default=0, ge=0, le=0)
    response_cache: str = "disabled"


class EvaluationConfig(FrozenModel):
    schema_version: int = 2
    id: str
    status: str = "unvalidated"
    server: ServerConfig = Field(default_factory=ServerConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    load_requested: dict[str, Any] = Field(default_factory=dict)
    operation_requested: dict[str, Any] = Field(default_factory=dict)
    reasoning_control: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)

    @field_validator("id")
    @classmethod
    def non_empty_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("configuration id must not be empty")
        return value


class SettingEvidenceRecord(FrozenModel):
    """Keep intent, wire representation, and observed state non-equivalent."""

    name: str
    requested: Any = None
    transmitted: Any = None
    effective: Any = None
    status: SettingEvidenceStatus = SettingEvidenceStatus.UNKNOWN
    evidence: tuple[str, ...] = ()


class InferenceUsage(FrozenModel):
    """Provider-reported counts. Missing usage remains unknown, never zero."""

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class SecretReference(FrozenModel):
    """A reference to a secret source; the secret value is never serialized."""

    name: str
    source: str = "environment"
    value: SecretStr | None = Field(default=None, exclude=True, repr=False)


class StateSnapshot(FrozenModel):
    schema_version: int = 1
    snapshot_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    settings: dict[str, Any]
    settings_digest: str
    source_fingerprint: str
    secret_references: tuple[str, ...] = ()


class ChangeJournalEntry(FrozenModel):
    sequence: int = Field(ge=1)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str = "splash-evals"
    reason: str
    before_digest: str
    after_digest: str
    before: dict[str, Any]
    after: dict[str, Any]


class RestoreAction(FrozenModel):
    setting: str
    current: Any = None
    restore_to: Any = None


class RestorePlan(FrozenModel):
    snapshot_id: str
    status: RestoreStatus
    dry_run: bool = True
    current_digest: str
    expected_current_digest: str
    target_digest: str
    actions: tuple[RestoreAction, ...] = ()
    reasons: tuple[str, ...] = ()


class RequestBudgetLimits(FrozenModel):
    max_wall_minutes: int = Field(default=45, gt=0)
    max_generated_tokens: int = Field(default=250_000, gt=0)
    max_in_flight_requests: int = Field(default=1, ge=1, le=1)
    max_live_requests: int = Field(default=250, gt=0)
    max_tuning_candidates_beyond_baseline: int = Field(default=3, ge=0)
    max_generated_candidates_per_problem: int = Field(default=1, ge=1, le=1)
    max_new_download_gib_without_confirmation: float = Field(default=2.0, ge=0)
    minimum_free_disk_gib: float = Field(default=20.0, ge=0)
    paid_api_budget_usd: float = Field(default=0.0, ge=0, le=0)


class RequestBudgetUsage(FrozenModel):
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    generated_tokens: int = Field(default=0, ge=0)
    live_requests: int = Field(default=0, ge=0)
    in_flight_requests: int = Field(default=0, ge=0)
    tuning_candidates_beyond_baseline: int = Field(default=0, ge=0)


class BudgetDecision(FrozenModel):
    allowed: bool
    reasons: tuple[str, ...] = ()
    reserved_generated_tokens: int = Field(default=0, ge=0)
