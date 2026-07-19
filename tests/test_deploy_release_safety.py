from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parents[1]
DEPLOY_ROOT = PROJECT_ROOT / "deploy"
IMAGE_DIGEST = "sha256:" + "a" * 64
GHCR_IMAGE = f"ghcr.io/example/xianyu-connector@{IMAGE_DIGEST}"


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


def _snapshot(component_root: Path, release_id: str, *, previous: Path | None = None) -> Path:
    release = component_root / release_id
    release.mkdir(parents=True)
    (release / "production.env").write_text(
        f"XIANYU_IMAGE={GHCR_IMAGE}\nXIANYU_CONTROL_PORT=9000\n",
        encoding="utf-8",
    )
    (release / "release.env").write_text(
        f"XIANYU_IMAGE={GHCR_IMAGE}\n"
        f"RELEASE_TARGET=connector\n"
        f"XIANYU_RELEASE_ID={release_id}\n"
        f"XIANYU_ASSET_REVISION={'a' * 64}\n",
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
    previous = _snapshot(component_root, "20260720T010000Z")
    current = _snapshot(component_root, "20260720T020000Z", previous=previous)
    failed = _snapshot(component_root, "20260720T030000Z", previous=current)
    (component_root / "current").symlink_to(current)
    environment = {
        **_script_environment(tmp_path),
        "RELEASE_ROOT": str(release_root),
        "RELEASE_TARGET": "connector",
    }

    result = _run(DEPLOY_ROOT / "rollback.sh", env=environment)

    assert result.returncode == 0, result.stderr
    assert (component_root / "current").resolve() == previous.resolve()
    assert failed.resolve() != (component_root / "current").resolve()
    docker_log = Path(environment["DOCKER_LOG"]).read_text(encoding="utf-8")
    assert str(previous / "production.env") in docker_log
    assert str(failed) not in docker_log


def test_restore_current_redeploys_last_successful_snapshot_after_failed_release(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    previous = _snapshot(component_root, "20260720T010000Z")
    current = _snapshot(component_root, "20260720T020000Z", previous=previous)
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
    assert (component_root / "current").resolve() == current.resolve()
    docker_log = Path(environment["DOCKER_LOG"]).read_text(encoding="utf-8")
    assert str(current / "production.env") in docker_log


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


def test_failed_ghcr_promotion_restores_env_and_prints_explicit_recovery_command(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    current = _snapshot(component_root, "20260720T010000Z")
    (component_root / "current").symlink_to(current)
    compose = tmp_path / "compose.production.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file = tmp_path / "production.env"
    original = (
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
    assert env_file.read_text(encoding="utf-8") == original
    assert "RESTORE_CURRENT=true RELEASE_TARGET=connector" in result.stderr
    assert str(DEPLOY_ROOT / "rollback.sh") in result.stderr
    assert (component_root / "current").resolve() == current.resolve()


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
    original = "XIANYU_IMAGE=unchanged\nXIANYU_REMOTE_VERIFICATION_ENABLED=true\n"
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


def test_production_example_and_docs_make_control_default_but_automation_target_explicit() -> None:
    production_env = (DEPLOY_ROOT / "production.env.example").read_text(encoding="utf-8")
    readme = (DEPLOY_ROOT / "README.md").read_text(encoding="utf-8")
    promotion = (DEPLOY_ROOT / "promote-ghcr-release.sh").read_text(encoding="utf-8")

    assert "RELEASE_TARGET=control" in production_env
    assert "RELEASE_TARGET=all" not in production_env
    assert "RELEASE_TARGET=control" in readme
    assert "promote-ghcr-release.sh <ghcr-image@sha256:digest> <control|connector|all>" in readme
    assert 'if [ "$#" -ne 2 ]' in promotion
    assert 'RELEASE_TARGET="$release_target"' in promotion
    assert "non-backward-compatible" in readme
    assert "restore" in readme.lower()
