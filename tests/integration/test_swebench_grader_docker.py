"""Opt-in Docker proof for synthetic good/bad SWE-bench patches.

Run this file with SWEBENCH_SYNTHETIC_GRADER_BASE_IMAGE set to a local,
immutable qualification grader image. Also set SWEBENCH_PINNED_WHEEL and
SWEBENCH_DEPENDENCY_WHEELS as in test_swebench_official_scorer_pinned.py.
No benchmark task or model inference is used.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from hashlib import sha256
from importlib.metadata import version as package_version
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import local_evals.swebench as swebench
from local_evals.swebench_official_scorer import score_trusted_run, verify_pinned_wheel

_SOURCE = Path(__file__).resolve().parents[2] / "src"
_UNIDIFF_SHA = "5e2ca461eda8a4c761ad0922e3e8a65742f359dc60301a3a055b70fd7c158680"
_PYTEST_WHEELS = {
    "iniconfig-2.3.0-py3-none-any.whl": (
        "f631c04d2c48c52b84d0d0549c99ff38",
        "59c98df65b3101406327ecc7d53fbf12",
    ),
    "packaging-26.3-py3-none-any.whl": (
        "d7193f7c8e4e93f444fde0262bf90af3",
        "0e16fa0ad0ad44cb553c87339b23cd1c",
    ),
    "pluggy-1.6.0-py3-none-any.whl": (
        "e920276dd6813095e9377c0bc5566d94",
        "c932c33b27a3e3945d8389c374dd4746",
    ),
    "pygments-2.21.0-py3-none-any.whl": (
        "2363c69b61c4a97c838da3b130dcd646",
        "8f4848992b21a82f2a63ec34377137d9",
    ),
    "pytest-9.1.1-py3-none-any.whl": (
        "37a86b45efb9a47a61a36449063e8e18",
        "d0cab3161329fc099eb21783169c4f0c",
    ),
}
_SCRIPT = """#!/bin/bash
cd /opt/racecraft/grader
/opt/miniconda3/bin/python3 -B -m pytest -p no:cacheprovider -q -rA tests/test_synthetic.py
result=$?
printf '\\n>>>>> Test Exit Code: %s\\n' "$result"
exit "$result"
"""
_LIMITS: dict[str, Any] = {
    "timeout_seconds": 120,
    "memory_bytes": 2 * 1024**3,
    "cpus": "2",
    "pids_limit": 256,
    "nofile_limit": 1024,
    "tmpfs_bytes": 128 * 1024**2,
    "max_output_bytes": 4 * 1024**2,
    "max_turns": 1,
    "max_requests": 1,
    "max_task_output_tokens": 1,
}


def _docker(docker: str, *args: str, timeout: float = 120) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local Docker CLI and fixture arguments
        (docker, *args), capture_output=True, text=True, check=False, timeout=timeout
    )
    assert result.returncode == 0, f"Docker failed: {result.stderr[-3000:]}"
    return result.stdout.strip()


def _test_spec(tmp_path: Path, image: str) -> Any:
    wheel = Path(os.environ["SWEBENCH_PINNED_WHEEL"])
    dependencies = Path(os.environ["SWEBENCH_DEPENDENCY_WHEELS"])
    verify_pinned_wheel(wheel)
    wheel_root, dependency_root = tmp_path / "wheel", tmp_path / "dependencies"
    wheel_root.mkdir(mode=0o700)
    dependency_root.mkdir(mode=0o700)
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(wheel_root)
    unidiff = dependencies / "unidiff-1.0.1-py3-none-any.whl"
    assert unidiff.is_file() and not unidiff.is_symlink()
    assert sha256(unidiff.read_bytes()).hexdigest() == _UNIDIFF_SHA
    with zipfile.ZipFile(unidiff) as archive:
        archive.extractall(dependency_root)
    sys.path[:0] = [str(_SOURCE), str(wheel_root), str(dependency_root)]
    importlib.invalidate_caches()
    import swebench as official

    assert Path(official.__file__).is_relative_to(wheel_root)
    assert package_version("swebench") == "5.0.2"
    harness = ModuleType("swebench.harness")
    harness.__path__ = [str(Path(official.__file__).parent / "harness")]
    sys.modules["swebench.harness"] = harness
    from swebench.types import TestSpec

    return TestSpec(
        instance_id="synthetic__add-1",
        image=image,
        eval_script_list=[],
        repo="synthetic/add",
        version="1.0",
        FAIL_TO_PASS=["tests/test_synthetic.py::test_add_fix"],
        PASS_TO_PASS=["tests/test_synthetic.py::test_existing_behavior"],
        log_parser="parse_log_pytest",
        eval_type="pass_and_fail",
    )


def test_synthetic_good_and_bad_patches_use_fresh_isolated_grader(
    tmp_path: Path,
) -> None:
    base = os.environ.get("SWEBENCH_SYNTHETIC_GRADER_BASE_IMAGE")
    if not base:
        pytest.skip("set SWEBENCH_SYNTHETIC_GRADER_BASE_IMAGE to opt into Docker integration")
    assert re.fullmatch(r"[a-z0-9][a-z0-9._/:\-]*@sha256:[0-9a-f]{64}", base)
    docker = os.environ.get("SWEBENCH_TEST_DOCKER") or shutil.which("docker")
    assert docker is not None
    base_id = _docker(docker, "image", "inspect", "--format={{.Id}}", base)
    assert base_id == "sha256:" + base.rsplit("@sha256:", 1)[1]
    assert (
        _docker(docker, "image", "inspect", "--format={{.Os}}/{{.Architecture}}", base)
        == "linux/amd64"
    )
    base_alias = f"swebench-synthetic-base:{uuid.uuid4().hex[:16]}"

    context = tmp_path / "context"
    context.mkdir(mode=0o700)
    wheelhouse_value = os.environ.get("SWEBENCH_PYTEST_WHEELHOUSE")
    assert wheelhouse_value, "set SWEBENCH_PYTEST_WHEELHOUSE to the pinned offline wheelhouse"
    wheelhouse = Path(wheelhouse_value)
    assert wheelhouse.is_dir() and not wheelhouse.is_symlink()
    assert {path.name for path in wheelhouse.glob("*.whl")} == set(_PYTEST_WHEELS)
    staged_wheels = context / "wheels"
    staged_wheels.mkdir(mode=0o700)
    for filename, expected_sha_parts in _PYTEST_WHEELS.items():
        source = wheelhouse / filename
        assert source.is_file() and not source.is_symlink()
        expected_sha = "".join(expected_sha_parts)
        assert sha256(source.read_bytes()).hexdigest() == expected_sha
        target = staged_wheels / filename
        shutil.copyfile(source, target)
        assert sha256(target.read_bytes()).hexdigest() == expected_sha
    files = {
        "Dockerfile": f"""FROM --platform=linux/amd64 {base_alias}
USER root
RUN rm -rf /testbed && mkdir -p /testbed /opt/racecraft/grader/tests
COPY solution.py /testbed/solution.py
COPY test_synthetic.py /opt/racecraft/grader/tests/test_synthetic.py
COPY test-spec-eval.sh /opt/racecraft/grader/test-spec-eval.sh
COPY wheels/ /opt/racecraft/grader/wheels/
RUN set -eu; \\
    test -x /opt/miniconda3/bin/python3; \\
    /opt/miniconda3/bin/python3 -m pip install --no-index --no-deps \\
        /opt/racecraft/grader/wheels/*.whl; \\
    command -v git; \\
    /opt/miniconda3/bin/python3 -m pytest --version; \\
    git -C /testbed init -q; \\
    git -C /testbed config user.name fixture; \\
    git -C /testbed config user.email fixture@example.com; \\
    git -C /testbed add solution.py; \\
    git -C /testbed commit -qm baseline; \\
    chown -R 65532:65532 /testbed; \\
    git -C /testbed -c safe.directory=/testbed --no-optional-locks \\
        status --porcelain --untracked-files=all > /opt/racecraft/testbed-baseline.txt; \\
    chmod 0444 /opt/racecraft/testbed-baseline.txt; \\
    chmod 755 /opt/racecraft/grader/test-spec-eval.sh
USER 65532:65532
""",
        "solution.py": """def add(left, right):
    return left - right


def keep():
    return "stable"
""",
        "test_synthetic.py": """import sys
sys.path.insert(0, '/testbed')
from solution import add, keep

def test_add_fix():
    assert add(2, 3) == 5

def test_existing_behavior():
    assert keep() == 'stable'
""",
        "test-spec-eval.sh": _SCRIPT,
    }
    for name, contents in files.items():
        (context / name).write_text(contents, encoding="utf-8")

    tag = f"local/swebench-synthetic:{uuid.uuid4().hex[:16]}"
    containers: list[tuple[str, str]] = []
    built = False
    base_tagged = False
    runtime = swebench.SwebenchRuntime(docker_executable=Path(docker))
    run_dir = tmp_path / "evidence"
    run_dir.mkdir(mode=0o700)
    try:
        _docker(docker, "image", "tag", base, base_alias)
        base_tagged = True
        _docker(
            docker,
            "build",
            "--quiet",
            "--pull=false",
            "--network=none",
            "--platform",
            "linux/amd64",
            "--tag",
            tag,
            "--file",
            str(context / "Dockerfile"),
            str(context),
            timeout=900,
        )
        built = True
        digest = _docker(docker, "image", "inspect", "--format={{.Id}}", tag)
        reference = f"{tag}@{digest}"
        test_spec = _test_spec(tmp_path, reference)
        task = SimpleNamespace(instance_id=test_spec.instance_id)
        script_sha = sha256(_SCRIPT.encode()).hexdigest()
        names: set[str] = set()

        patches = (
            (
                "resolved",
                """diff --git a/solution.py b/solution.py
--- a/solution.py
+++ b/solution.py
@@ -1,6 +1,6 @@
 def add(left, right):
-    return left - right
+    return left + right
 

 def keep():
     return "stable"
""",
            ),
            (
                "unresolved",
                """diff --git a/solution.py b/solution.py
--- a/solution.py
+++ b/solution.py
@@ -1,6 +1,7 @@
 def add(left, right):
     return left - right
 

+# Documentation-only change.
 def keep():
     return "stable"
""",
            ),
        )
        for expected, patch_text in patches:
            patch = patch_text.encode()
            patch_sha = sha256(patch).hexdigest()
            handle = f"private://prediction-{patch_sha}.patch"
            (run_dir / handle.removeprefix("private://")).write_bytes(patch)
            names_for_attempt = [
                f"swebench-task-{uuid.uuid4().hex[:16]}",
                f"swebench-grader-{uuid.uuid4().hex[:16]}",
            ]
            for role, name in zip(("task", "grader"), names_for_attempt, strict=True):
                args_builder = (
                    swebench.build_task_container_args
                    if role == "task"
                    else swebench.build_grader_container_args
                )
                args = args_builder(
                    image_reference=reference,
                    image_digest=digest,
                    platform="linux/amd64",
                    limits=_LIMITS,
                    name=name,
                )
                swebench._run(
                    runtime,
                    (str(runtime.docker_executable), *args[1:]),
                    timeout=30,
                    max_output_bytes=_LIMITS["max_output_bytes"],
                )
                volume = f"{name}-workspace"
                containers.append((name, volume))
                swebench._attest_container(
                    runtime,
                    name,
                    _LIMITS,
                    role=role,
                    image_reference=reference,
                    image_digest=digest,
                )
                swebench._run(
                    runtime,
                    (str(runtime.docker_executable), "start", name),
                    timeout=30,
                    max_output_bytes=_LIMITS["max_output_bytes"],
                )
                names.add(name)

                observed = json.loads(_docker(docker, "inspect", "--format={{json .}}", name))
                assert observed["HostConfig"]["NetworkMode"] == "none"
                assert not observed["HostConfig"].get("Binds")
                assert set(observed["Config"]["Env"]) in (
                    {"HOME=/tmp/home", "PATH=/usr/local/bin:/usr/bin:/bin"},
                    {
                        "HOME=/tmp/home",
                        "PATH=/usr/local/bin:/usr/bin:/bin",
                        "TZ=Etc/UTC",
                    },
                )
                mounts = observed["Mounts"]
                assert len(mounts) == 1
                assert mounts[0]["Type"] == "volume"
                assert mounts[0]["Name"] == f"{name}-workspace"
                assert mounts[0]["Destination"] == "/testbed"
                assert mounts[0]["RW"] is True
                assert (
                    _docker(
                        docker,
                        "exec",
                        name,
                        "/bin/sh",
                        "-c",
                        "test ! -S /var/run/docker.sock "
                        "&& test -r /opt/racecraft/grader/tests/test_synthetic.py "
                        "&& test ! -w /opt/racecraft/grader/tests/test_synthetic.py",
                    )
                    == ""
                )

            grader = names_for_attempt[1]
            if expected == "resolved":
                network_probe = _docker(
                    docker,
                    "exec",
                    grader,
                    "/opt/miniconda3/bin/python3",
                    "-B",
                    "-c",
                    "import socket; s=socket.socket(); s.settimeout(2); "
                    "\ntry: s.connect(('1.1.1.1', 443))\n"
                    "except OSError: print('blocked')\n"
                    "else: raise SystemExit('unexpected outbound connection')\n"
                    "finally: s.close()",
                )
                assert network_probe == "blocked"
            capture = swebench._default_grader_executor(
                evidence_dir=run_dir,
                prediction_handle=handle,
                model_patch_sha256=patch_sha,
                test_spec_eval_script=_SCRIPT,
                test_spec_eval_script_sha256=script_sha,
                max_output_bytes=_LIMITS["max_output_bytes"],
                timeout_seconds=_LIMITS["timeout_seconds"],
                docker_executable=str(runtime.docker_executable),
                container_name=grader,
                runner=subprocess.run,
            )
            stdout = capture.get("stdout")
            stderr = capture.get("stderr")
            assert isinstance(stdout, bytes) and isinstance(stderr, bytes)
            output_limit = _LIMITS["max_output_bytes"]
            saved_stdout = stdout[:output_limit]
            saved_stderr = stderr[: max(0, output_limit - len(saved_stdout))]
            stdout_path = run_dir / f"grader-stdout-{patch_sha}.bin"
            stderr_path = run_dir / f"grader-stderr-{patch_sha}.bin"
            stdout_path.write_bytes(saved_stdout)
            stderr_path.write_bytes(saved_stderr)
            output_note = (
                f"stdout_tail={stdout[-4096:].decode('utf-8', errors='replace')!r}; "
                f"stderr_tail={stderr[-4096:].decode('utf-8', errors='replace')!r}; "
                f"captured_bytes={len(stdout) + len(stderr)} "
                f"persisted_bytes={len(saved_stdout) + len(saved_stderr)}; "
                f"raw_stdout={stdout_path}; raw_stderr={stderr_path}"
            )
            exit_code = 0 if expected == "resolved" else 1
            assert capture["phase"] == "patch_ready" and capture["error"] is None, (
                f"{expected} synthetic case did not reach test execution: "
                f"{capture!r}; {output_note}"
            )
            assert capture["eval_launcher_exit_code"] == capture["eval_exit_code"] == exit_code, (
                f"{expected} synthetic case expected exit {exit_code}, got "
                f"launcher={capture['eval_launcher_exit_code']} "
                f"eval={capture['eval_exit_code']}; {output_note}"
            )
            test_line = b"PASSED" if exit_code == 0 else b"FAILED"
            assert test_line + b" tests/test_synthetic.py::test_add_fix" in stdout, output_note

            persisted = swebench._persist_grader_capture(
                capture,
                task=task,
                prediction_sha256=patch_sha,
                eval_script_sha256=script_sha,
                attempt_nonce=uuid.uuid4().hex,
                run_dir=run_dir,
                limits=_LIMITS,
            )
            log = (run_dir / persisted["test_log_path"]).read_bytes()
            assert log.count(f">>>>> Test Exit Code: {exit_code}".encode()) == 1
            cleanup = [
                swebench._cleanup(runtime, name, _LIMITS, workspace_volume=volume)
                for name, volume in reversed(containers)
            ]
            containers.clear()
            assert cleanup == [(True, True), (True, True)]
            score_args = dict(
                capture=persisted,
                task=task,
                agent={"prediction_handle": handle, "model_patch_sha256": patch_sha},
                test_spec=test_spec,
                run_dir=run_dir,
                evidence_dir=run_dir,
                score_executor=score_trusted_run,
                cleanup_evidence=cleanup,
            )
            if expected == "resolved":
                with pytest.raises(swebench.SwebenchError, match="cleanup evidence"):
                    swebench._score_grader_capture(
                        **{**score_args, "cleanup_evidence": [(False, True), cleanup[1]]}
                    )
            assert swebench._score_grader_capture(**score_args) == ("completed", expected), (
                output_note
            )
        assert len(names) == 4
    finally:
        for name, volume in reversed(containers):
            swebench._cleanup(runtime, name, _LIMITS, workspace_volume=volume)
        if built:
            _docker(docker, "image", "rm", tag)
        if base_tagged:
            _docker(docker, "image", "rm", base_alias)
