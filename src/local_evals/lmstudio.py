"""Read-only LM Studio discovery and a fail-closed single-request transport."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin

import httpx

from .models import (
    CapabilityRecord,
    CapabilityStatus,
    DiscoveryIssue,
    DiscoveryReport,
    InferenceUsage,
    LocalityEvidence,
    ModelRecord,
    ModelRecordSource,
)
from .preflight import (
    EndpointPolicyError,
    RequestBudget,
    classify_locality,
    lms_device_is_local,
    require_verified_local,
    validate_loopback_url,
    validate_redirect_target,
)


class LMStudioError(RuntimeError):
    """A sanitized LM Studio discovery or transport failure."""


class LMStudioRedirectError(LMStudioError):
    pass


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


CommandRunner = Callable[[Sequence[str]], CommandResult]


_ALLOWED_COMMANDS = {
    ("--version",),
    ("ls", "--json"),
    ("ps", "--json"),
    ("runtime", "ls"),
    ("server", "status"),
    ("link", "status"),
}
_PRIVATE_PATH = re.compile(
    r"/" + r"(?:Users|home)/[^\s\"']+" + r"|[A-Za-z]:\\" + r"Users\\[^\s\"']+"
)
_SENSITIVE_KEYS = re.compile(
    r"(^|_)(password|secret|access_token|api_token|bearer_token)($|_)", re.I
)
_MACOS_LM_STUDIO_CLIS = (Path("/Applications/LM Studio.app/Contents/Resources/app/.webpack/lms"),)
_MACOS_BIONIC_CLIS = (Path("/Applications/Bionic.app/Contents/Resources/app/.webpack-bionic/lms"),)
_LM_STUDIO_HOME_POINTER = ".lmstudio-home-pointer"
_MAX_HOME_POINTER_BYTES = 4096


def _file_sha256(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _pointed_lm_studio_home(user_home: Path) -> Path | None:
    pointer = user_home / _LM_STUDIO_HOME_POINTER
    if pointer.is_symlink():
        raise LMStudioError("LM Studio home pointer cannot be resolved safely")
    if not pointer.exists():
        return None
    try:
        if not pointer.is_file() or pointer.stat().st_size > _MAX_HOME_POINTER_BYTES:
            raise LMStudioError("LM Studio home pointer cannot be resolved safely")
        pointer_value = pointer.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise LMStudioError("LM Studio home pointer cannot be resolved safely") from exc
    pointed_root = Path(pointer_value)
    if not pointer_value or not pointed_root.is_absolute():
        raise LMStudioError("LM Studio home pointer cannot be resolved safely")
    return pointed_root


def _official_llmster_clis(user_home: Path) -> tuple[Path, ...]:
    """Resolve official standalone CLI cache entries without executing them."""

    roots = [user_home / ".lmstudio", user_home / ".cache" / "lm-studio"]
    pointed_root = _pointed_lm_studio_home(user_home)
    if pointed_root is not None:
        roots.insert(0, pointed_root)

    candidates: list[Path] = []
    for root in roots:
        candidate = root / "bin" / "lms"
        if candidate.is_file():
            try:
                candidates.append(candidate.resolve(strict=True))
            except (OSError, RuntimeError) as exc:
                raise LMStudioError("LM Studio CLI cache cannot be resolved safely") from exc
    return tuple(dict.fromkeys(candidates))


def verify_lms_cli_executable(
    executable: str | os.PathLike[str],
    *,
    platform_name: str = sys.platform,
    lm_studio_paths: Sequence[Path] = _MACOS_LM_STUDIO_CLIS,
    bionic_paths: Sequence[Path] = _MACOS_BIONIC_CLIS,
) -> Path:
    """Verify a macOS ``lms`` executable from an official install, without running it.

    A desktop-app CLI is accepted only by byte identity. A standalone llmster
    CLI is accepted only when the executable resolves from the user's official
    LM Studio home pointer or cache entry. Arbitrary PATH executables fail
    closed even when their bytes copy an official standalone CLI.
    """

    try:
        resolved = Path(executable).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LMStudioError("lms executable cannot be resolved safely") from exc
    if not resolved.is_file():
        raise LMStudioError("lms executable is not a regular file")
    if platform_name != "darwin":
        return resolved

    lm_candidates = [path.resolve() for path in lm_studio_paths if path.is_file()]
    bionic_candidates = [path.resolve() for path in bionic_paths if path.is_file()]
    try:
        llmster_candidates = _official_llmster_clis(Path.home())
    except (OSError, RuntimeError) as exc:
        raise LMStudioError("LM Studio CLI cache cannot be resolved safely") from exc

    try:
        executable_hash = _file_sha256(resolved)
        bionic_hashes = {_file_sha256(path) for path in bionic_candidates}
        lm_studio_hashes = {_file_sha256(path) for path in lm_candidates}
    except OSError as exc:
        raise LMStudioError("lms executable identity could not be verified") from exc
    if executable_hash in bionic_hashes:
        raise LMStudioError("lms executable resolves to the conflicting Bionic CLI")
    if resolved not in llmster_candidates and executable_hash not in lm_studio_hashes:
        raise LMStudioError("lms executable does not match the installed LM Studio CLI")
    return resolved


def _default_runner(args: Sequence[str]) -> CommandResult:
    if tuple(args) not in _ALLOWED_COMMANDS:
        raise LMStudioError("unsupported read-only lms discovery command")
    executable = shutil.which("lms")
    if executable is None:
        raise LMStudioError("lms executable is unavailable")
    verified_executable = verify_lms_cli_executable(executable)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SHELL"}
    }
    completed = subprocess.run(  # noqa: S603 - executable and arguments are allowlisted above.
        [str(verified_executable), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        env=environment,
    )
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _redact_text(value: str) -> str:
    return _PRIVATE_PATH.sub("<private-path>", value).strip()


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "<redacted>" if _SENSITIVE_KEYS.search(str(key)) else _sanitize(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _parse_json(result: CommandResult, source: str) -> Any:
    if result.returncode != 0:
        raise LMStudioError(f"{source} discovery failed")
    try:
        return _sanitize(json.loads(result.stdout))
    except json.JSONDecodeError as exc:
        raise LMStudioError(f"{source} did not return valid JSON") from exc


def _objects(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        for key in ("data", "models", "loadedModels", "items"):
            candidate = value.get(key)
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, Mapping)]
        return [value]
    return []


def _first_string(item: Mapping[str, Any], names: Sequence[str]) -> str | None:
    for name in names:
        value = item.get(name)
        if isinstance(value, str) and value:
            return _redact_text(value)
    return None


def _quantization_name(item: Mapping[str, Any]) -> str | None:
    quantization = item.get("quantization")
    if isinstance(quantization, Mapping):
        return _first_string(quantization, ("name",))
    return quantization if isinstance(quantization, str) else None


def model_keys_equivalent(cli_key: str, native_key: str, publisher: str | None) -> bool:
    """Match an exact key or one exact, case-sensitive publisher qualification."""
    if cli_key == native_key:
        return True
    if not publisher or "/" in publisher:
        return False
    return native_key == f"{publisher}/{cli_key}" or cli_key == f"{publisher}/{native_key}"


def _native_reasoning(item: Mapping[str, Any]) -> tuple[tuple[str, ...], str | None]:
    capabilities = item.get("capabilities")
    if not isinstance(capabilities, Mapping):
        return (), None
    reasoning = capabilities.get("reasoning")
    if not isinstance(reasoning, Mapping):
        return (), None
    options = reasoning.get("allowed_options")
    allowed = (
        tuple(value for value in options if isinstance(value, str) and value)
        if isinstance(options, list)
        else ()
    )
    default = reasoning.get("default")
    return allowed, default if isinstance(default, str) and default else None


def _native_loaded_instance_ids(item: Mapping[str, Any]) -> tuple[str, ...]:
    instances = item.get("loaded_instances")
    if not isinstance(instances, list):
        return ()
    return tuple(
        value
        for instance in instances
        if isinstance(instance, Mapping)
        for value in [instance.get("id")]
        if isinstance(value, str) and value and not _PRIVATE_PATH.search(value)
    )


def _model_record_from_source(
    item: Mapping[str, Any], *, loaded: bool, source: ModelRecordSource
) -> ModelRecord | None:
    key = _first_string(item, ("modelKey", "key", "id", "model", "identifier"))
    if not key:
        return None
    selected_variant = _first_string(item, ("variant", "selectedVariant"))
    if source is ModelRecordSource.NATIVE_REST:
        selected_variant = _first_string(item, ("selected_variant", "selectedVariant"))
        loaded = bool(item.get("loaded_instances"))
    reasoning_allowed, reasoning_default = _native_reasoning(item)
    return ModelRecord(
        key=key,
        source=source,
        instance_id=_first_string(
            item, ("instanceIdentifier", "instance_id", "instanceId", "identifier")
        )
        if source is ModelRecordSource.LMS_CLI_LOADED
        else None,
        display_name=_first_string(item, ("display_name", "displayName")),
        publisher=_first_string(item, ("publisher",)),
        architecture=_first_string(item, ("architecture", "arch")),
        model_format=_first_string(item, ("format", "compatibility_type")),
        model_type=_first_string(item, ("type",)),
        quantization=_quantization_name(item),
        selected_variant=selected_variant,
        file_revision=_first_string(item, ("revision", "fileRevision", "sha")),
        engine=_first_string(item, ("engine", "runtimeName")),
        engine_version=_first_string(item, ("engineVersion", "runtimeVersion")),
        loaded=loaded,
        loaded_instance_ids=_native_loaded_instance_ids(item)
        if source is ModelRecordSource.NATIVE_REST
        else (),
        reasoning_allowed=reasoning_allowed,
        reasoning_default=reasoning_default,
    )


def _extract_models(
    *payloads: tuple[Any, bool, ModelRecordSource],
) -> tuple[ModelRecord, ...]:
    by_identity: dict[tuple[ModelRecordSource, str, str | None], ModelRecord] = {}
    for payload, loaded, source in payloads:
        for item in _objects(payload):
            record = _model_record_from_source(item, loaded=loaded, source=source)
            if record is None:
                continue
            identity = (record.source, record.key, record.instance_id)
            prior = by_identity.get(identity)
            if prior is None or (record.loaded and not prior.loaded):
                by_identity[identity] = record
    return tuple(
        sorted(
            by_identity.values(),
            key=lambda item: (item.key, item.source.value, item.instance_id or ""),
        )
    )


def _find_value(value: Any, names: set[str]) -> Any:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in names:
                return child
            found = _find_value(child, names)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, names)
            if found is not None:
                return found
    return None


def _link_state(text: str | None) -> str:
    if not text:
        return "unknown"
    normalized = " ".join(text.lower().split())
    if "not linked" in normalized or "disconnected" in normalized or "disabled" in normalized:
        return "not_linked"
    if "linked" in normalized or "connected" in normalized:
        return "linked"
    return "unknown"


def classify_model_instance_locality(
    loaded_payload: Any,
    *,
    model_or_instance_id: str | None,
    lm_link_state: str | None,
) -> LocalityEvidence:
    """Apply the LMS device rule to one explicitly selected loaded instance.

    With multiple loaded instances, omission of the target is ambiguous by
    design: a local target and a linked draft/embedding instance can coexist.
    """

    items = _objects(loaded_payload)
    if model_or_instance_id is None:
        return classify_locality(
            endpoint_loopback=True,
            lm_link_state=lm_link_state,
            local_instance_evidence=False,
        )
    matches = [
        item
        for item in items
        if model_or_instance_id
        == _first_string(
            item,
            ("instanceIdentifier", "instance_id", "instanceId", "identifier"),
        )
    ]
    if len(matches) != 1:
        return classify_locality(
            endpoint_loopback=True,
            lm_link_state=lm_link_state,
            local_instance_evidence=False,
        )
    selected = matches[0]
    if "deviceIdentifier" not in selected:
        return classify_locality(
            endpoint_loopback=True,
            lm_link_state=lm_link_state,
            local_instance_evidence=False,
        )
    device_id = selected.get("deviceIdentifier")
    local_device_id = _find_value(loaded_payload, {"localDeviceIdentifier"})
    if device_id is not None and not isinstance(device_id, str):
        local = False
    else:
        local = lms_device_is_local(
            device_identifier=device_id,
            local_device_identifier=local_device_id if isinstance(local_device_id, str) else None,
        )
    device_name = (
        "linked-peer" if _first_string(selected, ("deviceName", "deviceIdentifier")) else None
    )
    return classify_locality(
        endpoint_loopback=True,
        lm_link_state=lm_link_state,
        local_instance_evidence=local,
        execution_device="local" if local else device_name,
    )


class LMStudioClient:
    """HTTP client constrained to one qualified loopback origin."""

    def __init__(
        self,
        origin: str,
        *,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        endpoint = validate_loopback_url(origin)
        self.endpoint = endpoint
        resolved_host = endpoint.resolved_addresses[0]
        network_host = f"[{resolved_host}]" if ":" in resolved_host else resolved_host
        scheme = endpoint.url.split(":", 1)[0]
        self._origin = f"{scheme}://{network_host}:{endpoint.port}"
        headers = {"Host": endpoint.host}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            headers=headers,
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> LMStudioClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, *, json_body: Any = None) -> dict[str, Any]:
        if not path.startswith("/") or path.startswith("//"):
            raise LMStudioError("LM Studio request path must be origin-relative")
        url = urljoin(f"{self._origin}/", path.lstrip("/"))
        validate_loopback_url(url)
        try:
            response = self._client.request(method, url, json=json_body)
        except httpx.HTTPError as exc:
            raise LMStudioError("LM Studio request failed") from exc
        if response.is_redirect:
            location = response.headers.get("location", "")
            try:
                validate_redirect_target(url, location)
            except EndpointPolicyError as exc:
                raise LMStudioRedirectError("LM Studio returned a non-local redirect") from exc
            raise LMStudioRedirectError("LM Studio redirects are not followed")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LMStudioError(f"LM Studio returned HTTP {response.status_code}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise LMStudioError("LM Studio returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise LMStudioError("LM Studio JSON response must be an object")
        return cast(dict[str, Any], _sanitize(payload))

    def get_json(self, path: str) -> dict[str, Any]:
        return self._request("GET", path)

    def post_json(self, path: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return self._request("POST", path, json_body=dict(body))


def _rest_inventory(
    client: LMStudioClient,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[DiscoveryIssue]]:
    issues: list[DiscoveryIssue] = []
    management: dict[str, Any] | None = None
    compatibility: dict[str, Any] | None = None
    for path, label in (("/api/v1/models", "native_models"), ("/v1/models", "openai_models")):
        try:
            result = client.get_json(path)
        except LMStudioError as exc:
            issues.append(
                DiscoveryIssue(source=label, code="unavailable", detail=str(exc), blocking=False)
            )
        else:
            if label == "native_models":
                management = result
            else:
                compatibility = result
    return management, compatibility, issues


def discover(
    base_url: str = "http://127.0.0.1:1234",
    *,
    api_key_env: str = "LM_STUDIO_API_KEY",
    selected_model: str | None = None,
    runner: CommandRunner = _default_runner,
    transport: httpx.BaseTransport | None = None,
) -> DiscoveryReport:
    """Combine read-only CLI and REST evidence without returning secrets or file paths."""

    endpoint = validate_loopback_url(base_url)
    issues: list[DiscoveryIssue] = []
    source_status: dict[str, str] = {}
    cli_payloads: dict[str, Any] = {}
    text_payloads: dict[str, str] = {}

    commands = {
        "cli_version": ("--version",),
        "downloaded_models": ("ls", "--json"),
        "loaded_models": ("ps", "--json"),
        "runtimes": ("runtime", "ls"),
        "server": ("server", "status"),
        "link": ("link", "status"),
    }
    for source, args in commands.items():
        try:
            result = runner(args)
            if source in {"downloaded_models", "loaded_models"}:
                cli_payloads[source] = _parse_json(result, source)
            elif result.returncode == 0:
                text_payloads[source] = _redact_text(result.stdout)
            else:
                raise LMStudioError(f"{source} discovery failed")
        except (LMStudioError, OSError, subprocess.SubprocessError) as exc:
            source_status[source] = "unavailable"
            issues.append(
                DiscoveryIssue(source=source, code="unavailable", detail=str(exc), blocking=False)
            )
        else:
            source_status[source] = "ok"

    api_key = os.environ.get(api_key_env)
    with LMStudioClient(base_url, api_key=api_key, transport=transport) as client:
        native_models, openai_models, rest_issues = _rest_inventory(client)
    issues.extend(rest_issues)
    source_status["native_models"] = "ok" if native_models is not None else "unavailable"
    source_status["openai_models"] = "ok" if openai_models is not None else "unavailable"

    loaded_payload = cli_payloads.get("loaded_models")
    has_loaded = bool(_objects(loaded_payload))
    link_state = _link_state(text_payloads.get("link"))
    locality = classify_model_instance_locality(
        loaded_payload,
        model_or_instance_id=selected_model,
        lm_link_state=link_state,
    )
    if not has_loaded:
        issues.append(
            DiscoveryIssue(
                source="loaded_models",
                code="no_loaded_instance",
                detail="no loaded model instance was available for locality attribution",
                blocking=True,
            )
        )

    models = _extract_models(
        (
            cli_payloads.get("downloaded_models"),
            False,
            ModelRecordSource.LMS_CLI_DOWNLOADED,
        ),
        (loaded_payload, True, ModelRecordSource.LMS_CLI_LOADED),
        (native_models, False, ModelRecordSource.NATIVE_REST),
        (openai_models, False, ModelRecordSource.OPENAI_COMPAT),
    )
    capabilities = (
        CapabilityRecord(
            name="openai_chat_completions",
            status=(
                CapabilityStatus.SUPPORTED
                if openai_models is not None
                else CapabilityStatus.UNKNOWN
            ),
            interface="/v1/chat/completions",
            evidence=("OpenAI-compatible model inventory reachable",)
            if openai_models is not None
            else (),
        ),
        CapabilityRecord(
            name="native_model_management",
            status=(
                CapabilityStatus.SUPPORTED
                if native_models is not None
                else CapabilityStatus.UNKNOWN
            ),
            interface="/api/v1/models",
            evidence=("native model inventory reachable",) if native_models is not None else (),
        ),
    )
    cli_version = text_payloads.get("cli_version") or None
    return DiscoveryReport(
        endpoint=endpoint,
        locality=locality,
        cli_version=cli_version,
        models=models,
        capabilities=capabilities,
        issues=tuple(issues),
        source_status=source_status,
    )


def parse_usage(response: Mapping[str, Any]) -> InferenceUsage:
    usage = response.get("stats") or response.get("usage")
    if not isinstance(usage, Mapping):
        return InferenceUsage()

    def value(name: str) -> int | None:
        raw = usage.get(name)
        return raw if isinstance(raw, int) and raw >= 0 else None

    prompt = value("input_tokens")
    completion = value("total_output_tokens")
    return InferenceUsage(
        prompt_tokens=prompt if prompt is not None else value("prompt_tokens"),
        completion_tokens=completion if completion is not None else value("completion_tokens"),
        total_tokens=value("total_tokens"),
    )


def _native_chat_payload(
    model: str,
    messages: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    max_tokens = params.get("max_output_tokens", params.get("max_tokens"))
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise LMStudioError("a finite positive output-token limit is required")
    if {"model", "messages", "input"}.intersection(params):
        raise LMStudioError("request parameters must not replace model or messages")
    if params.get("stream") is True:
        raise LMStudioError("chat_once supports one non-streaming response only")
    if len(messages) != 1:
        raise LMStudioError("native chat accepts exactly one user text input in this evaluator")
    message = messages[0]
    content = message.get("content")
    if message.get("role") != "user" or not isinstance(content, str):
        raise LMStudioError("native chat accepts exactly one user text input in this evaluator")
    reasoning = params.get("reasoning")
    if reasoning not in {"off", "low", "medium", "high", "on"}:
        raise LMStudioError("an explicit supported reasoning setting is required")
    wire = {
        key: value
        for key, value in params.items()
        if key in {"temperature", "top_p", "top_k", "max_output_tokens", "reasoning"}
        and value is not None
    }
    wire["max_output_tokens"] = max_tokens
    return {
        "model": model,
        "input": content,
        "stream": False,
        "store": False,
        **wire,
    }, max_tokens


def _require_served_instance(response: Mapping[str, Any], expected: str) -> None:
    if response.get("model_instance_id") != expected:
        raise LMStudioError("served model instance is absent or does not match the selection")


def chat_once(
    base_url: str,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    params: Mapping[str, Any],
    *,
    locality: LocalityEvidence,
    api_key_env: str = "LM_STUDIO_API_KEY",
    budget: RequestBudget | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Send exactly one no-retry request after local-execution and budget gates pass."""

    require_verified_local(locality)
    if not model.strip():
        raise LMStudioError("a concrete model or instance identifier is required")
    payload, max_tokens = _native_chat_payload(model, messages, params)
    if budget is not None:
        budget.begin(reserve_output_tokens=max_tokens)
    try:
        with LMStudioClient(
            base_url,
            api_key=os.environ.get(api_key_env),
            transport=transport,
        ) as client:
            response = client.post_json("/api/v1/chat", payload)
    except BaseException:
        if budget is not None:
            budget.cancel()
        raise
    try:
        _require_served_instance(response, model)
    except LMStudioError:
        if budget is not None:
            budget.cancel()
        raise
    if budget is not None:
        usage = parse_usage(response)
        budget.finish(
            generated_tokens=usage.completion_tokens,
            reserved_output_tokens=max_tokens,
        )
    return response
