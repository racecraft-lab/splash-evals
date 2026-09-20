"""Fail-closed preflight checks for local inference and bounded requests."""

from __future__ import annotations

import ipaddress
import socket
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from .models import (
    BudgetDecision,
    LocalityEvidence,
    LocalityStatus,
    QualifiedEndpoint,
    RequestBudgetLimits,
    RequestBudgetUsage,
)


class PreflightError(RuntimeError):
    """Base class for a request blocked before transmission."""


class EndpointPolicyError(PreflightError):
    pass


class LocalityError(PreflightError):
    pass


class UnsupportedControlError(PreflightError):
    pass


class BudgetExceededError(PreflightError):
    pass


Resolver = Callable[[str, int], list[tuple[Any, ...]]]


def _system_resolver(host: str, port: int) -> list[tuple[Any, ...]]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def validate_loopback_url(
    url: str,
    *,
    resolver: Resolver = _system_resolver,
    require_explicit_port: bool = True,
) -> QualifiedEndpoint:
    """Resolve and require an HTTP(S) URL whose every address is loopback."""

    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise EndpointPolicyError("endpoint contains an invalid port") from exc
    if parsed.scheme not in {"http", "https"}:
        raise EndpointPolicyError("endpoint scheme must be http or https")
    if not parsed.hostname:
        raise EndpointPolicyError("endpoint must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise EndpointPolicyError("endpoint must not embed credentials")
    if parsed.query or parsed.fragment:
        raise EndpointPolicyError("endpoint must not include query or fragment data")
    if require_explicit_port and port is None:
        raise EndpointPolicyError("endpoint must include an explicit port")
    effective_port = port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname.rstrip(".").lower()
    if "%" in host:
        raise EndpointPolicyError("scoped IPv6 endpoint addresses are not allowed")

    addresses: set[str] = set()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            answers = resolver(host, effective_port)
        except OSError as exc:
            raise EndpointPolicyError("endpoint hostname could not be resolved") from exc
        for answer in answers:
            sockaddr = answer[4]
            try:
                addresses.add(str(ipaddress.ip_address(str(sockaddr[0]).split("%", 1)[0])))
            except (IndexError, ValueError) as exc:
                raise EndpointPolicyError("resolver returned an invalid address") from exc
    else:
        addresses.add(str(literal))
    if not addresses:
        raise EndpointPolicyError("endpoint hostname resolved to no addresses")
    if any(not ipaddress.ip_address(address).is_loopback for address in addresses):
        raise EndpointPolicyError("endpoint resolved to a non-loopback address")

    display_host = f"[{host}]" if ":" in host else host
    normalized = urlunsplit(
        (parsed.scheme, f"{display_host}:{effective_port}", parsed.path.rstrip("/"), "", "")
    )
    return QualifiedEndpoint(
        url=normalized,
        host=host,
        port=effective_port,
        resolved_addresses=tuple(sorted(addresses)),
    )


def validate_redirect_target(
    source_url: str, location: str, *, resolver: Resolver = _system_resolver
) -> QualifiedEndpoint:
    if not location:
        raise EndpointPolicyError("redirect omitted its target")
    return validate_loopback_url(urljoin(source_url, location), resolver=resolver)


def classify_locality(
    *,
    endpoint_loopback: bool,
    lm_link_state: str | None,
    local_instance_evidence: bool,
    execution_device: str | None = None,
) -> LocalityEvidence:
    """Classify physical execution locality without trusting localhost alone."""

    state = (lm_link_state or "unknown").strip().lower().replace("-", "_")
    reasons: list[str] = []
    if not endpoint_loopback:
        return LocalityEvidence(
            status=LocalityStatus.REMOTE,
            endpoint_loopback=False,
            lm_link_state=state,
            local_instance_evidence=local_instance_evidence,
            execution_device=execution_device,
            reasons=("inference endpoint is not loopback",),
        )
    if local_instance_evidence:
        return LocalityEvidence(
            status=LocalityStatus.VERIFIED_LOCAL,
            endpoint_loopback=True,
            lm_link_state=state or "unknown",
            local_instance_evidence=True,
            execution_device=execution_device,
            reasons=("LM Studio identifies the selected model instance as local",),
        )
    linked_markers = ("connected", "linked", "remote", "preferred_device")
    link_is_ambiguous = state in {"unknown", "unavailable", "error", ""} or any(
        marker in state for marker in linked_markers
    )
    if link_is_ambiguous:
        reasons.append("LM Link routing is linked or not conclusively disabled")
        if not local_instance_evidence:
            reasons.append("no corroborating local model-instance execution evidence")
        return LocalityEvidence(
            status=LocalityStatus.AMBIGUOUS_LM_LINK,
            endpoint_loopback=True,
            lm_link_state=state or "unknown",
            local_instance_evidence=local_instance_evidence,
            execution_device=execution_device,
            reasons=tuple(reasons),
        )
    return LocalityEvidence(
        status=LocalityStatus.UNKNOWN,
        endpoint_loopback=True,
        lm_link_state=state,
        local_instance_evidence=local_instance_evidence,
        execution_device=execution_device,
        reasons=("physical execution location is not proven",),
    )


def lms_device_is_local(
    *, device_identifier: str | None, local_device_identifier: str | None
) -> bool:
    """Apply the macOS ``lms`` loaded-model locality rule.

    The local Mac record has a null ``deviceIdentifier``. Any populated value
    denotes another device and must remain remote even if another discovery
    field happens to contain the same identifier.
    """

    del local_device_identifier
    return device_identifier is None


def require_verified_local(locality: LocalityEvidence) -> None:
    if locality.status is not LocalityStatus.VERIFIED_LOCAL:
        raise LocalityError("scored requests require verified local model execution")


_LLAMA_CPP_ONLY = {
    "flash_attention",
    "gpu_offload",
    "gpu_offload_ratio",
    "eval_batch_size",
    "mmap",
    "keep_model_in_memory",
    "num_experts",
    "rope_frequency_base",
    "rope_frequency_scale",
}


def validate_load_controls(
    *,
    engine: str | None,
    backend_specific: Mapping[str, Any],
    explicitly_supported: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Reject backend-specific controls unless applicability is proven."""

    normalized_engine = (engine or "").lower()
    for name in backend_specific:
        if name in explicitly_supported:
            continue
        if name in _LLAMA_CPP_ONLY and "llama" in normalized_engine:
            continue
        engine_name = engine or "unknown"
        raise UnsupportedControlError(f"load control {name!r} is not verified for {engine_name!r}")


def validate_context_budget(
    *,
    prompt_tokens: int,
    max_output_tokens: int,
    reserved_overhead_tokens: int,
    context_length: int,
) -> None:
    values = (prompt_tokens, max_output_tokens, reserved_overhead_tokens, context_length)
    if any(value < 0 for value in values) or context_length == 0 or max_output_tokens == 0:
        raise BudgetExceededError("context budget values must be positive and finite")
    if prompt_tokens + max_output_tokens + reserved_overhead_tokens > context_length:
        raise BudgetExceededError("prompt, output, and reserved overhead exceed effective context")


class RequestBudget:
    """Thread-safe session-wide budget with pessimistic missing-usage accounting."""

    def __init__(
        self,
        limits: RequestBudgetLimits | None = None,
        usage: RequestBudgetUsage | None = None,
    ) -> None:
        self.limits = limits or RequestBudgetLimits()
        self._usage = usage or RequestBudgetUsage()
        self._lock = threading.Lock()

    @property
    def usage(self) -> RequestBudgetUsage:
        with self._lock:
            return self._usage

    def check(self, *, reserve_output_tokens: int) -> BudgetDecision:
        if reserve_output_tokens <= 0:
            return BudgetDecision(allowed=False, reasons=("output reservation must be positive",))
        with self._lock:
            return self._check_locked(reserve_output_tokens)

    def _check_locked(self, reserve_output_tokens: int) -> BudgetDecision:
        reasons: list[str] = []
        elapsed = datetime.now(UTC) - self._usage.started_at
        if elapsed.total_seconds() >= self.limits.max_wall_minutes * 60:
            reasons.append("session wall-time limit reached")
        if self._usage.live_requests >= self.limits.max_live_requests:
            reasons.append("live-request limit reached")
        if self._usage.in_flight_requests >= self.limits.max_in_flight_requests:
            reasons.append("in-flight request limit reached")
        if self._usage.generated_tokens + reserve_output_tokens > self.limits.max_generated_tokens:
            reasons.append("generated-token limit would be exceeded")
        return BudgetDecision(
            allowed=not reasons,
            reasons=tuple(reasons),
            reserved_generated_tokens=reserve_output_tokens if not reasons else 0,
        )

    def begin(self, *, reserve_output_tokens: int) -> None:
        """Atomically reserve a request slot; output is charged on completion."""

        with self._lock:
            decision = self._check_locked(reserve_output_tokens)
            if not decision.allowed:
                raise BudgetExceededError("; ".join(decision.reasons))
            self._usage = self._usage.model_copy(
                update={
                    "live_requests": self._usage.live_requests + 1,
                    "in_flight_requests": self._usage.in_flight_requests + 1,
                }
            )

    def finish(self, *, generated_tokens: int | None, reserved_output_tokens: int) -> None:
        """Charge the reservation when usage is absent; do not reinterpret it as observed usage."""

        if generated_tokens is not None and generated_tokens < 0:
            raise ValueError("generated token usage cannot be negative")
        charged = generated_tokens if generated_tokens is not None else reserved_output_tokens
        with self._lock:
            if self._usage.in_flight_requests == 0:
                raise RuntimeError("no in-flight request to finish")
            self._usage = self._usage.model_copy(
                update={
                    "generated_tokens": self._usage.generated_tokens + charged,
                    "in_flight_requests": self._usage.in_flight_requests - 1,
                }
            )

    def cancel(self) -> None:
        with self._lock:
            if self._usage.in_flight_requests == 0:
                raise RuntimeError("no in-flight request to cancel")
            self._usage = self._usage.model_copy(
                update={"in_flight_requests": self._usage.in_flight_requests - 1}
            )

    def register_tuning_candidate(self) -> None:
        with self._lock:
            next_count = self._usage.tuning_candidates_beyond_baseline + 1
            if next_count > self.limits.max_tuning_candidates_beyond_baseline:
                raise BudgetExceededError("tuning-candidate limit reached")
            self._usage = self._usage.model_copy(
                update={"tuning_candidates_beyond_baseline": next_count}
            )


def validate_storage_budget(
    *,
    new_download_gib: float,
    free_disk_gib: float,
    limits: RequestBudgetLimits,
    download_confirmed: bool = False,
) -> None:
    if new_download_gib < 0 or free_disk_gib < 0:
        raise BudgetExceededError("storage budget values cannot be negative")
    if (
        new_download_gib > limits.max_new_download_gib_without_confirmation
        and not download_confirmed
    ):
        raise BudgetExceededError("model download requires explicit confirmation")
    if free_disk_gib - new_download_gib < limits.minimum_free_disk_gib:
        raise BudgetExceededError("minimum free-disk reserve would be violated")
