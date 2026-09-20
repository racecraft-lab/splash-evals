from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from local_evals.models import LocalityStatus, RequestBudgetLimits, RequestBudgetUsage
from local_evals.preflight import (
    BudgetExceededError,
    EndpointPolicyError,
    LocalityError,
    RequestBudget,
    UnsupportedControlError,
    classify_locality,
    require_verified_local,
    validate_context_budget,
    validate_load_controls,
    validate_loopback_url,
    validate_redirect_target,
)


def _resolver_with(*addresses: str):
    def resolve(host: str, port: int):
        del host
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
            for address in addresses
        ]

    return resolve


def test_loopback_endpoint_accepts_only_loopback_dns_answers() -> None:
    endpoint = validate_loopback_url(
        "http://synthetic.local:1234/v1/", resolver=_resolver_with("127.0.0.1")
    )

    assert endpoint.url == "http://synthetic.local:1234/v1"
    assert endpoint.resolved_addresses == ("127.0.0.1",)


@pytest.mark.parametrize(
    "url",
    [
        "https://synthetic.local/v1",
        "file:///tmp/synthetic.sock",
        "http://name:synthetic@127.0.0.1:1234/v1",
        "http://127.0.0.1:1234/v1?target=remote",
        "http://127.0.0.1:1234/v1#fragment",
    ],
)
def test_endpoint_policy_rejects_ambiguous_or_sensitive_url_shapes(url: str) -> None:
    with pytest.raises(EndpointPolicyError):
        validate_loopback_url(url, resolver=_resolver_with("127.0.0.1"))


def test_endpoint_rejects_dns_rebinding_style_mixed_answers() -> None:
    with pytest.raises(EndpointPolicyError, match="non-loopback"):
        validate_loopback_url(
            "http://synthetic.local:1234/v1",
            resolver=_resolver_with("127.0.0.1", "192.0.2.9"),
        )


def test_redirect_target_is_requalified_and_remote_redirect_is_rejected() -> None:
    with pytest.raises(EndpointPolicyError, match="non-loopback"):
        validate_redirect_target(
            "http://127.0.0.1:1234/v1/chat/completions",
            "http://redirect.example.invalid:8080/remote",
            resolver=_resolver_with("192.0.2.10"),
        )


def test_loopback_with_uncertain_lm_link_is_not_verified_local() -> None:
    locality = classify_locality(
        endpoint_loopback=True,
        lm_link_state="unknown",
        local_instance_evidence=False,
    )

    assert locality.status is LocalityStatus.AMBIGUOUS_LM_LINK
    with pytest.raises(LocalityError, match="require verified local"):
        require_verified_local(locality)


def test_concrete_local_instance_evidence_passes_locality_gate() -> None:
    locality = classify_locality(
        endpoint_loopback=True,
        lm_link_state="disabled",
        local_instance_evidence=True,
        execution_device="local-apple-silicon",
    )

    assert locality.status is LocalityStatus.VERIFIED_LOCAL
    require_verified_local(locality)


def test_llama_only_control_is_rejected_for_splash_engine() -> None:
    with pytest.raises(UnsupportedControlError, match="not verified"):
        validate_load_controls(engine="mlx-splash", backend_specific={"flash_attention": True})


def test_explicitly_qualified_control_is_allowed() -> None:
    validate_load_controls(
        engine="mlx-splash",
        backend_specific={"context_length": 32768},
        explicitly_supported={"context_length"},
    )


def test_context_budget_refuses_silent_truncation() -> None:
    with pytest.raises(BudgetExceededError, match="exceed effective context"):
        validate_context_budget(
            prompt_tokens=30_000,
            max_output_tokens=4_096,
            reserved_overhead_tokens=512,
            context_length=32_768,
        )


def test_missing_usage_is_charged_as_reserved_not_zero() -> None:
    budget = RequestBudget(RequestBudgetLimits(max_generated_tokens=100, max_live_requests=2))
    budget.begin(reserve_output_tokens=60)
    budget.finish(generated_tokens=None, reserved_output_tokens=60)

    assert budget.usage.generated_tokens == 60
    assert budget.usage.live_requests == 1
    assert budget.usage.in_flight_requests == 0
    assert budget.check(reserve_output_tokens=41).allowed is False


def test_session_request_limit_is_not_reset_between_runs() -> None:
    budget = RequestBudget(RequestBudgetLimits(max_live_requests=1))
    budget.begin(reserve_output_tokens=1)
    budget.finish(generated_tokens=1, reserved_output_tokens=1)

    with pytest.raises(BudgetExceededError, match="live-request limit"):
        budget.begin(reserve_output_tokens=1)


def test_expired_session_wall_budget_blocks_before_request() -> None:
    usage = RequestBudgetUsage(started_at=datetime.now(UTC) - timedelta(minutes=46))
    budget = RequestBudget(RequestBudgetLimits(max_wall_minutes=45), usage)

    decision = budget.check(reserve_output_tokens=1)

    assert decision.allowed is False
    assert "session wall-time limit reached" in decision.reasons
