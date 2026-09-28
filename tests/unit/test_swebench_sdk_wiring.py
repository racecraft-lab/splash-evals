"""SDK checkpoint integration uses synthetic model/container fixtures only."""

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
import test_swebench as fixtures

from local_evals import swebench as swe


def setup_run(tmp_path):
    state = tmp_path / "private"
    state.mkdir(mode=0o700)
    repo = Path.cwd()
    profile = fixtures._profile(repo, state)
    prepared = swe._validate_profile(profile, repo, state)
    run_dir = state / "run"
    run_dir.mkdir(mode=0o700)
    return prepared, run_dir


def response(actual=12, status="completed"):
    return {
        "status": status,
        "choices": []
        if status != "completed"
        else [{"message": {"role": "assistant", "content": "synthetic"}}],
        "usage": {"completion_tokens": actual},
        "charged_output_tokens": swe.MAX_OUTPUT if actual is None else actual,
    }


def dispatch(path, repo):
    return swe._checkpoint_sdk_chat(
        {"model": "synthetic", "messages": []},
        checkpoint_path=path,
        deadline=time.monotonic() + 10,
        output_limit=2 * swe.MAX_OUTPUT,
        request_limit=3,
        repo=repo,
    )


def budget_dispatch(path, repo, *, output_limit=262_144, request_limit=100):
    return swe._checkpoint_sdk_chat(
        {"model": "synthetic", "messages": []},
        checkpoint_path=path,
        deadline=time.monotonic() + 10,
        output_limit=output_limit,
        request_limit=request_limit,
        repo=repo,
    )


def test_aggregate_output_budget_allows_second_small_sdk_response(tmp_path, monkeypatch):
    prepared, run_dir = setup_run(tmp_path)
    checkpoint = run_dir / "sdk-checkpoint.json"
    swe._write_private_json(checkpoint, {})
    calls = []

    def sdk(body, **kwargs):
        saved = json.loads(checkpoint.read_bytes())["sdk_budget"]
        assert saved["requests"] == len(calls) + 1
        assert saved["charged_output_tokens"] == len(calls) * 12 + swe.MAX_OUTPUT
        assert saved["state"] == "reserved"
        assert kwargs["budget"].remaining == swe.MAX_OUTPUT
        calls.append(body)
        return response(12)

    monkeypatch.setattr(swe, "sdk_chat_response", sdk)

    first = budget_dispatch(checkpoint, prepared.repo)
    second = budget_dispatch(checkpoint, prepared.repo)

    assert len(calls) == 2
    assert first["status"] == second["status"] == "completed"
    saved = json.loads(checkpoint.read_bytes())["sdk_budget"]
    assert saved["requests"] == 2
    assert saved["charged_output_tokens"] == 24
    assert saved["actual_output_tokens"] == 24


def test_aggregate_output_budget_refuses_second_call_when_less_than_one_reservation_remains(
    tmp_path, monkeypatch
):
    prepared, run_dir = setup_run(tmp_path)
    checkpoint = run_dir / "sdk-checkpoint.json"
    swe._write_private_json(
        checkpoint,
        {
            "sdk_budget": {
                "requests": 3,
                "charged_output_tokens": 3 * swe.MAX_OUTPUT,
                "last_actual_output_tokens": None,
                "actual_output_tokens": None,
                "last_request_sha256": "a" * 64,
                "state": "completed",
            }
        },
    )
    calls = []

    def sdk(body, **kwargs):
        saved = json.loads(checkpoint.read_bytes())["sdk_budget"]
        assert saved["requests"] == 4
        assert saved["charged_output_tokens"] == 4 * swe.MAX_OUTPUT
        assert saved["state"] == "reserved"
        calls.append(body)
        return response(12)

    monkeypatch.setattr(swe, "sdk_chat_response", sdk)

    first = budget_dispatch(checkpoint, prepared.repo)
    with pytest.raises(swe._SdkTrajectoryBudgetExhausted, match="budget exhausted"):
        budget_dispatch(checkpoint, prepared.repo)

    assert first["status"] == "completed"
    assert len(calls) == 1
    saved = json.loads(checkpoint.read_bytes())["sdk_budget"]
    assert saved["requests"] == 4
    assert saved["charged_output_tokens"] == 3 * swe.MAX_OUTPUT + 12
    assert saved["state"] == "completed"


@pytest.mark.parametrize("actual", [12, None])
def test_reservation_precedes_dispatch_and_survives_terminal_write(tmp_path, monkeypatch, actual):
    prepared, run_dir = setup_run(tmp_path)
    calls = []

    def sdk(body, **kwargs):
        checkpoint = run_dir / f"checkpoint-{len(calls):04d}.json"
        saved = json.loads(checkpoint.read_bytes())
        assert saved["sdk_budget"]["state"] == "reserved"
        assert saved["sdk_budget"]["charged_output_tokens"] == swe.MAX_OUTPUT
        assert saved["sdk_budget"]["last_actual_output_tokens"] is None
        calls.append(body)
        return response(actual)

    monkeypatch.setattr(swe, "sdk_chat_response", sdk)

    def agent(**kwargs):
        dispatch(kwargs["sdk_checkpoint"], prepared.repo)
        return fixtures._fixture_agent(**kwargs)

    runtime = replace(fixtures._runtime(fixtures._FakeRunner()), agent_executor=agent)
    results = swe._execute_tasks(prepared, runtime, run_dir)
    assert len(results) == len(calls) == 2
    for result in results:
        assert result["state"] == "terminal"
        assert result["sdk_budget"]["last_actual_output_tokens"] == actual
        assert result["sdk_budget"]["charged_output_tokens"] == (
            swe.MAX_OUTPUT if actual is None else actual
        )
        assert result["sdk_budget"]["actual_output_tokens"] == actual
    summary = swe._benchmark_summary(prepared, results)
    assert summary["actual_output_tokens"] == (None if actual is None else 2 * actual)


@pytest.mark.parametrize("failure", ["failed", "cancelled", "exception", "interrupt"])
def test_uncertain_inference_stops_task_loop_and_resume_accounts_it_without_refund(
    tmp_path, monkeypatch, failure
):
    prepared, run_dir = setup_run(tmp_path)
    calls = []

    def sdk(*args, **kwargs):
        calls.append(1)
        if failure == "exception":
            raise RuntimeError("synthetic transport loss")
        if failure == "interrupt":
            raise KeyboardInterrupt
        return response(None, failure)

    monkeypatch.setattr(swe, "sdk_chat_response", sdk)

    def agent(**kwargs):
        dispatch(kwargs["sdk_checkpoint"], prepared.repo)
        pytest.fail("terminal inference returned to agent")

    runtime = replace(fixtures._runtime(fixtures._FakeRunner()), agent_executor=agent)
    expected = KeyboardInterrupt if failure == "interrupt" else swe._SdkTransportStopped
    with pytest.raises(expected):
        swe._execute_tasks(prepared, runtime, run_dir)
    checkpoint = run_dir / "checkpoint-0000.json"
    before = checkpoint.read_bytes()
    saved = json.loads(before)["sdk_budget"]
    assert saved["charged_output_tokens"] == swe.MAX_OUTPUT
    assert saved["last_actual_output_tokens"] is None
    assert not (run_dir / "checkpoint-0001.json").exists()

    class NextTaskStarted(BaseException):
        pass

    def next_agent(**kwargs):
        raise NextTaskStarted

    resumed = replace(runtime, agent_executor=next_agent)
    with pytest.raises(NextTaskStarted):
        swe._execute_tasks(prepared, resumed, run_dir)
    recovered = json.loads(checkpoint.read_bytes())
    assert recovered["state"] == "ambiguous"
    assert recovered["status"] == "infrastructure_error"
    assert recovered["attempted"] is False
    assert recovered["sdk_budget"] == saved
    assert calls == [1]
    loaded = swe._task_checkpoint(checkpoint, prepared=prepared, task=prepared.tasks[0], index=0)
    assert loaded is not None and loaded["status"] == "infrastructure_error"


def test_sdk_fingerprint_changes_protocol_and_old_checkpoint_refuses(tmp_path, monkeypatch):
    prepared, run_dir = setup_run(tmp_path)
    path = run_dir / "checkpoint-0000.json"
    swe._write_private_json(
        path,
        swe._checkpoint_payload(
            prepared,
            prepared.tasks[0],
            0,
            attempt_nonce="0123456789abcdef",
            state="in_flight",
            attempted=False,
            status="in_flight",
        ),
    )
    monkeypatch.setattr(swe, "_sdk_transport_fingerprint", lambda repo: "f" * 64)
    changed = swe._validate_profile(prepared.profile, prepared.repo, tmp_path / "private")
    assert changed.protocol_fingerprint != prepared.protocol_fingerprint
    with pytest.raises(swe.SwebenchError, match="protocol binding drifted"):
        swe._task_checkpoint(path, prepared=changed, task=changed.tasks[0], index=0)


def test_host_chat_uses_sdk_without_http_completion(tmp_path, monkeypatch):
    model, instance = {"key": "synthetic"}, {"id": "instance"}
    fingerprint = swe._sha256(swe._canonical({"model": model, "instance": instance}))
    monkeypatch.setattr(swe, "_loaded_model", lambda *args, **kwargs: (model, instance))

    def http(*args, **kwargs):
        pytest.fail("HTTP completion must not be used")

    monkeypatch.setattr(swe, "_http_json", http)
    seen = []

    def sdk(body, **kwargs):
        seen.append(kwargs)
        return {
            **response(),
            "model": "synthetic",
            "choices": [
                {
                    "message": {"role": "assistant", "content": "synthetic"},
                    "finish_reason": "length",
                }
            ],
            "usage": {"completion_tokens": 12, "prompt_tokens": 5, "total_tokens": 17},
            "evidence": {
                "context_length": 131072,
                "stop_reason": "maxPredictedTokensReached",
            },
        }

    monkeypatch.setattr(swe, "_checkpoint_sdk_chat", sdk)
    actual, receipt = swe._host_verified_chat_response(
        "http://127.0.0.1:1234/v1",
        {"model": "synthetic"},
        expected_model="synthetic",
        expected_task_fingerprint="a" * 64,
        task_instance_id="task",
        expected_model_instance_id="instance",
        expected_served_model_fingerprint=fingerprint,
        deadline=time.monotonic() + 10,
        sdk_checkpoint=tmp_path / "checkpoint.json",
        sdk_output_limit=65536,
        sdk_request_limit=1,
    )
    assert seen[0]["checkpoint_path"] == tmp_path / "checkpoint.json"
    assert actual["usage"]["prompt_tokens"] == 5
    assert actual["choices"][0]["finish_reason"] == "length"
    assert actual["evidence"]["context_length"] == 131072
    assert actual["evidence"]["stop_reason"] == "maxPredictedTokensReached"
    assert receipt["response_sha256"] == swe._sha256(swe._canonical(actual))


def test_approved_on_profile_is_explicit_and_fingerprint_distinct(tmp_path):
    prepared, _ = setup_run(tmp_path)
    local = {**prepared.profile, "parameters": dict(swe._APPROVED_LOCAL_PARAMETERS)}
    changed = swe._validate_profile(local, prepared.repo, tmp_path / "private")
    assert changed.protocol_fingerprint != prepared.protocol_fingerprint
    assert changed.profile["parameters"]["reasoning_effort"] == "on"
    assert prepared.profile["parameters"]["reasoning_effort"] == "xhigh"


def test_xhigh_readiness_refuses_before_model_dispatch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported xhigh must not dispatch")

    monkeypatch.setattr(swe, "sdk_chat_response", forbidden)
    with pytest.raises(swe.SwebenchError, match="xhigh is unsupported locally"):
        swe._default_parameter_probe("http://127.0.0.1:1234/v1", "synthetic", swe._PARAMETERS)


def test_finished_budget_exhaustion_accounts_task_and_advances(tmp_path, monkeypatch):
    prepared, run_dir = setup_run(tmp_path)
    calls = []

    def sdk(*args, **kwargs):
        calls.append(1)
        return response(None)

    monkeypatch.setattr(swe, "sdk_chat_response", sdk)

    def agent(**kwargs):
        result = fixtures._fixture_agent(**kwargs)
        try:
            for _ in range(2):
                swe._checkpoint_sdk_chat(
                    {"model": "synthetic"},
                    checkpoint_path=kwargs["sdk_checkpoint"],
                    deadline=time.monotonic() + 10,
                    output_limit=swe.MAX_OUTPUT,
                    request_limit=2,
                    repo=prepared.repo,
                )
        except swe._SdkTrajectoryBudgetExhausted:
            return {
                **result,
                "status": "model_failure",
                "model_patch_sha256": None,
                "prediction_handle": None,
            }
        pytest.fail("budget must refuse")

    runtime = replace(fixtures._runtime(fixtures._FakeRunner()), agent_executor=agent)
    results = swe._execute_tasks(prepared, runtime, run_dir)
    assert calls == [1, 1]
    assert all(
        result["state"] == "terminal" and result["status"] == "model_failure" for result in results
    )
    summary = swe._benchmark_summary(prepared, results)
    assert summary["model_failure_count"] == 2
    assert summary["infrastructure_error_count"] == 0
    assert summary["actual_output_tokens"] is None
    assert summary["charged_output_tokens"] == 2 * swe.MAX_OUTPUT
    saved = json.loads((run_dir / "checkpoint-0000.json").read_bytes())["sdk_budget"]
    assert saved["charged_output_tokens"] == swe.MAX_OUTPUT
    assert saved["last_actual_output_tokens"] is None


def test_unknown_then_known_usage_stays_cumulatively_unknown(tmp_path, monkeypatch):
    prepared, run_dir = setup_run(tmp_path)
    answers = iter([None, 12, None, 12])
    monkeypatch.setattr(swe, "sdk_chat_response", lambda *args, **kwargs: response(next(answers)))

    def agent(**kwargs):
        dispatch(kwargs["sdk_checkpoint"], prepared.repo)
        dispatch(kwargs["sdk_checkpoint"], prepared.repo)
        return fixtures._fixture_agent(**kwargs)

    runtime = replace(fixtures._runtime(fixtures._FakeRunner()), agent_executor=agent)
    results = swe._execute_tasks(prepared, runtime, run_dir)
    for result in results:
        budget = result["sdk_budget"]
        assert budget["last_actual_output_tokens"] == 12
        assert budget["actual_output_tokens"] is None
        assert budget["charged_output_tokens"] == swe.MAX_OUTPUT + 12
    summary = swe._benchmark_summary(prepared, results)
    assert summary["actual_output_tokens"] is None
    assert summary["charged_output_tokens"] == 2 * (swe.MAX_OUTPUT + 12)


@pytest.mark.parametrize("after_budget", ["result", "task_exec"])
def test_rpc_budget_refusal_preserves_model_failure_without_more_commands(
    tmp_path, monkeypatch, after_budget
):
    tail = (
        {"op": "result", "value": {"status": "model_failure"}}
        if after_budget == "result"
        else {"op": "task_exec", "command": "must never execute"}
    )
    lines = iter(
        [
            swe._canonical({"op": "lm_chat", "body": {"model": "synthetic"}}),
            swe._canonical({"op": "lm_chat", "body": {"model": "synthetic"}}),
            swe._canonical(tail),
        ]
    )
    monkeypatch.setattr(swe, "_read_control_line", lambda *args, **kwargs: (next(lines), 0))
    replies = []
    monkeypatch.setattr(swe, "_rpc_reply", lambda process, value, **kwargs: replies.append(value))
    calls = []

    def chat(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise swe._SdkTrajectoryBudgetExhausted("budget exhausted")
        return response(), {"synthetic_receipt": True}

    monkeypatch.setattr(swe, "_host_verified_chat_response", chat)
    kwargs = {
        "evidence_dir": tmp_path,
        "runner": lambda *args, **kwargs: pytest.fail("command ran"),
        "max_output_bytes": 1048576,
        "server_origin": "http://127.0.0.1:1234/v1",
        "model": "synthetic",
        "task_fingerprint_sha256": "a" * 64,
        "instance_id": "task",
        "served_model_fingerprint": "b" * 64,
    }

    def exchange():
        return swe._control_plane_exchange_loop(
            None, kwargs, selector=None, stdout_fd=1, stderr_fd=2, deadline=time.monotonic() + 10
        )

    if after_budget == "result":
        result = exchange()
        assert result["status"] == "model_failure"
        assert result["host_response_receipt"] == {"synthetic_receipt": True}
    else:
        with pytest.raises(swe.SwebenchError, match="may not execute another command"):
            exchange()
    assert replies[-1] == {"ok": False, "error": "trajectory_budget_exhausted"}
    assert calls == [1, 1]


@pytest.mark.parametrize("operation", ["uncertain_resume", "completed_resume", "rescore"])
def test_recovery_never_probes_before_checkpoint_decision(tmp_path, monkeypatch, operation):
    prepared, _ = setup_run(tmp_path)
    runtime = fixtures._runtime(fixtures._FakeRunner())
    initial = swe.execute_swebench(
        prepared.profile,
        repo=prepared.repo,
        state=prepared.state,
        server_origin="http://127.0.0.1:1234/v1",
        runtime=runtime,
    )
    run_id = initial["run_id"]
    run_dir = prepared.state / "swebench/runs" / run_id
    if operation == "uncertain_resume":
        path = run_dir / "checkpoint-0000.json"
        saved = json.loads(path.read_bytes())
        saved["sdk_budget"] = {
            "requests": 1,
            "charged_output_tokens": swe.MAX_OUTPUT,
            "actual_output_tokens": None,
            "last_actual_output_tokens": None,
            "last_request_sha256": "a" * 64,
            "state": "reserved",
        }
        swe._write_private_json(path, saved)

    def forbidden(*args, **kwargs):
        pytest.fail("recovery must not make a model probe or SDK/HTTP model call")

    selected = replace(runtime, parameter_probe=forbidden, adapter_probe=forbidden)
    monkeypatch.setattr(swe, "sdk_chat_response", forbidden)
    monkeypatch.setattr(swe, "_http_json", forbidden)
    kwargs = dict(
        repo=prepared.repo,
        state=prepared.state,
        server_origin="http://127.0.0.1:1234/v1",
        run_id=run_id,
        runtime=selected,
    )
    if operation == "uncertain_resume":
        with pytest.raises(swe._SdkTransportStopped, match="no retry or refund"):
            swe.resume_swebench(prepared.profile, **kwargs)
    elif operation == "rescore":
        with pytest.raises(
            swe.SwebenchError,
            match="private grader capture identity is not bound to this task attempt",
        ):
            swe.rescore_swebench(prepared.profile, **kwargs)
    else:
        result = swe.resume_swebench(prepared.profile, **kwargs)
        assert result["benchmark_summary"] == initial["benchmark_summary"]


@pytest.mark.parametrize(
    "mismatch",
    [None, "temperature", "top_p", "reasoning", "max_output_tokens", "context_length", "cancelled"],
)
def test_readiness_uses_effective_sdk_fields_without_http_fallback(monkeypatch, mismatch):
    evidence = {
        "reasoning": "on",
        "temperature": 1,
        "top_p": 0.95,
        "max_output_tokens": 65536,
        "context_length": 131072,
    }
    if mismatch in evidence:
        evidence[mismatch] = None
    seen = []

    def sdk(body, **kwargs):
        seen.append(body)
        return {
            **response(status="cancelled" if mismatch == "cancelled" else "completed"),
            "evidence": evidence,
        }

    monkeypatch.setattr(swe, "sdk_chat_response", sdk)
    monkeypatch.setattr(swe, "_loaded_model", lambda *args, **kwargs: ({}, {}))
    monkeypatch.setattr(swe, "_http_json", lambda *args, **kwargs: pytest.fail("HTTP fallback"))
    if mismatch is None:
        assert all(
            swe._default_parameter_probe(
                "http://127.0.0.1:1234/v1", "synthetic", swe._APPROVED_LOCAL_PARAMETERS
            ).values()
        )
    else:
        with pytest.raises(swe._SdkTransportStopped, match="effective-settings probe failed"):
            swe._default_parameter_probe(
                "http://127.0.0.1:1234/v1", "synthetic", swe._APPROVED_LOCAL_PARAMETERS
            )
    assert len(seen) == 1
    assert seen[0]["max_tokens"] == 65536
