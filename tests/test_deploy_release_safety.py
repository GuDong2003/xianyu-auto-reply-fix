from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path, PurePosixPath

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
DEPLOY_ROOT = PROJECT_ROOT / "deploy"
IMAGE_DIGEST = "sha256:" + "a" * 64
GHCR_IMAGE = f"ghcr.io/example/xianyu-connector@{IMAGE_DIGEST}"
PREVIOUS_IMAGE_DIGEST = "sha256:" + "b" * 64
PREVIOUS_GHCR_IMAGE = f"ghcr.io/example/xianyu-connector@{PREVIOUS_IMAGE_DIGEST}"
FAILED_IMAGE_DIGEST = "sha256:" + "c" * 64
FAILED_GHCR_IMAGE = f"ghcr.io/example/xianyu-connector@{FAILED_IMAGE_DIGEST}"


def _write_executable(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)
    return path


def _copy_deploy_script(
    source_name: str,
    tmp_path: Path,
    replacements: dict[str, str],
) -> Path:
    source = (DEPLOY_ROOT / source_name).read_text(encoding="utf-8")
    for original, replacement in replacements.items():
        assert original in source
        source = source.replace(original, replacement)
    return _write_executable(tmp_path / source_name, source)


def _write_fake_docker(bin_dir: Path) -> Path:
    docker = bin_dir / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
    case "$4" in
        *RepoDigests*) printf '%s\n' "${REPO_DIGEST_OUTPUT:-$EXPECTED_IMAGE}" ;;
        *Architecture*) printf '%s\n' "${IMAGE_PLATFORM:-linux/amd64}" ;;
    esac
    exit 0
fi
if [ "$1" = "inspect" ]; then
    printf '%s\n' 'false'
    exit 0
fi
if [ "${FAIL_COMPOSE_UP:-false}" = "true" ]; then
    case " $* " in
        *" compose "*" up "*) exit 42 ;;
    esac
fi
exit 0
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    return docker


def _forced_parser_with_fake_sudo(tmp_path: Path) -> tuple[Path, Path]:
    sudo_log = tmp_path / "sudo.log"
    fake_sudo = _write_executable(
        tmp_path / "fake-sudo",
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(sudo_log))}\n",
    )
    parser = _copy_deploy_script(
        "production-deploy-entrypoint.sh",
        tmp_path,
        {"/usr/bin/sudo": shlex.quote(str(fake_sudo))},
    )
    return parser, sudo_log


def _provenance_verifier_with_fake_gh(tmp_path: Path) -> tuple[Path, Path, Path]:
    gh_log = tmp_path / "gh.log"
    gh_environment_log = tmp_path / "gh-environment.log"
    fake_gh = _write_executable(
        tmp_path / "gh",
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(gh_log))}\n"
        "if [ \"$1\" = --version ]; then\n"
        "  printf '%s\\n' 'gh version 2.96.0 (test)'\n"
        "  exit 0\n"
        "fi\n"
        f"printf 'GH_TOKEN=%s\\n' \"${{GH_TOKEN-unset}}\" > "
        f"{shlex.quote(str(gh_environment_log))}\n"
        f"printf 'GITHUB_TOKEN=%s\\n' \"${{GITHUB_TOKEN-unset}}\" >> "
        f"{shlex.quote(str(gh_environment_log))}\n",
    )
    docker_config = tmp_path / "docker-config.json"
    docker_config.write_text("{}\n", encoding="utf-8")
    verifier = _copy_deploy_script(
        "verify-github-provenance.sh",
        tmp_path,
        {
            "gh=/usr/bin/gh": f"gh={shlex.quote(str(fake_gh))}",
            "docker_config=/root/.docker/config.json": (
                f"docker_config={shlex.quote(str(docker_config))}"
            ),
        },
    )
    return verifier, gh_log, gh_environment_log


def _write_root_orchestrator_program(
    path: Path,
    log_path: Path,
    *,
    exit_code: int,
) -> Path:
    return _write_executable(
        path,
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log_path))}\n"
        f"exit {exit_code}\n",
    )


def _write_sequenced_root_orchestrator_program(
    path: Path,
    log_path: Path,
    exit_codes: tuple[int, ...],
) -> Path:
    state_path = path.with_suffix(".count")
    cases = "".join(
        f"  {index}) exit {exit_code} ;;\n"
        for index, exit_code in enumerate(exit_codes, start=1)
    )
    return _write_executable(
        path,
        "#!/bin/sh\n"
        "set -eu\n"
        "count=0\n"
        f"if [ -r {shlex.quote(str(state_path))} ]; then\n"
        f"  IFS= read -r count < {shlex.quote(str(state_path))}\n"
        "fi\n"
        "count=$((count + 1))\n"
        f"printf '%s\\n' \"$count\" > {shlex.quote(str(state_path))}\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(log_path))}\n"
        "case \"$count\" in\n"
        f"{cases}"
        f"  *) exit {exit_codes[-1]} ;;\n"
        "esac\n",
    )


def _write_verify_docker(
    bin_dir: Path,
    *,
    docker_log: Path,
    docker_stdin_log: Path,
    release_id: str,
) -> Path:
    return _write_executable(
        bin_dir / "docker",
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(docker_log))}\n"
        "case \"$1\" in\n"
        "  compose)\n"
        "    case \"$*\" in\n"
        "      *xianyu-control) printf '%s\\n' control-container ;;\n"
        "      *) printf '%s\\n' connector-container ;;\n"
        "    esac\n"
        "    ;;\n"
        "  inspect)\n"
        "    case \"$3\" in\n"
        "      *State.Running*) printf '%s\\n' true ;;\n"
        "      *State.Health*) printf '%s\\n' healthy ;;\n"
        "      *RestartCount*) printf '%s\\n' 0 ;;\n"
        "      *RestartPolicy.Name*) printf '%s\\n' unless-stopped ;;\n"
        "      *Config.Image*) printf '%s\\n' \"$EXPECTED_IMAGE\" ;;\n"
        "      *Config.Env*)\n"
        f"        printf '%s\\n' XIANYU_RELEASE_ID={release_id} \\\n"
        "          XIANYU_ASSET_REVISION=\"${EXPECTED_IMAGE##*@sha256:}\" \\\n"
        "          XIANYU_REMOTE_VERIFICATION_ENABLED=false\n"
        "        ;;\n"
        "    esac\n"
        "    ;;\n"
        "  port)\n"
        "    if [ \"$2\" = control-container ]; then\n"
        "      printf '%s\\n' 127.0.0.1:9000\n"
        "    fi\n"
        "    ;;\n"
        f"  exec) cat >> {shlex.quote(str(docker_stdin_log))} ;;\n"
        "esac\n",
    )


def _release_verification_environment(
    tmp_path: Path,
    release_target: str,
) -> tuple[dict[str, str], Path, Path, Path]:
    release_id = "release-20260720"
    release_root = tmp_path / "releases"
    component_root = release_root / release_target
    release = component_root / release_id
    release.mkdir(parents=True)
    (component_root / "current").symlink_to(release)
    environment_source = (
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        f"XIANYU_IMAGE={GHCR_IMAGE}\n"
        "XIANYU_REMOTE_VERIFICATION_ENABLED=false\n"
        "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=\n"
        "XIANYU_CONTROL_PORT=9000\n"
    )
    env_file = tmp_path / "production.env"
    env_file.write_text(environment_source, encoding="utf-8")
    (release / "production.env").write_text(environment_source, encoding="utf-8")
    (release / "release.env").write_text(
        f"XIANYU_IMAGE={GHCR_IMAGE}\n"
        f"XIANYU_RELEASE_ID={release_id}\n"
        f"XIANYU_ASSET_REVISION={'a' * 64}\n",
        encoding="utf-8",
    )
    (release / "compose.production.yml").write_text("services: {}\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    docker_log = tmp_path / "docker.log"
    docker_stdin_log = tmp_path / "docker-stdin.log"
    host_python_log = tmp_path / "host-python.log"
    _write_verify_docker(
        bin_dir,
        docker_log=docker_log,
        docker_stdin_log=docker_stdin_log,
        release_id=release_id,
    )
    _write_executable(
        bin_dir / "readlink",
        "#!/bin/sh\n"
        f"printf '%s\\n' {shlex.quote(str(release.resolve()))}\n",
    )
    _write_executable(
        bin_dir / "python3",
        "#!/bin/sh\n"
        f"cat > {shlex.quote(str(host_python_log))}\n",
    )
    environment = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "ENV_FILE": str(env_file),
        "RELEASE_ROOT": str(release_root),
        "EXPECTED_IMAGE": GHCR_IMAGE,
    }
    return environment, docker_log, docker_stdin_log, host_python_log


def _snapshot(
    component_root: Path,
    release_id: str,
    *,
    image: str = GHCR_IMAGE,
    previous: Path | None = None,
) -> Path:
    release = component_root / release_id
    release.mkdir(parents=True)
    (release / "production.env").write_text(
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        f"XIANYU_IMAGE={image}\n"
        "XIANYU_CONTROL_PORT=9000\n"
        "XIANYU_REMOTE_VERIFICATION_ENABLED=false\n"
        "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=\n",
        encoding="utf-8",
    )
    (release / "release.env").write_text(
        f"XIANYU_IMAGE={image}\n"
        f"RELEASE_TARGET=connector\n"
        f"XIANYU_RELEASE_ID={release_id}\n"
        f"XIANYU_ASSET_REVISION={image.rsplit(':', 1)[1]}\n",
        encoding="utf-8",
    )
    (release / "compose.production.yml").write_text("services: {}\n", encoding="utf-8")
    if previous is not None:
        (release / "previous-current").write_text(f"{previous.resolve()}\n", encoding="utf-8")
    return release


def _run(
    script: Path,
    *args: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _script_environment(tmp_path: Path, *, image: str = GHCR_IMAGE) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_docker(bin_dir)
    docker_log = tmp_path / "docker.log"
    return {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "DOCKER_LOG": str(docker_log),
        "EXPECTED_IMAGE": image,
    }


def test_rollback_uses_previous_current_record_and_ignores_failed_newer_directory(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    previous = _snapshot(
        component_root,
        "20260720T010000Z",
        image=PREVIOUS_GHCR_IMAGE,
    )
    current = _snapshot(component_root, "20260720T020000Z", previous=previous)
    failed = _snapshot(
        component_root,
        "20260720T030000Z",
        image=FAILED_GHCR_IMAGE,
        previous=current,
    )
    (component_root / "current").symlink_to(current)
    environment = {
        **_script_environment(tmp_path),
        "RELEASE_ROOT": str(release_root),
        "RELEASE_TARGET": "connector",
    }

    result = _run(DEPLOY_ROOT / "rollback.sh", env=environment)

    assert result.returncode == 0, result.stderr
    recovery = (component_root / "current").resolve()
    assert recovery not in {previous.resolve(), current.resolve(), failed.resolve()}
    assert "-rollback-" in recovery.name
    assert f"XIANYU_IMAGE={PREVIOUS_GHCR_IMAGE}" in (recovery / "release.env").read_text(
        encoding="utf-8"
    )
    recovery_environment = (recovery / "production.env").read_text(encoding="utf-8")
    assert f"XIANYU_IMAGE={PREVIOUS_GHCR_IMAGE}" in recovery_environment
    assert "XIANYU_REMOTE_VERIFICATION_ENABLED=false" in recovery_environment
    docker_log = Path(environment["DOCKER_LOG"]).read_text(encoding="utf-8")
    assert str(recovery / "production.env") in docker_log
    assert str(failed) not in docker_log


def test_restore_current_redeploys_last_successful_snapshot_after_failed_release(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    previous = _snapshot(component_root, "20260720T010000Z")
    current = _snapshot(
        component_root,
        "20260720T020000Z",
        image=PREVIOUS_GHCR_IMAGE,
        previous=previous,
    )
    _snapshot(component_root, "20260720T030000Z", previous=current)
    (component_root / "current").symlink_to(current)
    environment = {
        **_script_environment(tmp_path),
        "RELEASE_ROOT": str(release_root),
        "RELEASE_TARGET": "connector",
        "RESTORE_CURRENT": "true",
    }

    result = _run(DEPLOY_ROOT / "rollback.sh", env=environment)

    assert result.returncode == 0, result.stderr
    recovery = (component_root / "current").resolve()
    assert recovery != current.resolve()
    assert f"XIANYU_IMAGE={PREVIOUS_GHCR_IMAGE}" in (recovery / "release.env").read_text(
        encoding="utf-8"
    )
    docker_log = Path(environment["DOCKER_LOG"]).read_text(encoding="utf-8")
    assert str(recovery / "production.env") in docker_log


def test_rollback_commits_retryable_desired_state_before_compose(tmp_path: Path) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    current = _snapshot(
        component_root,
        "20260720T020000Z",
        image=PREVIOUS_GHCR_IMAGE,
    )
    (component_root / "current").symlink_to(current)
    deployment_env = tmp_path / "production.env"
    deployment_env.write_text(
        (current / "production.env").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    deployment_env.chmod(0o600)
    environment = {
        **_script_environment(tmp_path),
        "ENV_FILE": str(deployment_env),
        "RELEASE_ROOT": str(release_root),
        "RELEASE_TARGET": "connector",
        "RESTORE_CURRENT": "true",
        "FAIL_COMPOSE_UP": "true",
    }

    failed = _run(DEPLOY_ROOT / "rollback.sh", env=environment)

    assert failed.returncode == 42
    committed_recovery = (component_root / "current").resolve()
    assert committed_recovery != current.resolve()
    assert "committed sanitized rollback desired state" in failed.stderr
    committed_environment = deployment_env.read_text(encoding="utf-8")
    assert f"XIANYU_IMAGE={PREVIOUS_GHCR_IMAGE}" in committed_environment
    assert "XIANYU_REMOTE_VERIFICATION_ENABLED=false" in committed_environment

    retry = _run(
        DEPLOY_ROOT / "rollback.sh",
        env={**environment, "FAIL_COMPOSE_UP": "false"},
    )

    assert retry.returncode == 0, retry.stderr
    assert (component_root / "current").resolve() != committed_recovery


def test_ghcr_promotion_validates_digest_updates_env_and_records_previous_current(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    previous = _snapshot(component_root, "20260720T010000Z")
    (component_root / "current").symlink_to(previous)
    compose = tmp_path / "compose.production.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / "production.env"
    env_file.write_text(
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        "XIANYU_IMAGE=127.0.0.1:5000/xianyu-connector@sha256:old\n"
        "XIANYU_REMOTE_VERIFICATION_ENABLED=true\n"
        "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=https://control.example.test\n"
        f"COMPOSE_FILE={compose}\n"
        f"RELEASE_ROOT={release_root}\n"
        "XIANYU_LEGACY_CONTAINER=legacy\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    environment = {
        **_script_environment(tmp_path),
        "ENV_FILE": str(env_file),
        "RELEASE_ROOT": str(release_root),
    }

    result = _run(
        DEPLOY_ROOT / "promote-ghcr-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    updated_env = env_file.read_text(encoding="utf-8")
    assert f"XIANYU_IMAGE={GHCR_IMAGE}" in updated_env
    assert "XIANYU_REMOTE_VERIFICATION_ENABLED=false" in updated_env
    assert "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=\n" in updated_env
    assert env_file.stat().st_mode & 0o777 == 0o600
    docker_log = Path(environment["DOCKER_LOG"]).read_text(encoding="utf-8")
    assert f"pull --platform linux/amd64 {GHCR_IMAGE}" in docker_log
    assert "image inspect --format {{range .RepoDigests}}{{println .}}{{end}}" in docker_log
    assert "image inspect --format {{.Os}}/{{.Architecture}}" in docker_log
    current = (component_root / "current").resolve()
    assert current != previous.resolve()
    assert (current / "previous-current").read_text(encoding="utf-8").strip() == str(
        previous.resolve()
    )


def test_failed_ghcr_promotion_commits_recovered_previous_desired_state(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    current = _snapshot(
        component_root,
        "20260720T010000Z",
        image=PREVIOUS_GHCR_IMAGE,
    )
    (component_root / "current").symlink_to(current)
    compose = tmp_path / "compose.production.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / "production.env"
    original = (
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        "XIANYU_IMAGE=127.0.0.1:5000/xianyu-connector@sha256:old\n"
        "XIANYU_REMOTE_VERIFICATION_ENABLED=false\n"
        "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=\n"
        f"COMPOSE_FILE={compose}\n"
        f"RELEASE_ROOT={release_root}\n"
        "XIANYU_LEGACY_CONTAINER=legacy\n"
    )
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o600)
    environment = {
        **_script_environment(tmp_path),
        "ENV_FILE": str(env_file),
        "RELEASE_ROOT": str(release_root),
        "FAIL_COMPOSE_UP": "true",
    }

    result = _run(
        DEPLOY_ROOT / "promote-ghcr-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 42
    recovered_environment = env_file.read_text(encoding="utf-8")
    assert f"XIANYU_IMAGE={PREVIOUS_GHCR_IMAGE}" in recovered_environment
    assert "XIANYU_REMOTE_VERIFICATION_ENABLED=false" in recovered_environment
    assert "automatic recovery failed with status 42" in result.stderr
    assert "retry with:" in result.stderr
    assert (component_root / "current").resolve() != current.resolve()


def test_ghcr_promotion_rejects_non_ghcr_or_non_digest_image_before_docker(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "production.env"
    env_file.write_text("XIANYU_IMAGE=unchanged\n", encoding="utf-8")
    environment = {
        **_script_environment(tmp_path),
        "ENV_FILE": str(env_file),
    }

    result = _run(
        DEPLOY_ROOT / "promote-ghcr-release.sh",
        "127.0.0.1:5000/xianyu-connector@sha256:" + "a" * 64,
        "connector",
        env=environment,
    )

    assert result.returncode == 2
    assert env_file.read_text(encoding="utf-8") == "XIANYU_IMAGE=unchanged\n"
    assert not Path(environment["DOCKER_LOG"]).exists()


@pytest.mark.parametrize(
    ("environment_override", "expected_error"),
    [
        ({"REPO_DIGEST_OUTPUT": "ghcr.io/example/other@sha256:" + "b" * 64}, "RepoDigests"),
        ({"IMAGE_PLATFORM": "linux/arm64"}, "linux/amd64"),
    ],
)
def test_ghcr_promotion_rejects_digest_or_platform_mismatch_before_env_update(
    tmp_path: Path,
    environment_override: dict[str, str],
    expected_error: str,
) -> None:
    env_file = tmp_path / "production.env"
    original = (
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        "XIANYU_IMAGE=unchanged\n"
        "XIANYU_REMOTE_VERIFICATION_ENABLED=true\n"
    )
    env_file.write_text(original, encoding="utf-8")
    environment = {
        **_script_environment(tmp_path),
        **environment_override,
        "ENV_FILE": str(env_file),
    }

    result = _run(
        DEPLOY_ROOT / "promote-ghcr-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr
    assert env_file.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "original_command",
    [
        "",
        "deploy",
        f"release {GHCR_IMAGE} control",
        f"deploy {GHCR_IMAGE} control extra",
        "deploy ghcr.io/example/xianyu-connector:latest control",
        f"deploy {GHCR_IMAGE};id control",
        f"deploy {GHCR_IMAGE} control;id",
    ],
    ids=[
        "empty",
        "missing-arguments",
        "wrong-command",
        "extra-argument",
        "mutable-tag",
        "digest-injection",
        "target-injection",
    ],
)
def test_forced_deploy_parser_rejects_untrusted_commands_before_sudo(
    tmp_path: Path,
    original_command: str,
) -> None:
    parser, sudo_log = _forced_parser_with_fake_sudo(tmp_path)

    result = _run(
        parser,
        env={**os.environ, "SSH_ORIGINAL_COMMAND": original_command},
    )

    assert result.returncode == 2
    assert not sudo_log.exists()


def test_forced_deploy_parser_invokes_only_fixed_root_orchestrator(tmp_path: Path) -> None:
    parser, sudo_log = _forced_parser_with_fake_sudo(tmp_path)

    result = _run(
        parser,
        env={
            **os.environ,
            "SSH_ORIGINAL_COMMAND": f"deploy {GHCR_IMAGE} connector",
            "ENV_FILE": "/tmp/untrusted.env",
            "RELEASE_ROOT": "/tmp/untrusted-releases",
        },
    )

    assert result.returncode == 0, result.stderr
    assert sudo_log.read_text(encoding="utf-8").splitlines() == [
        "-n",
        "/opt/xianyu-production/staging/production-deploy-root.sh",
        GHCR_IMAGE,
        "connector",
    ]


def test_provenance_verifier_uses_oci_bundle_and_fixed_github_identity(
    tmp_path: Path,
) -> None:
    verifier, gh_log, gh_environment_log = _provenance_verifier_with_fake_gh(tmp_path)

    result = _run(
        verifier,
        GHCR_IMAGE,
        "ghcr.io/example/xianyu-connector",
        env={
            **os.environ,
            "GH_TOKEN": "must-not-be-forwarded",
            "GITHUB_TOKEN": "must-not-be-forwarded",
        },
    )

    assert result.returncode == 0, result.stderr
    assert gh_log.read_text(encoding="utf-8").splitlines() == [
        "--version",
        (
            f"attestation verify oci://{GHCR_IMAGE} "
            "--repo example/xianyu-connector "
            "--signer-workflow "
            "example/xianyu-connector/.github/workflows/docker-image.yml "
            "--source-ref refs/heads/main --deny-self-hosted-runners --bundle-from-oci"
        ),
    ]
    assert gh_environment_log.read_text(encoding="utf-8").splitlines() == [
        "GH_TOKEN=unset",
        "GITHUB_TOKEN=unset",
    ]


@pytest.mark.parametrize("retry_verification_status", [0, 23], ids=["retry-ok", "retry-fails"])
def test_root_orchestrator_reconciles_committed_recovery_after_promotion_failure(
    tmp_path: Path,
    retry_verification_status: int,
) -> None:
    staging_root = tmp_path / "staging"
    release_root = tmp_path / "releases"
    env_file = tmp_path / "production.env"
    lock_file = tmp_path / "deployment.lock"
    promote_log = tmp_path / "promote.log"
    provenance_log = tmp_path / "provenance.log"
    verify_log = tmp_path / "verify.log"
    rollback_log = tmp_path / "rollback.log"
    _write_root_orchestrator_program(
        staging_root / "promote-ghcr-release.sh",
        promote_log,
        exit_code=42,
    )
    _write_root_orchestrator_program(
        staging_root / "verify-github-provenance.sh",
        provenance_log,
        exit_code=0,
    )
    _write_sequenced_root_orchestrator_program(
        staging_root / "verify-production-release.sh",
        verify_log,
        (17, retry_verification_status),
    )
    _write_executable(
        staging_root / "rollback.sh",
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf 'RESTORE_CURRENT=%s %s\\n' \"${{RESTORE_CURRENT-unset}}\" \"$*\" "
        f">> {shlex.quote(str(rollback_log))}\n",
    )
    fake_flock = _write_executable(tmp_path / "flock", "#!/bin/sh\nexit 0\n")
    recovery = _snapshot(
        release_root / "control",
        "20260720T010000Z-rollback-1",
        image=PREVIOUS_GHCR_IMAGE,
    )
    (recovery.parent / "current").symlink_to(recovery)
    env_file.write_text(
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n",
        encoding="utf-8",
    )
    orchestrator = _copy_deploy_script(
        "production-deploy-root.sh",
        tmp_path,
        {
            "staging_root=/opt/xianyu-production/staging": (
                f"staging_root={shlex.quote(str(staging_root))}"
            ),
            "env_file=/opt/xianyu-production/production.env": (
                f"env_file={shlex.quote(str(env_file))}"
            ),
            "release_root=/opt/xianyu/releases": (
                f"release_root={shlex.quote(str(release_root))}"
            ),
            "exec 9>/run/lock/xianyu-production-deploy.lock": (
                f"exec 9>{shlex.quote(str(lock_file))}"
            ),
            "/usr/bin/flock": shlex.quote(str(fake_flock)),
        },
    )

    result = _run(orchestrator, GHCR_IMAGE, "control", env=os.environ.copy())

    assert result.returncode == 42
    assert provenance_log.read_text(encoding="utf-8").splitlines() == [
        f"{GHCR_IMAGE} ghcr.io/example/xianyu-connector"
    ]
    assert promote_log.read_text(encoding="utf-8").splitlines() == [f"{GHCR_IMAGE} control"]
    assert verify_log.read_text(encoding="utf-8").splitlines() == [
        f"{PREVIOUS_GHCR_IMAGE} control",
        f"{PREVIOUS_GHCR_IMAGE} control"
    ]
    assert rollback_log.read_text(encoding="utf-8").splitlines() == [
        "RESTORE_CURRENT=true control"
    ]


def test_release_verification_uses_liveness_not_account_readiness(tmp_path: Path) -> None:
    environment, _, docker_stdin_log, _ = _release_verification_environment(
        tmp_path,
        "connector",
    )

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    health_probe = docker_stdin_log.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8091/health/live" in health_probe
    assert "/health/ready" not in health_probe


@pytest.mark.parametrize("release_target", ["control", "connector", "all"])
def test_release_verification_probes_connector_internal_health_from_control(
    tmp_path: Path,
    release_target: str,
) -> None:
    environment, docker_log, docker_stdin_log, host_python_log = (
        _release_verification_environment(tmp_path, release_target)
    )

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        release_target,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    docker_commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert "exec -i control-container python -" in docker_commands
    connector_probe = docker_stdin_log.read_text(encoding="utf-8")
    assert "/run/secrets/connector_internal_token" in connector_probe
    assert "http://xianyu-connector:8091/internal/health" in connector_probe
    assert "X-Connector-Token" in connector_probe
    assert "/health/ready" not in connector_probe
    if release_target in {"control", "all"}:
        host_probe = host_python_log.read_text(encoding="utf-8")
        assert "http://127.0.0.1:{port}/health/live" in host_probe
    else:
        assert not host_python_log.exists()


def test_readme_installs_forced_parser_outside_root_only_staging() -> None:
    readme = (DEPLOY_ROOT / "README.md").read_text(encoding="utf-8")
    forced_command = re.search(r'restrict,command="([^"]+)"', readme)

    assert forced_command is not None
    parser_path = PurePosixPath(forced_command.group(1))
    staging_root = PurePosixPath("/opt/xianyu-production/staging")
    assert parser_path == PurePosixPath("/usr/local/bin/xianyu-production-deploy")
    assert not parser_path.is_relative_to(staging_root)
    assert "install -d -o root -g root -m 0750 /opt/xianyu-production/staging" in readme
    assert (
        "install -o root -g root -m 0755 deploy/production-deploy-entrypoint.sh \\\n"
        "  /usr/local/bin/xianyu-production-deploy"
    ) in readme


def test_production_example_and_docs_make_control_default_but_automation_target_explicit() -> None:
    production_env = (DEPLOY_ROOT / "production.env.example").read_text(encoding="utf-8")
    readme = (DEPLOY_ROOT / "README.md").read_text(encoding="utf-8")
    promotion = (DEPLOY_ROOT / "promote-ghcr-release.sh").read_text(encoding="utf-8")

    assert "RELEASE_TARGET=control" in production_env
    assert "RELEASE_TARGET=all" not in production_env
    assert "default operational target" in readme
    assert "/opt/xianyu-production/staging/production-deploy-root.sh" in readme
    assert "Do not\ninvoke `promote-ghcr-release.sh`" in readme
    assert 'if [ "$#" -ne 2 ]' in promotion
    assert 'RELEASE_TARGET="$release_target"' in promotion
    assert "non-backward-compatible" in readme
    assert "restore" in readme.lower()
