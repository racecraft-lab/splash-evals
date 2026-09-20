"""User-facing ``local-evals`` command line interface."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any, cast

import typer

from .frontier import ComparisonRefused, compare_frontier, validate_catalog
from .lmstudio import LMStudioError, discover
from .models import SettingEvidenceRecord, SettingEvidenceStatus, StateSnapshot
from .optimization import optimize
from .publication import audit_publication, prepare_publication
from .runs import (
    RunError,
    build_plan,
    compare_runs,
    execute_run,
    get_state_dir,
    load_config,
    load_policies,
    project_root,
    rescore_run,
    resume_run,
    run_scorer_self_test,
)
from .settings import build_export, create_snapshot, persist_snapshot, plan_restore

app = typer.Typer(
    no_args_is_help=True, help="Privacy-first local Splash evaluation through LM Studio."
)
lmstudio_app = typer.Typer(
    no_args_is_help=True, help="Read-only LM Studio discovery and snapshots."
)
settings_app = typer.Typer(
    no_args_is_help=True, help="Export or safely plan restoration of settings."
)
frontier_app = typer.Typer(
    no_args_is_help=True, help="Validate and compare historical frontier evidence."
)
privacy_app = typer.Typer(no_args_is_help=True, help="Run local publication privacy gates.")
publish_app = typer.Typer(no_args_is_help=True, help="Stage allowlisted result exports for review.")
app.add_typer(lmstudio_app, name="lmstudio")
app.add_typer(settings_app, name="settings")
app.add_typer(frontier_app, name="frontier")
app.add_typer(privacy_app, name="privacy")
app.add_typer(publish_app, name="publish")


def _emit(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


def _abort(error: BaseException, *, code: int = 2) -> None:
    _emit({"status": "blocked", "error_type": type(error).__name__, "message": str(error)})
    raise typer.Exit(code=code)


def _origin_and_model(config_name: str = "lmstudio-as-found") -> tuple[str, str | None, str]:
    config = load_config(config_name)
    server = config.get("server") or {}
    model = config.get("model") or {}
    return (
        str(server.get("origin", "http://127.0.0.1:1234")),
        model.get("key") or os.environ.get("LOCAL_EVALS_MODEL"),
        str(server.get("api_key_env", "LM_STUDIO_API_KEY")),
    )


def _stable_discovery_settings(report: Any) -> dict[str, Any]:
    value = report.model_dump(mode="json")
    value.pop("collected_at", None)
    # Human-readable issue detail can vary by process; codes retain the relevant state.
    value["issues"] = [
        {"source": issue["source"], "code": issue["code"], "blocking": issue["blocking"]}
        for issue in value.get("issues", [])
    ]
    return cast(dict[str, Any], value)


def _private_write(directory: Path, name: str, value: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / name
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    os.chmod(path, 0o600)
    return path


@app.command()
def doctor() -> None:
    """Check the local-only workbench and report research blockers."""
    try:
        root = project_root()
        state = get_state_dir(root)
        policies = load_policies(root)
        scorer = run_scorer_self_test()
        origin, selected_model, api_key_env = _origin_and_model()
        try:
            report = discover(origin, selected_model=selected_model, api_key_env=api_key_env)
            discovery = report.model_dump(mode="json")
            blocking_issues = [issue for issue in discovery["issues"] if issue["blocking"]]
            splash_models = [
                model
                for model in discovery["models"]
                if "splash" in model["key"].casefold() and model["loaded"]
            ]
            verified_local = discovery["locality"]["status"] == "verified_local"
        except (LMStudioError, OSError) as error:
            discovery = {"status": "unavailable", "error_type": type(error).__name__}
            blocking_issues = [{"code": "discovery_unavailable"}]
            splash_models = []
            verified_local = False
        tools = {
            name: {"available": shutil.which(name) is not None}
            for name in ("uv", "lms", "evalscope", "gitleaks", "git")
        }
        checks = {
            "external_state": {
                "passed": root.resolve() not in state.resolve().parents
                and state.resolve() != root.resolve(),
                "mode": oct(state.stat().st_mode & 0o777),
            },
            "scorer_self_test": scorer,
            "zero_paid_api_budget": policies["initial_run_limits"]["paid_api_budget_usd"] == 0,
            "single_in_flight": policies["initial_run_limits"]["max_in_flight_requests"] == 1,
            "tools": tools,
            "lmstudio": discovery,
        }
        workbench_ready = checks["external_state"]["passed"] and scorer["passed"]
        primary_ready = (
            workbench_ready and verified_local and bool(splash_models) and not blocking_issues
        )
        _emit(
            {
                "status": "ready"
                if primary_ready
                else "qualified_with_blockers"
                if workbench_ready
                else "blocked",
                "workbench_ready": workbench_ready,
                "primary_research_status": "not_started" if primary_ready else "blocked",
                "primary_blockers": []
                if primary_ready
                else [
                    "A loaded Splash model with verified local execution is required "
                    "before scored research."
                ],
                "checks": checks,
            }
        )
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@lmstudio_app.command("discover")
def lmstudio_discover(
    config: str = typer.Option("lmstudio-as-found", "--config"),
) -> None:
    """Discover relevant LM Studio state without changing it."""
    try:
        origin, selected_model, api_key_env = _origin_and_model(config)
        report = discover(origin, selected_model=selected_model, api_key_env=api_key_env)
        payload = report.model_dump(mode="json")
        state = get_state_dir()
        _private_write(state / "inventory", "inventory.json", payload)
        _private_write(
            state / "inventory",
            "capabilities.json",
            {
                "capabilities": payload["capabilities"],
                "locality": payload["locality"],
                "issues": payload["issues"],
            },
        )
        _emit(payload)
    except (RunError, LMStudioError, OSError, ValueError) as error:
        _abort(error)


@lmstudio_app.command("snapshot")
def lmstudio_snapshot(
    config: str = typer.Option("lmstudio-as-found", "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Preserve relevant as-found settings in private external state."""
    try:
        origin, selected_model, api_key_env = _origin_and_model(config)
        report = discover(origin, selected_model=selected_model, api_key_env=api_key_env)
        stable = _stable_discovery_settings(report)
        fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
        secret_refs = [api_key_env] if os.environ.get(api_key_env) else []
        snapshot = create_snapshot(
            stable, source_fingerprint=fingerprint, secret_references=secret_refs
        )
        if dry_run:
            _emit(
                {
                    "status": "dry_run",
                    "snapshot": snapshot.model_dump(mode="json"),
                    "persisted": False,
                }
            )
            return
        directory = get_state_dir() / "snapshots"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = persist_snapshot(snapshot, directory)
        _emit(
            {
                "status": "captured",
                "snapshot_id": snapshot.snapshot_id,
                "private_path": str(path),
                "publication_eligible": False,
            }
        )
    except (RunError, LMStudioError, OSError, ValueError) as error:
        _abort(error)


@app.command()
def plan(
    suite: str = typer.Option(..., "--suite"),
    config: str = typer.Option(..., "--config"),
    allow_expanded: bool = typer.Option(False, "--allow-expanded"),
    dry_run: bool = typer.Option(True, "--dry-run/--execute"),
) -> None:
    """Resolve samples, repetitions, budgets, blockers, and outputs before execution."""
    del dry_run  # Planning is always read-only; retained for interface consistency.
    try:
        _emit(build_plan(suite, config, allow_expanded=allow_expanded))
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@app.command("run")
def run_command(
    suite: str = typer.Option(..., "--suite"),
    config: str = typer.Option(..., "--config"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    allow_expanded: bool = typer.Option(False, "--allow-expanded"),
) -> None:
    """Run a fixed suite through one local LM Studio request per attempt."""
    try:
        _emit(execute_run(suite, config, dry_run=dry_run, allow_expanded=allow_expanded))
    except (RunError, OSError, ValueError) as error:
        _abort(error)


def optimize_command(
    matrix: str = typer.Option(..., "--matrix"),
    baseline: str = typer.Option(..., "--baseline"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Run at most three calibration-only candidates and freeze one profile."""
    try:
        _emit(optimize(matrix, baseline, dry_run=dry_run))
    except (RunError, OSError, ValueError) as error:
        _abort(error)


# Keep the required public command name while avoiding shadowing the imported function.
app.command("optimize")(optimize_command)


@app.command()
def compare(
    first_run: str = typer.Option(..., "--runs", help="First run ID."),
    second_run: str = typer.Argument(..., help="Second run ID."),
) -> None:
    """Compare exactly two protocol-matched local runs."""
    try:
        _emit(compare_runs([first_run, second_run]))
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@settings_app.command("export")
def settings_export(config: str = typer.Option(..., "--config")) -> None:
    """Export separate generation/load/effective profiles outside Git."""
    try:
        profile = load_config(config)
        model = profile.get("model") or {}
        metadata = profile.get("metadata") or {}
        load_records = [
            SettingEvidenceRecord(
                name=str(name),
                requested=value,
                transmitted=None,
                effective=(metadata.get("load_effective") or {}).get(name)
                if isinstance(metadata.get("load_effective"), dict)
                else None,
                status=SettingEvidenceStatus.VERIFIED
                if isinstance(metadata.get("load_effective"), dict)
                and name in metadata["load_effective"]
                else SettingEvidenceStatus.UNKNOWN,
                evidence=("effective LM Studio read-back",)
                if isinstance(metadata.get("load_effective"), dict)
                and name in metadata["load_effective"]
                else (),
            )
            for name, value in (profile.get("load_requested") or {}).items()
        ]
        generation_records = [
            SettingEvidenceRecord(
                name=str(name),
                requested=value,
                transmitted=value,
                effective=None,
                status=SettingEvidenceStatus.ACCEPTED_UNVERIFIED,
                evidence=(
                    "request field prepared; behavioral effect not inferred from HTTP success",
                ),
            )
            for name, value in (profile.get("operation_requested") or {}).items()
            if value is not None
        ]
        document = build_export(
            profile_id=config,
            model_identity={
                key: value for key, value in model.items() if key not in {"instance_id"}
            },
            load_settings=load_records,
            generation_settings=generation_records,
        )
        output = get_state_dir() / "settings_exports" / config
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        _private_write(
            output,
            "generation-settings.json",
            {"schema_version": 1, "settings": document["generation_settings"]},
        )
        _private_write(
            output,
            "load-profile.json",
            {"schema_version": 1, "settings": document["load_settings"]},
        )
        _private_write(output, "effective-settings.json", document)
        instructions = output / "INSTRUCTIONS.md"
        instructions.write_text(
            "# LM Studio settings use\n\n"
            "This export is a project profile, not a validated native LM Studio preset. "
            "Apply generation values per request. Apply load values only through an "
            "installed-version interface "
            "that has been verified, after checking the model is idle and preserving a snapshot. "
            "Read back effective load state after any reload.\n",
            encoding="utf-8",
        )
        os.chmod(instructions, 0o600)
        _emit(
            {
                "status": "exported",
                "config": config,
                "directory": str(output),
                "native_preset_status": "not_validated",
                "publication_eligible": False,
            }
        )
    except (RunError, OSError, ValueError) as error:
        _abort(error)


def _load_snapshot(snapshot_id: str) -> StateSnapshot:
    if snapshot_id != Path(snapshot_id).name or any(
        part in snapshot_id for part in ("/", "\\", "..")
    ):
        raise RunError("snapshot ID must be a simple identifier")
    path = get_state_dir() / "snapshots" / f"{snapshot_id}.json"
    if not path.is_file():
        raise RunError(f"snapshot not found: {snapshot_id}")
    return StateSnapshot.model_validate_json(path.read_text(encoding="utf-8"))


@settings_app.command("restore")
def settings_restore(
    snapshot_id: str = typer.Option(..., "--snapshot"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Plan restoration and refuse intervening changes or unverified writers."""
    try:
        snapshot = _load_snapshot(snapshot_id)
        origin, selected_model, api_key_env = _origin_and_model()
        current = _stable_discovery_settings(
            discover(origin, selected_model=selected_model, api_key_env=api_key_env)
        )
        plan = plan_restore(snapshot, current_settings=current, journal=[])
        payload = plan.model_dump(mode="json")
        if dry_run or payload["status"] == "no_changes":
            _emit(
                {
                    "status": "dry_run" if dry_run else "no_changes",
                    "restore_plan": payload,
                    "writes_performed": False,
                }
            )
            return
        if payload["status"] == "blocked_intervening_change":
            raise RunError(
                "restore refused: current LM Studio state differs from the expected "
                "project-written state"
            )
        raise RunError(
            "restore apply is blocked: no installed-version settings writer has been "
            "qualified; use the generated manual rollback plan"
        )
    except (RunError, LMStudioError, OSError, ValueError) as error:
        _abort(error)


def _select_dashboard_port(preferred: int) -> int:
    for port in range(preferred, preferred + 21):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RunError("no free loopback dashboard port found in the bounded range")


@app.command()
def view(
    port: int = typer.Option(9000, "--port", min=1024, max=65515),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Launch the local EvalScope dashboard without claiming another process's port."""
    try:
        executable = shutil.which("evalscope")
        if not executable:
            raise RunError(
                "evalscope executable is unavailable; install the locked optional dependency"
            )
        selected = _select_dashboard_port(port)
        command = [
            executable,
            "service",
            "--host",
            "127.0.0.1",
            "--port",
            str(selected),
            "--outputs",
            str(get_state_dir() / "outputs"),
        ]
        if dry_run:
            _emit(
                {
                    "status": "dry_run",
                    "host": "127.0.0.1",
                    "port": selected,
                    "port_changed": selected != port,
                    "command": ["evalscope", *command[1:]],
                }
            )
            return
        completed = subprocess.run(command, check=False)  # noqa: S603 - resolved executable, fixed args.
        if completed.returncode != 0:
            raise RunError(f"EvalScope dashboard exited with status {completed.returncode}")
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@app.command()
def resume(
    run_id: str = typer.Option(..., "--run"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Resume only unfinished attempts under the exact original fingerprint."""
    try:
        _emit(resume_run(run_id, dry_run=dry_run))
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@app.command()
def rescore(
    run_id: str = typer.Option(..., "--run"),
    scorer_version: str = typer.Option(..., "--scorer-version"),
) -> None:
    """Write a derived score from archived predictions without new inference."""
    try:
        _emit(rescore_run(run_id, scorer_version))
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@frontier_app.command("validate")
def frontier_validate(
    catalog: str = typer.Option(..., "--catalog"),
    require_coverage: bool = typer.Option(
        False,
        "--require-coverage",
        help="Also require exact historical comparison coverage across the configured cohort.",
    ),
) -> None:
    """Validate catalog integrity and optionally require exact comparison coverage."""
    try:
        result = validate_catalog(catalog)
        _emit(result)
        schema_failed = not result["schema_valid"]
        required_coverage_failed = require_coverage and not result["valid"]
        if schema_failed or required_coverage_failed:
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@frontier_app.command("compare")
def frontier_compare(
    run_id: str = typer.Option(..., "--run"),
    catalog: str = typer.Option(..., "--catalog"),
) -> None:
    """Compare only when benchmark and protocol evidence actually match."""
    try:
        _emit(compare_frontier(run_id, catalog))
    except ComparisonRefused as error:
        try:
            report = json.loads(str(error))
        except json.JSONDecodeError:
            _abort(error)
        _emit(report)
        raise typer.Exit(code=3) from None
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@privacy_app.command("audit")
def privacy_audit(scope: str = typer.Option("publication", "--scope")) -> None:
    """Audit exact source/history scope with redacted diagnostics."""
    try:
        result = audit_publication(scope=scope)
        _emit(result)
        if result["status"] != "pass":
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except (RunError, OSError, ValueError) as error:
        _abort(error)


@publish_app.command("prepare")
def publish_prepare(
    run_id: str = typer.Option(..., "--run"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Stage a fresh aggregate-only export for review; never upload it."""
    try:
        result = prepare_publication(run_id, dry_run=dry_run)
        _emit(result)
        if result["status"].endswith("blocked"):
            raise typer.Exit(code=2)
    except typer.Exit:
        raise
    except (RunError, OSError, ValueError) as error:
        _abort(error)


if __name__ == "__main__":
    app()
