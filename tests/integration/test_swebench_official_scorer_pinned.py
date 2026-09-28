"""Explicit real-package parity fixture for SWE-bench 5.0.2.

Run this file separately with SWEBENCH_PINNED_WHEEL and
SWEBENCH_DEPENDENCY_WHEELS set to the locked local wheel and wheel directory.
It is skipped without that explicit fixture input; skipped parity is not
reported as a passing result.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

from local_evals.swebench_official_scorer import verify_pinned_wheel

_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
_DEPENDENCY_WHEELS = {
    "unidiff-1.0.1-py3-none-any.whl": (
        "5e2ca461eda8a4c761ad0922e3e8a65742f359dc60301a3a055b70fd7c158680"
    ),
}
_SUBPROCESS = textwrap.dedent(
    r"""
    import sys
    import tempfile
    import types
    from dataclasses import replace
    from pathlib import Path

    source_root = Path(sys.argv[2])
    wheel = Path(sys.argv[1])
    sys.path.insert(0, str(source_root))
    from local_evals.swebench_official_scorer import (
        PARSER_AUTHENTICITY_LIMITATION,
        SWEBENCH_WHEEL_SHA256,
        TrustedRun,
        patch_digest,
        score_trusted_run,
        verify_pinned_wheel,
    )

    assert verify_pinned_wheel(wheel) in SWEBENCH_WHEEL_SHA256
    import swebench

    harness = types.ModuleType("swebench.harness")
    harness.__path__ = [str(Path(swebench.__file__).parent / "harness")]
    sys.modules["swebench.harness"] = harness
    from swebench.types import TestSpec

    instance_id = "demo__repo-1"
    model_patch = "diff --git a/demo.py b/demo.py\n+print('trusted fixture')\n"
    patch_sha = patch_digest(model_patch)
    spec = TestSpec(
        instance_id=instance_id,
        image="sha256:" + "a" * 64,
        eval_script_list=[],
        repo="demo/repo",
        version="5.0.2",
        FAIL_TO_PASS=["test_fix"],
        PASS_TO_PASS=["test_keep"],
        log_parser="parse_log_pytest",
        eval_type="pass_and_fail",
    )

    def score(
        work,
        content,
        exit_code,
        stdout="",
        *,
        timed_out=False,
        cleanup_ok=True,
        log_parser=None,
        base_spec=None,
    ):
        log = work / "test_output.txt"
        if content is not None:
            log.write_text(content)
        else:
            log.unlink(missing_ok=True)
        base = spec if base_spec is None else base_spec
        run_spec = (
            base if log_parser is None else replace(base, log_parser=log_parser)
        )
        run = TrustedRun(
            test_spec=run_spec,
            instance_id=instance_id,
            prediction_handle=f"private://prediction-{patch_sha}.patch",
            patch_sha256=patch_sha,
            model_patch=model_patch,
            model_name_or_path="fixture/model",
            test_log_path=log,
            trusted_evidence_root=work,
            exit_code=exit_code,
            timed_out=timed_out,
            cleanup_ok=cleanup_ok,
            stdout=stdout,
        )
        return score_trusted_run(run)

    def require_real(result, expected_status):
        if result.status == "dependency_blocked":
            raise RuntimeError(f"official scorer dependency blocked: {result.reason}")
        assert result.status == expected_status

    gold_log = (
        ">>>>> Applied Patch\n"
        ">>>>> Start Test Output\n"
        "PASSED test_fix\n"
        "PASSED test_keep\n"
        ">>>>> End Test Output\n"
        ">>>>> Test Exit Code: 0\n"
    )
    failed_log = (
        ">>>>> Applied Patch\n"
        ">>>>> Start Test Output\n"
        "FAILED test_fix - assertion\n"
        "PASSED test_keep\n"
        ">>>>> End Test Output\n"
        ">>>>> Test Exit Code: 1\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        gold = score(work, gold_log, 0, '{"resolved": true, "score": 1}')
        require_real(gold, "resolved")
        assert gold.resolved is True
        assert gold.official is True
        assert gold.limitation == PARSER_AUTHENTICITY_LIMITATION
        failed = score(work, failed_log, 1)
        require_real(failed, "unresolved")
        assert failed.resolved is False
        json_only = score(work, '{"resolved": true}\n', 0)
        assert json_only.status != "resolved"
        assert json_only.resolved is not True
        parser_error = score(work, gold_log, 0, log_parser="missing_parser")
        require_real(parser_error, "official_parser_error")
        assert parser_error.resolved is None
        timeout = score(work, None, 0, timed_out=True)
        assert timeout.status == "timeout" and timeout.resolved is None
        cleanup = score(work, None, 0, cleanup_ok=False)
        assert cleanup.status == "cleanup_failed" and cleanup.resolved is None

    # Harness-shaped log: raw eval-script output (xtrace markers included), then the
    # harness's trailing end marker and trusted exit line. Setup noise such as an
    # offline pip failure precedes the script's own start marker.
    django_spec = replace(
        spec,
        FAIL_TO_PASS=["test_fix (demo.tests.DemoTests)"],
        PASS_TO_PASS=["Keeps working."],
        log_parser="parse_log_django",
    )
    script_output = (
        "+ python -m pip install -e .\n"
        "ERROR: Could not find a version that satisfies the requirement setuptools\n"
        "ERROR: No matching distribution found for setuptools\n"
        "+ : '>>>>> Start Test Output'\n"
        "test_fix (demo.tests.DemoTests) ... ok\n"
        "Keeps working. ... ok\n"
        "+ : '>>>>> End Test Output'\n"
        ">>>>> Test Exit Code [host-neutralized]\n"
    )
    harness_log = script_output + "\n>>>>> End Test Output\n>>>>> Test Exit Code: 0\n"
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        harness = score(work, harness_log, 0, base_spec=django_spec)
        require_real(harness, "resolved")
        # A leading harness marker makes the parser slice only the setup noise.
        wrapped = score(
            work, ">>>>> Start Test Output\n" + harness_log, 0, base_spec=django_spec
        )
        require_real(wrapped, "unresolved")
    """
)


def test_real_pinned_wheel_parity_in_isolated_subprocess() -> None:
    wheel_value = os.environ.get("SWEBENCH_PINNED_WHEEL")
    dependency_value = os.environ.get("SWEBENCH_DEPENDENCY_WHEELS")
    if not wheel_value or not dependency_value:
        pytest.skip(
            "set SWEBENCH_PINNED_WHEEL and SWEBENCH_DEPENDENCY_WHEELS for explicit real parity"
        )
    wheel = Path(wheel_value)
    dependency_wheels = Path(dependency_value)
    digest = verify_pinned_wheel(wheel)
    with (
        tempfile.TemporaryDirectory() as extracted,
        tempfile.TemporaryDirectory() as dependencies,
    ):
        for filename, expected_hash in _DEPENDENCY_WHEELS.items():
            dependency = dependency_wheels / filename
            assert dependency.is_file() and not dependency.is_symlink()
            assert sha256(dependency.read_bytes()).hexdigest() == expected_hash
            with zipfile.ZipFile(dependency) as archive:
                archive.extractall(dependencies)
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(extracted)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_SOURCE_ROOT), extracted, dependencies, env.get("PYTHONPATH", "")]
        )
        completed = subprocess.run(  # noqa: S603 - verified local fixture inputs
            [sys.executable, "-c", _SUBPROCESS, str(wheel), str(_SOURCE_ROOT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    assert completed.returncode == 0, (
        f"pinned wheel parity subprocess failed for {digest}: "
        f"{completed.stdout}\n{completed.stderr}"
    )
