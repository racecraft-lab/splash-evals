from __future__ import annotations

import pytest
from pydantic import ValidationError

from local_evals.models import (
    EvaluationConfig,
    ExecutionConfig,
    LocalityEvidence,
    LocalityStatus,
    RequestBudgetLimits,
    SecretReference,
)


def test_execution_policy_forbids_parallelism_and_hidden_retries() -> None:
    with pytest.raises(ValidationError):
        ExecutionConfig(max_in_flight_requests=2)
    with pytest.raises(ValidationError):
        ExecutionConfig(transport_retries=1)


def test_paid_budget_is_always_zero() -> None:
    with pytest.raises(ValidationError):
        RequestBudgetLimits(paid_api_budget_usd=0.01)


def test_config_requires_nonempty_id() -> None:
    with pytest.raises(ValidationError, match="must not be empty"):
        EvaluationConfig(id="   ")


def test_local_listener_does_not_imply_local_execution() -> None:
    evidence = LocalityEvidence(
        status=LocalityStatus.AMBIGUOUS_LM_LINK,
        endpoint_loopback=True,
        lm_link_state="linked-preferred-device-unknown",
        local_instance_evidence=False,
    )

    assert evidence.endpoint_loopback is True
    assert evidence.status is not LocalityStatus.VERIFIED_LOCAL


def test_secret_reference_never_serializes_secret_value() -> None:
    reference = SecretReference(name="SYNTHETIC_API_KEY", value="synthetic-not-a-secret")

    dumped = reference.model_dump(mode="json")

    assert dumped == {"name": "SYNTHETIC_API_KEY", "source": "environment"}
    assert "synthetic-not-a-secret" not in repr(reference)
