import shlex
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import reply_server
from xianyu_control.accounts_router import _normalize_public_origin

PROJECT_ROOT = Path(__file__).parents[1]

REQUIRED_RUFF_TESTS = {
    "tests/test_account_operation_coordinator.py",
    "tests/test_account_supervisor.py",
    "tests/test_alembic_revision_persistence.py",
    "tests/test_bounded_process.py",
    "tests/test_browser_auth_worker.py",
    "tests/test_connector_api.py",
    "tests/test_connector_domain.py",
    "tests/test_connector_ops.py",
    "tests/test_connector_repositories.py",
    "tests/test_connector_security.py",
    "tests/test_connector_verification.py",
    "tests/test_control_accounts_api.py",
    "tests/test_control_browser_runtime.py",
    "tests/test_control_connector_client.py",
    "tests/test_control_remote_verification.py",
    "tests/test_control_verification.py",
    "tests/test_deploy_release_safety.py",
    "tests/test_egress_guard.py",
    "tests/test_file_log_collector.py",
    "tests/test_legacy_adapter_policy.py",
    "tests/test_local_verification_handoff.py",
    "tests/test_production_image_contract.py",
    "tests/test_qr_auth_manager.py",
    "tests/test_qr_bootstrap.py",
    "tests/test_qr_relogin_ui.py",
    "tests/test_remote_verification_pointer_e2e.py",
    "tests/test_runtime_compat.py",
    "tests/test_runtime_reporter.py",
    "tests/test_slider_verification_guards.py",
    "tests/test_verification_backend.py",
    "tests/test_verification_browser.py",
    "tests/test_verification_rfb.py",
    "tests/test_verification_runtime.py",
}

LEGACY_RUFF_EXCLUSIONS = {
    "tests/test_verification_screenshot_freshness.py",
    "tests/test_xianyu_token_refresh_request.py",
}


def _check_ignore(paths: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=PROJECT_ROOT,
        input="\n".join(paths) + "\n",
        check=False,
        capture_output=True,
        text=True,
    )


def test_production_image_leaves_chromium_path_to_playwright_platform_resolution() -> None:
    dockerfile = PROJECT_ROOT / "deploy" / "Dockerfile.production"
    source = dockerfile.read_text(encoding="utf-8")

    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in source
    assert "PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH" not in source
    assert "chrome-linux64/chrome" not in source
    assert "chrome-linux/chrome" not in source


def test_release_manifest_sources_are_not_silently_ignored() -> None:
    required_paths = [
        "pyproject.toml",
        "xianyu_connector/application/runtime_reporter.py",
        "xianyu_connector/application/runtime_service.py",
        "xianyu_control/runtime_compat.py",
        "deploy/systemd/xianyu-backup.service",
        "deploy/systemd/xianyu-backup.timer",
        "deploy/systemd/xianyu-restore-check.service",
        "deploy/systemd/xianyu-restore-check.timer",
        "deploy/systemd/xianyu-egress-policy.service",
    ]
    pako_root = PROJECT_ROOT / "static/vendor/novnc/vendor/pako/lib"
    required_paths.extend(
        str(path.relative_to(PROJECT_ROOT))
        for path in sorted(pako_root.rglob("*"))
        if path.is_file()
    )

    result = _check_ignore(required_paths)

    assert result.returncode == 1, result.stdout
    assert result.stdout == ""


def test_python_cache_rules_remain_ignored() -> None:
    ignored_paths = [
        "xianyu_connector/application/__pycache__/probe.py",
        "xianyu_connector/application/probe.pyc",
        "xianyu_connector/application/runtime_generated.py",
        "deploy/systemd/unlisted.service",
        "static/vendor/other/lib/unlisted.js",
        "nested/example/pyproject.toml",
    ]

    result = _check_ignore(ignored_paths)

    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(ignored_paths)


def test_operator_html_is_private_no_store_and_uses_one_release_asset_namespace() -> None:
    client = TestClient(reply_server.app)
    revision = reply_server.ASSET_REVISION
    expected_prefix = f"/static/releases/{revision}/"

    for path in ("/", "/login.html", "/admin"):
        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-xianyu-release-id"] == reply_server.RELEASE_ID
        assert response.headers["x-xianyu-asset-revision"] == revision
        assert f'name="x-xianyu-release-id" content="{reply_server.RELEASE_ID}"' in response.text

    admin = client.get("/admin").text
    assert f'{expected_prefix}css/app.css' in admin
    assert f'{expected_prefix}js/remote-verification-console.js' in admin
    assert f'{expected_prefix}js/app.js' in admin
    assert "/static/css/app.css?v=" not in admin
    assert "/static/js/remote-verification-console.js?v=" not in admin
    assert "/static/js/app.js?v=" not in admin


def test_release_signal_and_stale_token_401_are_never_cached() -> None:
    client = TestClient(reply_server.app)
    release = client.get("/api/release")

    assert release.status_code == 200
    assert release.json() == {
        "release_id": reply_server.RELEASE_ID,
        "asset_revision": reply_server.ASSET_REVISION,
    }
    assert release.headers["cache-control"] == "no-store, private, max-age=0"

    stale = client.get("/cookies", headers={"Authorization": "Bearer pre-release-token"})
    assert stale.status_code == 401
    assert stale.headers["cache-control"] == "no-store, private, max-age=0"
    assert stale.headers["x-xianyu-release-id"] == reply_server.RELEASE_ID
    assert stale.headers["x-xianyu-asset-revision"] == reply_server.ASSET_REVISION


def test_verify_connection_mode_response_is_private_no_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XIANYU_EXTERNAL_CONNECTOR", "true")
    client = TestClient(reply_server.app)
    token = "verify-cache-contract-token"
    reply_server.SESSION_TOKENS[token] = {
        "user_id": 7,
        "username": "seller",
        "is_admin": False,
        "timestamp": time.time(),
    }
    try:
        authenticated = client.get(
            "/verify",
            headers={"Authorization": f"Bearer {token}"},
        )
        anonymous = client.get("/verify")
    finally:
        reply_server.SESSION_TOKENS.pop(token, None)

    assert authenticated.status_code == anonymous.status_code == 200
    assert authenticated.json()["connection_mode"] == "external_connector"
    assert anonymous.json() == {
        "authenticated": False,
        "connection_mode": "external_connector",
    }
    for response in (authenticated, anonymous):
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-xianyu-release-id"] == reply_server.RELEASE_ID


def test_register_page_all_branches_use_release_html_and_no_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reply_server, "static_dir", str(tmp_path))
    client = TestClient(reply_server.app)

    monkeypatch.setattr(
        reply_server.db_manager,
        "get_system_setting",
        lambda _name: "false",
    )
    disabled = client.get("/register.html")

    monkeypatch.setattr(
        reply_server.db_manager,
        "get_system_setting",
        lambda _name: "true",
    )
    missing = client.get("/register.html")
    (tmp_path / "register.html").write_text(
        '<html><head><script src="/static/js/app.js?v=old"></script></head>'
        '<body>register</body></html>',
        encoding="utf-8",
    )
    available = client.get("/register.html")

    assert disabled.status_code == 403
    assert missing.status_code == available.status_code == 200
    for response in (disabled, missing, available):
        assert response.headers["cache-control"] == "no-store, private, max-age=0"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["x-xianyu-release-id"] == reply_server.RELEASE_ID
        assert f'name="x-xianyu-release-id" content="{reply_server.RELEASE_ID}"' in response.text
    assert f"{reply_server.RELEASE_ASSET_PREFIX}/js/app.js" in available.text
    assert "/static/js/app.js?v=old" not in available.text


def test_remote_viewer_and_all_novnc_imports_stay_inside_release_namespace() -> None:
    client = TestClient(reply_server.app)
    prefix = reply_server.RELEASE_ASSET_PREFIX

    viewer_script = client.get(f"{prefix}/js/remote-verification-viewer.js")
    nested_module = client.get(f"{prefix}/vendor/novnc/core/input/keyboard.js")

    assert viewer_script.status_code == 200
    assert f"from '{prefix}/vendor/novnc/core/rfb.js'" in viewer_script.text
    assert "/static/vendor/novnc/core/rfb.js?v=" not in viewer_script.text
    assert viewer_script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert nested_module.status_code == 200
    assert nested_module.headers["cache-control"] == "public, max-age=31536000, immutable"

    remote_html = reply_server._render_release_html(
        '<html><head><link href="/static/css/remote-verification-viewer.css?v=old"></head>'
        '<body><script src="/static/js/remote-verification-viewer.js?v=old"></script></body></html>'
    )
    assert f'{prefix}/css/remote-verification-viewer.css' in remote_html
    assert f'{prefix}/js/remote-verification-viewer.js' in remote_html
    assert "?v=old" not in remote_html


@pytest.mark.parametrize(
    "public_origin",
    [
        "http://control.example.test",
        "https://control.example.test:8443",
        "https://control.example.test:invalid",
    ],
)
def test_remote_verification_rejects_nonstandard_public_origin(
    public_origin: str,
) -> None:
    with pytest.raises(RuntimeError, match="HTTPS public origin on port 443"):
        reply_server.validate_remote_verification_configuration(
            enabled=True,
            public_origin=public_origin,
        )
    assert _normalize_public_origin(public_origin) is None


@pytest.mark.parametrize(
    "public_origin",
    ["https://control.example.test", "https://control.example.test:443"],
)
def test_remote_verification_accepts_only_standard_https_origin(
    public_origin: str,
) -> None:
    reply_server.validate_remote_verification_configuration(
        enabled=True,
        public_origin=public_origin,
    )
    assert _normalize_public_origin(public_origin) is not None
    reply_server.validate_remote_verification_configuration(enabled=False, public_origin="")


def test_release_scripts_propagate_and_verify_runtime_release_identity() -> None:
    compose = (PROJECT_ROOT / "deploy" / "compose.production.yml").read_text(encoding="utf-8")
    release = (PROJECT_ROOT / "deploy" / "release.sh").read_text(encoding="utf-8")
    rollback = (PROJECT_ROOT / "deploy" / "rollback.sh").read_text(encoding="utf-8")
    entrypoint = (PROJECT_ROOT / "deploy" / "entrypoint.production.sh").read_text(encoding="utf-8")
    cutover = (PROJECT_ROOT / "deploy" / "initial-cutover.sh").read_text(encoding="utf-8")

    assert "XIANYU_RELEASE_ID" in compose
    assert "XIANYU_ASSET_REVISION" in compose
    assert "XIANYU_RELEASE_ID" in release
    assert "XIANYU_ASSET_REVISION" in release
    assert "${XIANYU_IMAGE##*@sha256:}" in release
    assert "/api/release" in release
    assert "XIANYU_RELEASE_ID" in rollback
    assert "XIANYU_ASSET_REVISION" in rollback
    assert "XIANYU_RELEASE_ID=initial-cutover-preflight" in cutover
    assert "XIANYU_ASSET_REVISION=initial-cutover-preflight" in cutover
    assert "remote verification requires an HTTPS public origin" in entrypoint


def test_production_image_exposes_real_amd64_remote_pointer_e2e_command() -> None:
    runner = PROJECT_ROOT / "deploy" / "run-remote-verification-pointer-e2e.sh"
    harness = PROJECT_ROOT / "deploy" / "remote_verification_pointer_e2e.py"
    dockerignore_lines = {
        line.strip()
        for line in (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    assert runner.is_file()
    assert harness.is_file()
    assert "deploy/*" in dockerignore_lines
    assert {
        line for line in dockerignore_lines if line.startswith("!deploy/")
    } == {
        "!deploy/entrypoint.production.sh",
        "!deploy/novnc.SHA256SUMS",
        "!deploy/run-remote-verification-pointer-e2e.sh",
        "!deploy/remote_verification_pointer_e2e.py",
    }
    assert "!deploy/" not in dockerignore_lines
    runner_source = runner.read_text(encoding="utf-8")
    harness_source = harness.read_text(encoding="utf-8")
    assert "x86_64" in runner_source
    assert "pointerdown" in harness_source
    assert "pointermove" in harness_source
    assert "pointerup" in harness_source
    assert "x11vnc" in harness_source
    assert "create_connector_app" in harness_source
    assert "ConnectorClient" in harness_source
    assert "create_accounts_router" in harness_source
    assert "remote-verification-viewer.js" in harness_source
    assert "remote-proof" in harness_source
    assert "_RfbWebSocketBridge" not in harness_source


def test_connector_image_gate_runs_real_remote_pointer_e2e_on_amd64() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/connector-quality.yml").read_text(
        encoding="utf-8"
    )
    compose = (PROJECT_ROOT / "deploy/compose.production.yml").read_text(
        encoding="utf-8"
    )
    readme = (PROJECT_ROOT / "deploy/README.md").read_text(encoding="utf-8")
    production_tmpfs = "/tmp:rw,noexec,nosuid,size=128m"

    assert "platforms: linux/amd64" in workflow
    assert "docker run --rm --platform linux/amd64 --read-only" in workflow
    assert production_tmpfs in compose
    assert f"--tmpfs {production_tmpfs}" in workflow
    assert f"--tmpfs {production_tmpfs}" in readme
    assert "--entrypoint /app/deploy/run-remote-verification-pointer-e2e.sh" in workflow
    assert "Smoke test production Chromium" not in workflow


def test_connector_quality_is_reusable_and_orders_image_after_source_quality() -> None:
    workflow_path = PROJECT_ROOT / ".github/workflows/connector-quality.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    assert workflow["on"]["workflow_call"]["inputs"]["run_image_gate"] == {
        "description": "Run the production image pointer gate",
        "required": "false",
        "type": "boolean",
        "default": "true",
    }
    assert workflow["jobs"]["image"]["needs"] == "quality"
    assert "inputs.run_image_gate" in workflow["jobs"]["image"]["if"]
    assert "--strict" in workflow["jobs"]["quality"]["steps"][4]["run"]
    source = workflow_path.read_text(encoding="utf-8")
    assert "--cov-fail-under=80" in source
    assert (
        "uv run pytest -q tests --cov=xianyu_connector/application "
        "--cov=xianyu_connector/domain --cov-config=/dev/null "
        "--cov-branch --cov-fail-under=90"
    ) in source
    assert "--cov=xianyu_connector.application" not in source
    assert "--cov=xianyu_connector.domain" not in source


def test_connector_ruff_gate_covers_release_tests_without_legacy_failures() -> None:
    workflow_path = PROJECT_ROOT / ".github/workflows/connector-quality.yml"
    workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    quality_steps = workflow["jobs"]["quality"]["steps"]
    ruff_run = next(
        step["run"]
        for step in quality_steps
        if step.get("run", "").startswith("uv run ruff check ")
    )
    ruff_targets = set(shlex.split(ruff_run)[4:])

    assert ruff_targets >= REQUIRED_RUFF_TESTS
    assert LEGACY_RUFF_EXCLUSIONS.isdisjoint(ruff_targets)
    assert "git diff" not in ruff_run
    assert "github.event.pull_request" not in ruff_run


def test_official_image_publish_chain_is_ghcr_only_and_digest_first() -> None:
    workflow_path = PROJECT_ROOT / ".github/workflows/docker-image.yml"
    source = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)

    assert set(workflow["on"]) == {"release", "workflow_dispatch"}
    assert workflow["on"]["release"]["types"] == ["published"]
    dispatch_inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch_inputs) == {"deploy", "target"}
    assert dispatch_inputs["deploy"]["default"] == "false"
    assert dispatch_inputs["target"]["options"] == ["control", "connector", "all"]

    quality = workflow["jobs"]["quality"]
    assert quality["uses"] == "./.github/workflows/connector-quality.yml"
    assert quality["with"]["run_image_gate"] == "false"

    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "quality"
    assert publish["permissions"] == {"contents": "read", "packages": "write"}
    assert publish["outputs"] == {
        "image": "${{ steps.publish.outputs.image }}",
        "digest": "${{ steps.publish.outputs.digest }}",
    }
    build_steps = [
        step
        for step in publish["steps"]
        if step.get("uses", "").startswith("docker/build-push-action@")
    ]
    assert len(build_steps) == 1
    assert build_steps[0]["with"]["platforms"] == "linux/amd64"
    assert build_steps[0]["with"]["load"] == "true"
    assert build_steps[0]["with"]["push"] == "false"

    e2e_index = source.index("--tmpfs /tmp:rw,noexec,nosuid,size=128m")
    supply_chain_index = source.index("sha256sum -c deploy/novnc-source.SHA256SUMS")
    build_index = source.index("docker/build-push-action@v6")
    login_index = source.index("docker/login-action@")
    push_index = source.index('docker push "$tag"')
    assert supply_chain_index < build_index
    assert e2e_index < login_index < push_index
    assert "docker.io/" not in source
    assert "DOCKER_USERNAME" not in source
    assert "DOCKER_PASSWORD" not in source
    assert "ghcr.io/" in source
    assert "docker buildx imagetools inspect" in source
    assert "actions/upload-artifact@v4" in source
    assert "type=sha,prefix=sha-,format=long" in source
    assert "flavor: latest=false" in source
    assert "github.event.release.prerelease == false" in source


def test_protected_deploy_handoff_only_receives_digest_and_explicit_target() -> None:
    workflow_path = PROJECT_ROOT / ".github/workflows/docker-image.yml"
    source = workflow_path.read_text(encoding="utf-8")
    workflow = yaml.load(source, Loader=yaml.BaseLoader)
    deploy = workflow["jobs"]["deploy"]

    assert deploy["needs"] == "publish"
    assert deploy["environment"] == "production"
    assert deploy["permissions"] == {}
    assert "github.event_name == 'workflow_dispatch'" in deploy["if"]
    assert "inputs.deploy" in deploy["if"]
    step = deploy["steps"][0]
    assert step["env"] == {
        "XIANYU_IMAGE": "${{ needs.publish.outputs.image }}",
        "RELEASE_TARGET": "${{ inputs.target }}",
    }
    assert "@sha256:" in step["run"]
    assert "docker push" not in step["run"]
    assert "deployment wrapper is not configured" in step["run"]
