from __future__ import annotations

from pathlib import Path


def test_public_workflows_use_hosted_runners_without_private_inference_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    workflows = sorted((root / ".github" / "workflows").glob("*.yml"))

    assert {path.name for path in workflows} == {"ci.yml", "docs.yml", "release.yml"}
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        assert "runs-on: ubuntu-24.04" in text
        assert "self-hosted" not in text
        assert "LM_STUDIO_API_KEY" not in text
        assert "local-evals run" not in text
        assert "local-evals optimize" not in text
        assert "HTTP_PROXY" not in text
        assert "HTTPS_PROXY" not in text
        assert "ALL_PROXY" not in text


def test_model_capable_workflows_are_explicitly_mock_only() -> None:
    root = Path(__file__).resolve().parents[2]
    for name in ("ci.yml", "release.yml"):
        text = (root / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert 'LOCAL_EVALS_TEST_MODE: "mock-only"' in text

    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    docs = (root / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")
    release = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    for text in (ci, docs, release):
        assert "gitleaks_8.30.1_linux_x64.tar.gz" in text
        assert "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in text
    assert "local-evals privacy audit --scope publication" in ci
    assert 'git checkout --detach "$PR_HEAD_SHA"' in ci
    assert "local-evals privacy audit --scope publication" in release
    assert "pull-requests: read" in release
    assert "checks: read" in release
    release_permissions = release.split("concurrency:", maxsplit=1)[0]
    assert "contents: read" in release_permissions
    assert "contents: write" not in release_permissions
    assert (
        "GITHUB_PROVENANCE_EVIDENCE: ${{ runner.temp }}/splash-evals-github-provenance.json"
    ) in release
    assert "python scripts/collect_github_provenance.py" in release
    assert '--output "$GITHUB_PROVENANCE_EVIDENCE"' in release
    assert '--repository "$GITHUB_REPOSITORY"' in release
    assert '--ref "$GITHUB_REF"' in release
    assert '--head-sha "${{ steps.audited-source.outputs.sha }}"' in release
    assert "audited-sha: ${{ steps.audited-source.outputs.sha }}" in release
    assert '--target "${{ needs.build.outputs.audited-sha }}"' in release
    assert release.index("python scripts/collect_github_provenance.py") < release.index(
        "local-evals privacy audit --scope publication"
    )
    assert "pnpm --dir docs-site install --frozen-lockfile" in docs
    assert "pnpm --dir docs-site validate" in docs
    assert "python3 scripts/docs_artifact.py prepare docs-site/dist" in docs
    assert "python3 scripts/docs_artifact.py verify docs-site/dist" in docs
    assert 'gitleaks" dir docs-site/dist --redact --no-banner --no-color' in docs
    assert "path: docs-site/dist" in docs
    assert "gitleaks dir dist --redact --no-banner --no-color" in release
    assert "working-directory: dist" in release
    assert "sha256sum *.whl *.tar.gz sbom.cdx.json > SHA256SUMS" in release
    assert "sha256sum dist/*.whl" not in release
    assert "working-directory: release-artifacts" in release
    assert "sha256sum --check SHA256SUMS" in release
