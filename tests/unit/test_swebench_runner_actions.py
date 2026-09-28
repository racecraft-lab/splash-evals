"""Exercise production runner classification with synthetic mini-agent/host fixtures."""

import base64
import hashlib
import importlib.util
import io
import json
import os
import sys
import urllib.request
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml as yaml_loader

_ACTIONS_TEXT = "minisweagent.models.utils.actions_text"


class _FormatError(Exception):
    """Synthetic stand-in for the pinned upstream FormatError."""


def _parse_regex_actions(content, *, action_regex, format_error_template, template_kwargs=None):
    # Mirrors pinned mini-SWE-agent 2.4.6 parse_regex_actions for exactly-one-action checks.
    import re

    actions = [action.strip() for action in re.findall(action_regex, content, re.DOTALL)]
    if len(actions) != 1:
        raise _FormatError(format_error_template, template_kwargs)
    return [{"command": action} for action in actions]


_SYNTHETIC_TEMPLATES = {
    "system_template": "synthetic system",
    "instance_template": "{{task}}",
    "observation_template": "{{output.returncode}} {{output.output}}",
    "format_error_template": "synthetic format error",
}


def _format_observation_messages(outputs, *, observation_template, template_vars=None):
    return [
        {"role": "user", "content": observation_template, "extra": dict(output)}
        for output in outputs
    ]


def _stub_actions_text(modules):
    for name in ("minisweagent.models", "minisweagent.models.utils", _ACTIONS_TEXT):
        modules[name] = ModuleType(name)
    modules[_ACTIONS_TEXT].parse_regex_actions = _parse_regex_actions
    modules[_ACTIONS_TEXT].format_observation_messages = _format_observation_messages


def _load_runner_for_identity_tests(monkeypatch):
    modules = {
        name: ModuleType(name)
        for name in (
            "minisweagent",
            "minisweagent.agents",
            "minisweagent.agents.default",
            "minisweagent.exceptions",
            "yaml",
        )
    }
    modules["minisweagent"].__version__ = "synthetic"
    modules["minisweagent.agents.default"].DefaultAgent = object
    modules["minisweagent.exceptions"].Submitted = RuntimeError
    _stub_actions_text(modules)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    runner_path = Path(__file__).with_name("runner.py")
    if not runner_path.exists():
        runner_path = Path.cwd() / "sandbox/swebench/runner.py"
    spec = importlib.util.spec_from_file_location("synthetic_swe_runner_identity", runner_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "_upstream_templates", lambda: _SYNTHETIC_TEMPLATES)
    return runner


def test_loaded_instance_resolves_selected_instance_id_separately_from_model_key(monkeypatch):
    runner = _load_runner_for_identity_tests(monkeypatch)
    selected_id = "racecraft-splash-local"
    inventory = {
        "models": [
            {
                "key": "qwen3.8-27b-splash",
                "loaded_instances": [{"id": selected_id, "context_length": 131072}],
            }
        ]
    }
    monkeypatch.setattr(runner, "_rpc", lambda _request: {"value": inventory})

    instance_id, fingerprint = runner._loaded_instance(selected_id)

    assert instance_id == selected_id
    assert len(fingerprint) == 64


def test_loaded_instance_ignores_only_remaining_ttl_countdown(monkeypatch):
    runner = _load_runner_for_identity_tests(monkeypatch)
    selected_id = "racecraft-splash-local"

    def inventory(ttl, *, model_key="qwen3.8-27b-splash", context_length=131072):
        return {
            "models": [
                {
                    "key": model_key,
                    "format": "gguf",
                    "future_model_field": "retained",
                    "loaded_instances": [
                        {
                            "id": selected_id,
                            "config": {"context_length": context_length},
                            "remaining_ttl_seconds": ttl,
                            "future_instance_field": "retained",
                        }
                    ],
                }
            ]
        }

    current = inventory(300)
    monkeypatch.setattr(runner, "_rpc", lambda _request: {"value": current})
    instance_id, original_fingerprint = runner._loaded_instance(selected_id)
    assert instance_id == selected_id

    current = inventory(298)
    _, countdown_fingerprint = runner._loaded_instance(selected_id)
    assert countdown_fingerprint == original_fingerprint

    for changed in (
        inventory(298, model_key="other-model"),
        inventory(298, context_length=65536),
    ):
        monkeypatch.setattr(runner, "_rpc", lambda _request, value=changed: {"value": value})
        _, changed_fingerprint = runner._loaded_instance(selected_id)
        assert changed_fingerprint != original_fingerprint

    changed = inventory(298)
    changed["models"][0]["future_model_field"] = "changed"
    monkeypatch.setattr(runner, "_rpc", lambda _request: {"value": changed})
    _, changed_model_fingerprint = runner._loaded_instance(selected_id)
    assert changed_model_fingerprint != original_fingerprint

    changed = inventory(298)
    changed["models"][0]["loaded_instances"][0]["future_instance_field"] = "changed"
    monkeypatch.setattr(runner, "_rpc", lambda _request: {"value": changed})
    _, changed_instance_fingerprint = runner._loaded_instance(selected_id)
    assert changed_instance_fingerprint != original_fingerprint


def test_live_qualification_identity_preflight_uses_inventory_only(monkeypatch):
    if os.environ.get("SWEBENCH_LIVE_IDENTITY_PREFLIGHT") != "1":
        pytest.skip("set SWEBENCH_LIVE_IDENTITY_PREFLIGHT=1 for the local inventory preflight")

    repo = Path(__file__).resolve().parents[2]
    profile = yaml_loader.safe_load(
        (repo / "configs/profiles/swebench-qualification.yaml").read_text()
    )
    benchmark = profile["swebench"]
    expected_fingerprint = benchmark["model_runtime"]["served_model_fingerprint"]
    model_alias = benchmark["model"]

    with urllib.request.urlopen("http://127.0.0.1:1234/api/v1/models", timeout=5) as response:
        inventory = json.load(response)

    runner = _load_runner_for_identity_tests(monkeypatch)
    requests = []

    def inventory_only_rpc(request):
        requests.append(request)
        assert request == {"op": "lm_models"}
        return {"value": inventory}

    monkeypatch.setattr(runner, "_rpc", inventory_only_rpc)
    instance_id, actual_fingerprint = runner._loaded_instance(model_alias)

    assert instance_id == model_alias
    assert actual_fingerprint == expected_fingerprint
    assert requests == [{"op": "lm_models"}]


def test_loaded_instance_rejects_alias_that_matches_model_key_and_other_instance_id(monkeypatch):
    runner = _load_runner_for_identity_tests(monkeypatch)
    selected_id = "racecraft-splash-local"
    inventory = {
        "models": [
            {"key": selected_id, "loaded_instances": [{"id": "old-instance"}]},
            {
                "key": "qwen3.8-27b-splash",
                "loaded_instances": [{"id": selected_id}],
            },
        ]
    }
    monkeypatch.setattr(runner, "_rpc", lambda _request: {"value": inventory})

    with pytest.raises(RuntimeError, match="exactly one loaded instance"):
        runner._loaded_instance(selected_id)


def test_strict_model_rejects_instance_alias_rebound_during_response(monkeypatch):
    runner = _load_runner_for_identity_tests(monkeypatch)
    selected_id = "racecraft-splash-local"
    before_inventory = {
        "models": [
            {
                "key": "qwen3.8-27b-splash",
                "loaded_instances": [{"id": selected_id, "context_length": 131072}],
            }
        ]
    }
    rebound_inventory = {
        "models": [
            {
                "key": "different-model",
                "loaded_instances": [{"id": selected_id, "context_length": 131072}],
            }
        ]
    }
    monkeypatch.setattr(runner, "_rpc", lambda _request: {"value": before_inventory})
    _, frozen_fingerprint = runner._loaded_instance(selected_id)
    inventories = iter((before_inventory, rebound_inventory))

    def rpc(request):
        if request["op"] == "lm_models":
            return {"value": next(inventories)}
        assert request["op"] == "lm_chat"
        return {
            "value": {
                "model": selected_id,
                "choices": [{"message": {"content": "```mswea_bash_command\necho safe\n```"}}],
            }
        }

    monkeypatch.setattr(runner, "_rpc", rpc)
    model = runner.StrictModel(
        {
            "model": selected_id,
            "max_requests": 1,
            "served_model_fingerprint": frozen_fingerprint,
            "parameters": {
                "temperature": 1,
                "top_p": 0.95,
                "max_output_tokens": 65536,
                "reasoning_effort": "on",
            },
        }
    )

    with pytest.raises(RuntimeError, match="did not prove the exact served instance"):
        model.query([{"role": "user", "content": "synthetic"}])


@pytest.mark.parametrize(
    "content,identity_valid,telemetry",
    [
        (
            "No bash action",
            True,
            {
                "usage": {"completion_tokens": 7, "prompt_tokens": 5, "total_tokens": 12},
                "finish_reason": "length",
                "evidence": {
                    "context_length": 131072,
                    "stop_reason": "contextLengthReached",
                },
                "expected_truncation_status": "context_length_reached",
                "expected_context_truncation_status": "context_length_reached",
            },
        ),
        (
            "```mswea_bash_command\necho one\n```\n```mswea_bash_command\necho two\n```",
            True,
            {
                "usage": {"completion_tokens": 7},
                "finish_reason": None,
                "evidence": None,
                "expected_truncation_status": None,
                "expected_context_truncation_status": None,
            },
        ),
        (
            "No bash action",
            True,
            {
                "usage": {"completion_tokens": 7, "prompt_tokens": 5, "total_tokens": 12},
                "finish_reason": "length",
                "evidence": {
                    "context_length": 131072,
                    "stop_reason": "maxPredictedTokensReached",
                },
                "expected_truncation_status": "generation_limit_reached",
                "expected_context_truncation_status": None,
            },
        ),
        (
            "No bash action",
            False,
            {
                "usage": {"completion_tokens": 7, "prompt_tokens": 5, "total_tokens": 12},
                "finish_reason": "length",
                "evidence": {"context_length": 131072},
                "expected_truncation_status": "generation_limit_reached",
                "expected_context_truncation_status": None,
            },
        ),
        (
            "No bash action",
            True,
            {
                "usage": {"completion_tokens": 7, "prompt_tokens": 5, "total_tokens": 12},
                "finish_reason": "length",
                "evidence": {"context_length": 131072},
                "expected_truncation_status": "limit_reached_unspecified",
                "expected_context_truncation_status": None,
            },
        ),
    ],
)
def test_first_invalid_action_is_model_failure_not_infrastructure(
    monkeypatch, content, identity_valid, telemetry
):
    # Only the unavailable third-party agent loop is synthetic; main and StrictModel
    # are loaded unchanged from the trusted project runner.
    effective_configs = []

    class Agent:
        def __init__(self, model, environment, **kwargs):
            self.model = model
            effective_configs.append(kwargs)

        def run(self, statement):
            self.model.query([{"role": "user", "content": statement}])

        def serialize(self):
            return {"messages": []}

    modules = {
        name: ModuleType(name)
        for name in (
            "minisweagent",
            "minisweagent.agents",
            "minisweagent.agents.default",
            "minisweagent.exceptions",
            "yaml",
        )
    }
    modules["minisweagent"].__version__ = "synthetic"
    modules["minisweagent.agents.default"].DefaultAgent = Agent
    modules["minisweagent.exceptions"].Submitted = RuntimeError
    modules["yaml"].safe_load = lambda _content: {"agent": {}}
    _stub_actions_text(modules)
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    runner_path = Path(__file__).with_name("runner.py")
    if not runner_path.exists():
        runner_path = Path.cwd() / "sandbox/swebench/runner.py"
    spec = importlib.util.spec_from_file_location("synthetic_swe_runner", runner_path)
    assert spec is not None and spec.loader is not None
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "_upstream_templates", lambda: _SYNTHETIC_TEMPLATES)
    clock = iter((10.0, 12.5))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    statement = "Synthetic classification fixture."
    request = {
        "model": "synthetic",
        "instance_id": "task",
        "max_requests": 1,
        "max_turns": 7,
        "timeout_seconds": 41,
        "served_model_fingerprint": "verified",
        "problem_statement_sha256": hashlib.sha256(statement.encode()).hexdigest(),
        "parameters": {
            "temperature": 1,
            "top_p": 0.95,
            "max_output_tokens": 65536,
            "reasoning_effort": "on",
        },
    }
    stdin = io.BytesIO(json.dumps({"op": "start", "request": request}).encode() + b"\n")
    stdout = io.BytesIO()
    monkeypatch.setattr(
        runner,
        "sys",
        SimpleNamespace(stdin=SimpleNamespace(buffer=stdin), stdout=SimpleNamespace(buffer=stdout)),
    )
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            "agent:\n  tools: [bash]\n  step_limit: 100\n  wall_time_limit_seconds: 600"
            if str(path) == "/opt/racecraft/mini-swe-agent.yaml"
            else original_read(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(
        runner,
        "_loaded_instance",
        lambda model: ("instance", "verified" if identity_valid else "drifted"),
    )
    calls = []

    def rpc(value):
        calls.append(value["op"])
        if value["op"] == "task_json":
            return {
                "output_b64": base64.b64encode(
                    json.dumps({"instance_id": "task", "problem_statement": statement}).encode()
                ).decode()
            }
        assert value["op"] == "lm_chat"
        choice = {"message": {"content": content}}
        if telemetry["finish_reason"] is not None:
            choice["finish_reason"] = telemetry["finish_reason"]
        value = {"model": "synthetic", "choices": [choice], "usage": telemetry["usage"]}
        if telemetry["evidence"] is not None:
            value["evidence"] = telemetry["evidence"]
        return {"value": value}

    monkeypatch.setattr(runner, "_rpc", rpc)
    evidence = []
    monkeypatch.setattr(runner, "_write_evidence", lambda name, data: evidence.append(name))
    assert runner.main() == 0
    result = json.loads(stdout.getvalue())["value"]
    assert result["status"] == ("model_failure" if identity_valid else "infrastructure_error")
    assert result["attempted"] is identity_valid
    assert result["first_verified_response"] is identity_valid
    assert result["request_count"] == int(identity_valid)
    assert result["output_tokens"] == (7 if identity_valid else 0)
    assert result["prompt_tokens"] == (
        telemetry["usage"].get("prompt_tokens") if identity_valid else None
    )
    assert result["finish_reason"] == (telemetry["finish_reason"] if identity_valid else None)
    assert result["context_length"] == (
        telemetry["evidence"]["context_length"]
        if identity_valid and telemetry["evidence"] is not None
        else None
    )
    assert result["truncation_status"] == (
        telemetry["expected_truncation_status"] if identity_valid else None
    )
    assert result["context_truncation_status"] == (
        telemetry["expected_context_truncation_status"] if identity_valid else None
    )
    assert effective_configs[0]["step_limit"] == 7
    assert effective_configs[0]["wall_time_limit_seconds"] == 41
    assert result["effective_step_limit"] == 7
    assert result["effective_wall_time_limit_seconds"] == 41
    assert result["trajectory_elapsed_seconds"] == 2.5
    assert result["model_patch_sha256"] is None
    assert result["prediction_handle"] is None
    assert len(evidence) == 1
    assert calls == (["task_json", "lm_chat"] if identity_valid else ["task_json"])


@pytest.mark.parametrize(
    ("run_result", "expected_status", "expect_diff", "diff_returncode"),
    [
        pytest.param(
            {"exit_status": "LimitsExceeded"}, "model_failure", False, 0, id="limits-exceeded"
        ),
        pytest.param(
            {"exit_status": "TimeExceeded"}, "model_failure", False, 0, id="time-exceeded"
        ),
        pytest.param(
            {"exit_status": "RepeatedFormatError"},
            "model_failure",
            False,
            0,
            id="repeated-format-error",
        ),
        pytest.param({"exit_status": "UnknownExit"}, "model_failure", False, 0, id="unknown-exit"),
        pytest.param({}, "model_failure", False, 0, id="missing-exit-status"),
        pytest.param(None, "model_failure", False, 0, id="malformed-result"),
        pytest.param(
            {"exit_status": "Submitted", "submission": "diff --git a/file b/file\n"},
            "completed",
            True,
            0,
            id="submitted",
        ),
        pytest.param(
            {"exit_status": "Submitted", "submission": "\n"},
            "model_failure",
            False,
            0,
            id="submitted-empty-patch",
        ),
    ],
)
def test_run_exit_status_controls_diff_request_and_result(
    monkeypatch, run_result, expected_status, expect_diff, diff_returncode
):
    runner = _load_runner_for_identity_tests(monkeypatch)
    trajectory = {"info": {"exit_status": "synthetic"}, "messages": []}

    class Agent:
        def __init__(self, model, _environment, **_kwargs):
            self.model = model

        def run(self, _statement):
            self.model.verified_responses = 100
            return run_result

        def serialize(self):
            return trajectory

    runner.DefaultAgent = Agent
    monkeypatch.setattr(runner.yaml, "safe_load", lambda _content: {"agent": {}}, raising=False)
    clock = iter((10.0, 12.5))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    statement = "Synthetic terminal-status fixture."
    request = {
        "model": "synthetic",
        "instance_id": "task",
        "max_requests": 100,
        "max_turns": 100,
        "timeout_seconds": 41,
        "served_model_fingerprint": "verified",
        "problem_statement_sha256": hashlib.sha256(statement.encode()).hexdigest(),
        "parameters": {
            "temperature": 1,
            "top_p": 0.95,
            "max_output_tokens": 65536,
            "reasoning_effort": "on",
        },
    }
    stdin = io.BytesIO(json.dumps({"op": "start", "request": request}).encode() + b"\n")
    stdout = io.BytesIO()
    monkeypatch.setattr(
        runner,
        "sys",
        SimpleNamespace(stdin=SimpleNamespace(buffer=stdin), stdout=SimpleNamespace(buffer=stdout)),
    )
    original_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda path, *args, **kwargs: (
            "agent:\n  tools: [bash]\n  step_limit: 100\n  wall_time_limit_seconds: 600"
            if str(path) == "/opt/racecraft/mini-swe-agent.yaml"
            else original_read(path, *args, **kwargs)
        ),
    )

    calls = []
    patch = b"diff --git a/file b/file\n"

    def rpc(value):
        calls.append(value["op"])
        if value["op"] == "task_json":
            task = {"instance_id": "task", "problem_statement": statement}
            return {"output_b64": base64.b64encode(json.dumps(task).encode()).decode()}
        raise AssertionError(f"unexpected RPC operation: {value['op']}")

    monkeypatch.setattr(runner, "_rpc", rpc)
    evidence = []
    monkeypatch.setattr(runner, "_write_evidence", lambda name, _data: evidence.append(name))

    assert runner.main() == 0
    result = json.loads(stdout.getvalue())["value"]
    expected_trajectory_sha = hashlib.sha256(runner._canonical(trajectory)).hexdigest()
    assert result["status"] == expected_status
    assert result["request_count"] == 100
    assert result["trajectory_sha256"] == expected_trajectory_sha
    assert f"trajectory-{expected_trajectory_sha}.json" in evidence
    # The submitted text is the prediction, as in pinned upstream; no host diff is requested.
    assert calls == ["task_json"]
    if expected_status == "completed":
        expected_patch_sha = hashlib.sha256(patch).hexdigest()
        assert result["model_patch_sha256"] == expected_patch_sha
        assert result["prediction_handle"] == f"private://prediction-{expected_patch_sha}.patch"
    else:
        assert result["model_patch_sha256"] is None
        assert result["prediction_handle"] is None


def test_submission_marker_makes_following_text_the_prediction(monkeypatch):
    runner = _load_runner_for_identity_tests(monkeypatch)
    raised = []

    class _Submitted(Exception):
        def __init__(self, *messages):
            raised.extend(messages)

    monkeypatch.setattr(runner, "Submitted", _Submitted)
    output = b"COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\ndiff --git a/x b/x\n"
    monkeypatch.setattr(
        runner,
        "_rpc",
        lambda _request: {"output_b64": base64.b64encode(output).decode(), "returncode": 0},
    )
    with pytest.raises(_Submitted):
        runner.StrictContainerEnvironment().execute({"command": "cat patch.txt"})
    assert raised[0]["extra"] == {
        "exit_status": "Submitted",
        "submission": "diff --git a/x b/x\n",
    }
