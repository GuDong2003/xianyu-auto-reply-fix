from __future__ import annotations

import hashlib
import os
import re
import shlex
import subprocess
import sys
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
PROVENANCE_GHCR_REPOSITORY = "ghcr.io/wangjunkai-1996/xianyu-auto-reply-fix"
PROVENANCE_GITHUB_REPOSITORY = "Wangjunkai-1996/xianyu-auto-reply-fix"
PROVENANCE_IMAGE = f"{PROVENANCE_GHCR_REPOSITORY}@{IMAGE_DIGEST}"
BASELINE_RUN_ID = "123456789"
BASELINE_COMMIT = "d" * 40
BASELINE_ID = f"ghcr-baseline-{BASELINE_RUN_ID}-{'a' * 16}"


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
        *org.opencontainers.image.revision*)
            printf '%s\n' "${IMAGE_REVISION:-dddddddddddddddddddddddddddddddddddddddd}" ;;
        *org.opencontainers.image.source*)
            printf '%s\n' "${IMAGE_SOURCE:-https://github.com/Example/xianyu-connector}" ;;
    esac
    exit 0
fi
if [ "$1" = "compose" ]; then
    case " $* " in
        *" config "*)
            printf '{"services":{"xianyu-control":{"environment":{"XIANYU_RELEASE_ID":"%s","XIANYU_ASSET_REVISION":"%s"}},"xianyu-connector":{"environment":{"XIANYU_RELEASE_ID":"%s","XIANYU_ASSET_REVISION":"%s"}}}}\n' \
                "$XIANYU_RELEASE_ID" "$XIANYU_ASSET_REVISION" \
                "$XIANYU_RELEASE_ID" "$XIANYU_ASSET_REVISION"
            exit 0
            ;;
    esac
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
        f"#!/bin/sh\nset -eu\nprintf '%s\\n' \"$@\" > {shlex.quote(str(sudo_log))}\n",
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
        'if [ "$1" = --version ]; then\n'
        "  printf '%s\\n' 'gh version 2.96.0 (test)'\n"
        "  exit 0\n"
        "fi\n"
        f"printf 'GH_TOKEN=%s\\n' \"${{GH_TOKEN-unset}}\" > "
        f"{shlex.quote(str(gh_environment_log))}\n"
        f"printf 'GITHUB_TOKEN=%s\\n' \"${{GITHUB_TOKEN-unset}}\" >> "
        f"{shlex.quote(str(gh_environment_log))}\n",
    )
    verifier = _copy_deploy_script(
        "verify-github-provenance.sh",
        tmp_path,
        {"gh=/usr/bin/gh": f"gh={shlex.quote(str(fake_gh))}"},
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
        f"  {index}) exit {exit_code} ;;\n" for index, exit_code in enumerate(exit_codes, start=1)
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
        'case "$count" in\n'
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
        'case "$1" in\n'
        "  compose)\n"
        '    case "$*" in\n'
        "      *xianyu-control) printf '%s\\n' control-container ;;\n"
        "      *) printf '%s\\n' connector-container ;;\n"
        "    esac\n"
        "    ;;\n"
        "  inspect)\n"
        '    case "$3" in\n'
        "      *State.Running*) printf '%s\\n' true ;;\n"
        "      *State.Health*) printf '%s\\n' healthy ;;\n"
        "      *RestartCount*) printf '%s\\n' 0 ;;\n"
        "      *RestartPolicy.Name*) printf '%s\\n' unless-stopped ;;\n"
        "      *Config.Image*) printf '%s\\n' \"$EXPECTED_IMAGE\" ;;\n"
        "      *Config.Env*)\n"
        f"        printf '%s\\n' XIANYU_RELEASE_ID={release_id} \\\n"
        '          XIANYU_ASSET_REVISION="${EXPECTED_IMAGE##*@sha256:}" \\\n'
        "          XIANYU_REMOTE_VERIFICATION_ENABLED=false\n"
        "        ;;\n"
        "    esac\n"
        "    ;;\n"
        "  port)\n"
        '    if [ "$2" = control-container ]; then\n'
        "      printf '%s\\n' 127.0.0.1:9000\n"
        "    fi\n"
        "    ;;\n"
        f"  exec) cat >> {shlex.quote(str(docker_stdin_log))} ;;\n"
        "esac\n",
    )


def _write_http_semantics_verify_docker(
    bin_dir: Path,
    *,
    docker_log: Path,
    harness: Path,
    release_id: str,
) -> Path:
    return _write_executable(
        bin_dir / "docker",
        "#!/bin/sh\n"
        "set -eu\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(docker_log))}\n"
        'case "$1" in\n'
        "  compose)\n"
        '    case "$*" in\n'
        "      *xianyu-control) printf '%s\\n' control-container ;;\n"
        "      *) printf '%s\\n' connector-container ;;\n"
        "    esac\n"
        "    ;;\n"
        "  inspect)\n"
        '    case "$3" in\n'
        "      *State.Running*) printf '%s\\n' true ;;\n"
        "      *State.Health*) printf '%s\\n' healthy ;;\n"
        "      *RestartCount*) printf '%s\\n' 0 ;;\n"
        "      *RestartPolicy.Name*) printf '%s\\n' unless-stopped ;;\n"
        "      *Config.Image*) printf '%s\\n' \"$EXPECTED_IMAGE\" ;;\n"
        "      *Config.Env*)\n"
        f"        printf '%s\\n' XIANYU_RELEASE_ID={release_id} \\\n"
        '          XIANYU_ASSET_REVISION="${EXPECTED_IMAGE##*@sha256:}" \\\n'
        "          XIANYU_REMOTE_VERIFICATION_ENABLED=false\n"
        "        ;;\n"
        "    esac\n"
        "    ;;\n"
        "  port)\n"
        '    if [ "$2" = control-container ]; then\n'
        "      printf '%s\\n' 127.0.0.1:9000\n"
        "    fi\n"
        "    ;;\n"
        "  exec)\n"
        "    shift 5\n"
        f'    {shlex.quote(sys.executable)} {shlex.quote(str(harness))} "$@"\n'
        "    ;;\n"
        "esac\n",
    )


def _release_verification_http_environment(
    tmp_path: Path,
    mode: str,
    *,
    image: str = GHCR_IMAGE,
    metadata_override: dict[str, str] | None = None,
) -> dict[str, str]:
    environment, docker_log, _, _ = _release_verification_environment(
        tmp_path,
        "connector",
    )
    token_file = tmp_path / "connector-token"
    token_file.write_text(
        "wrong-token\n" if mode == "wrong-token" else "expected-token\n",
        encoding="utf-8",
    )
    release = tmp_path / "releases" / "connector" / "release-20260720"
    compose_sha256 = hashlib.sha256((release / "compose.production.yml").read_bytes()).hexdigest()
    _write_executable(
        tmp_path / "bin" / "sha256sum",
        f"#!/bin/sh\nprintf '%s  %s\\n' {shlex.quote(compose_sha256)} \"$1\"\n",
    )
    baseline_metadata = {
        "XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE": GHCR_IMAGE,
        "XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID": BASELINE_RUN_ID,
        "XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT": BASELINE_COMMIT,
        "XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256": compose_sha256,
    }
    baseline_metadata.update(metadata_override or {})
    environment_lines = [
        line
        for line in Path(environment["ENV_FILE"]).read_text(encoding="utf-8").splitlines()
        if not line.startswith("XIANYU_IMAGE=")
    ]
    environment_lines.append(f"XIANYU_IMAGE={image}")
    environment_lines.extend(f"{key}={value}" for key, value in baseline_metadata.items())
    environment_source = "\n".join(environment_lines) + "\n"
    Path(environment["ENV_FILE"]).write_text(environment_source, encoding="utf-8")
    (release / "production.env").write_text(environment_source, encoding="utf-8")
    release_id = BASELINE_ID if image == GHCR_IMAGE else "future-release"
    (release / "release.env").write_text(
        f"XIANYU_IMAGE={image}\n"
        "RELEASE_TARGET=connector\n"
        f"XIANYU_RELEASE_ID={release_id}\n"
        f"XIANYU_ASSET_REVISION={image.rsplit(':', 1)[1]}\n"
        f"XIANYU_BASELINE_SOURCE_RUN_ID={BASELINE_RUN_ID}\n"
        f"XIANYU_BASELINE_SOURCE_COMMIT={BASELINE_COMMIT}\n"
        "XIANYU_INTERNAL_HEALTH_PROBE=legacy-qr-404\n"
        "XIANYU_BASELINE_ORIGINAL_RELEASE=/opt/xianyu/releases/connector/legacy\n",
        encoding="utf-8",
    )
    harness = tmp_path / "verify-http-harness.py"
    harness.write_text(
        """from __future__ import annotations

import io
import json
import os
import re
import sys
import urllib.error
import urllib.request


class Response(io.BytesIO):
    def __init__(self, status: int, payload: dict[str, object]) -> None:
        super().__init__(json.dumps(payload).encode("utf-8"))
        self.status = status

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def http_error(url: str, status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url,
        status,
        "simulated",
        None,
        io.BytesIO(b'{"detail":"simulated"}'),
    )


def urlopen(request: object, timeout: int = 0) -> Response:
    del timeout
    url = getattr(request, "full_url", str(request))
    headers = {
        key.lower(): value
        for key, value in getattr(request, "header_items", lambda: [])()
    }
    token = headers.get("x-connector-token")
    mode = os.environ["FAKE_INTERNAL_HEALTH_MODE"]
    expected_token = "expected-token"
    with open(os.environ["FAKE_HTTP_LOG"], "a", encoding="utf-8") as log:
        token_class = "expected" if token == expected_token else "other"
        log.write(f"{url}|token={token_class}\\n")
    if url.endswith("/health/live"):
        return Response(200, {"status": "healthy"})
    if url.endswith("/internal/health"):
        if mode == "current":
            if token != expected_token:
                raise http_error(url, 401)
            return Response(200, {"status": "healthy"})
        raise http_error(url, 404)
    if re.search(r"/internal/accounts/[^/]+/qr-sessions/[^/]+$", url):
        if token != expected_token:
            raise http_error(url, 401)
        raise http_error(url, 404)
    raise AssertionError(f"unexpected verifier URL: {url}")


source = sys.stdin.read().replace(
    "/run/secrets/connector_internal_token",
    os.environ["FAKE_CONNECTOR_TOKEN_FILE"],
)
urllib.request.urlopen = urlopen
exec(compile(source, "<docker-exec-verifier>", "exec"), {"__name__": "__main__"})
""",
        encoding="utf-8",
    )
    _write_http_semantics_verify_docker(
        tmp_path / "bin",
        docker_log=docker_log,
        harness=harness,
        release_id=release_id,
    )
    return {
        **environment,
        "EXPECTED_IMAGE": image,
        "FAKE_CONNECTOR_TOKEN_FILE": str(token_file),
        "FAKE_HTTP_LOG": str(tmp_path / "http.log"),
        "FAKE_INTERNAL_HEALTH_MODE": mode,
    }


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
    compose_source = "services: {}\n"
    compose_sha256 = hashlib.sha256(compose_source.encode("utf-8")).hexdigest()
    environment_source = (
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        f"XIANYU_IMAGE={GHCR_IMAGE}\n"
        f"XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE={GHCR_IMAGE}\n"
        f"XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID={BASELINE_RUN_ID}\n"
        f"XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT={BASELINE_COMMIT}\n"
        "XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256="
        f"{compose_sha256}\n"
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
        f"XIANYU_ASSET_REVISION={'a' * 64}\n"
        f"XIANYU_BASELINE_SOURCE_RUN_ID={BASELINE_RUN_ID}\n"
        f"XIANYU_BASELINE_SOURCE_COMMIT={BASELINE_COMMIT}\n"
        "XIANYU_INTERNAL_HEALTH_PROBE=legacy-qr-404\n",
        encoding="utf-8",
    )
    (release / "compose.production.yml").write_text(compose_source, encoding="utf-8")
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
        bin_dir / "sha256sum",
        f"#!/bin/sh\nprintf '%s  %s\\n' {shlex.quote(compose_sha256)} \"$1\"\n",
    )
    _write_executable(
        bin_dir / "readlink",
        f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(str(release.resolve()))}\n",
    )
    _write_executable(
        bin_dir / "python3",
        f"#!/bin/sh\ncat > {shlex.quote(str(host_python_log))}\n",
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


def _bootstrap_baseline_environment(
    tmp_path: Path,
    *,
    previous_current: str | None = None,
) -> tuple[dict[str, str], Path, Path, Path]:
    release_root = tmp_path / "releases"
    component_root = release_root / "connector"
    legacy_release = component_root / "legacy-release"
    current_release = component_root / "current-release"
    legacy_release.mkdir(parents=True)
    current_release.mkdir(parents=True)
    compose_file = current_release / "compose.production.yml"
    compose_file.write_text(
        "services:\n"
        "  xianyu-connector:\n"
        "    environment:\n"
        "      XIANYU_RELEASE_ID: ${XIANYU_RELEASE_ID:?release identity required}\n"
        "      XIANYU_ASSET_REVISION: ${XIANYU_ASSET_REVISION:?asset revision required}\n",
        encoding="utf-8",
    )
    trusted_compose_sha256 = hashlib.sha256(compose_file.read_bytes()).hexdigest()
    environment_source = (
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        "XIANYU_GITHUB_REPOSITORY=Example/xianyu-connector\n"
        f"XIANYU_IMAGE={GHCR_IMAGE}\n"
        f"XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE={GHCR_IMAGE}\n"
        f"XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID={BASELINE_RUN_ID}\n"
        f"XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT={BASELINE_COMMIT}\n"
        "XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256="
        f"{trusted_compose_sha256}\n"
        "XIANYU_EXPECTED_EGRESS_IP=203.0.113.10\n"
        "XIANYU_SHADOW_MODE=true\n"
        "XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED=false\n"
        "XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN=\n"
        "XIANYU_REMOTE_VERIFICATION_ENABLED=true\n"
        "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=https://disabled.example.test\n"
        "XIANYU_CONTROL_PORT=9000\n"
        "XIANYU_DATA_DIR=/srv/xianyu/data\n"
        "XIANYU_PROFILES_DIR=/srv/xianyu/profiles\n"
        "XIANYU_LOGS_DIR=/srv/xianyu/logs\n"
        "XIANYU_BACKUPS_DIR=/srv/xianyu/backups\n"
        "XIANYU_UPLOADS_DIR=/srv/xianyu/uploads\n"
        "XIANYU_TRAJECTORY_DIR=/srv/xianyu/trajectory\n"
        "XIANYU_CONFIG_FILE=/srv/xianyu/global_config.yml\n"
        "XIANYU_MASTER_KEY_FILE=/srv/xianyu/secrets/master\n"
        "XIANYU_CONNECTOR_TOKEN_FILE=/srv/xianyu/secrets/connector\n"
        "XIANYU_LEGACY_SECRET_FILE=/srv/xianyu/secrets/legacy\n"
        "XIANYU_ADMIN_PASSWORD_FILE=/srv/xianyu/secrets/admin\n"
        "XIANYU_JWT_SECRET_FILE=/srv/xianyu/secrets/jwt\n"
        f"COMPOSE_FILE={compose_file}\n"
        f"ENV_FILE={tmp_path / 'production.env'}\n"
        f"RELEASE_ROOT={release_root}\n"
        "RELEASE_TARGET=connector\n"
    )
    env_file = tmp_path / "production.env"
    env_file.write_text(environment_source, encoding="utf-8")
    env_file.chmod(0o600)
    (current_release / "production.env").write_text(
        environment_source,
        encoding="utf-8",
    )
    (current_release / "release.env").write_text(
        f"XIANYU_IMAGE={GHCR_IMAGE}\n"
        "RELEASE_TARGET=connector\n"
        "XIANYU_RELEASE_ID=current-release\n"
        f"XIANYU_ASSET_REVISION={'a' * 64}\n",
        encoding="utf-8",
    )
    legacy_environment = environment_source.replace(
        f"XIANYU_IMAGE={GHCR_IMAGE}",
        "XIANYU_IMAGE=registry.example.test/xianyu-connector:legacy",
    )
    (legacy_release / "production.env").write_text(
        legacy_environment,
        encoding="utf-8",
    )
    (legacy_release / "release.env").write_text(
        "XIANYU_IMAGE=registry.example.test/xianyu-connector:legacy\n"
        "RELEASE_TARGET=connector\n"
        "XIANYU_RELEASE_ID=legacy-release\n"
        "XIANYU_ASSET_REVISION=legacy\n",
        encoding="utf-8",
    )
    (legacy_release / "compose.production.yml").write_text(
        "services:\n  legacy: {}\n",
        encoding="utf-8",
    )
    recorded_previous = previous_current or str(legacy_release.resolve())
    (current_release / "previous-current").write_text(
        f"{recorded_previous}\n",
        encoding="utf-8",
    )
    (component_root / "current").symlink_to(current_release)
    (release_root / "current").symlink_to(current_release)
    environment = {
        **_script_environment(tmp_path),
        "ENV_FILE": str(env_file),
        "RELEASE_ROOT": str(release_root),
        "LOCK_FILE": str(tmp_path / "deployment.lock"),
    }
    environment["TEST_BOOTSTRAP_PATH"] = environment["PATH"]
    return environment, component_root, current_release, env_file


def _bootstrap_test_script(tmp_path: Path) -> Path:
    source = (DEPLOY_ROOT / "bootstrap-ghcr-rollback-baseline.sh").read_text(encoding="utf-8")
    root_guard = 'if [ "$(id -u)" -ne 0 ]; then'
    assert root_guard in source
    source = source.replace(
        root_guard,
        'if [ "${TEST_BOOTSTRAP_EUID:-0}" -ne 0 ]; then',
    )
    source = source.replace(
        "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PATH=${TEST_BOOTSTRAP_PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}",
    )
    source = source.replace(
        "exec 9>/run/lock/xianyu-production-deploy.lock",
        'exec 9>"${LOCK_FILE:-/run/lock/xianyu-production-deploy.lock}"',
    )
    fake_flock = _write_executable(
        tmp_path / "fake-flock",
        "#!/bin/sh\nexit 0\n",
    )
    source = source.replace("/usr/bin/flock", shlex.quote(str(fake_flock)))
    source = source.replace(
        "os.fchown(temporary.fileno(), 0, 0)",
        "if os.geteuid() == 0:\n            os.fchown(temporary.fileno(), 0, 0)",
    )
    source = source.replace(
        "            os.chown(child, 0, 0)",
        "            if os.geteuid() == 0:\n                os.chown(child, 0, 0)",
    )
    source = source.replace(
        "        os.chown(temporary, 0, 0)",
        "        if os.geteuid() == 0:\n            os.chown(temporary, 0, 0)",
    )
    return _write_executable(tmp_path / "bootstrap-test.sh", source)


def _environment_values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line
    )


def _run_bootstrap(
    tmp_path: Path,
    *args: str,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return _run(_bootstrap_test_script(tmp_path), *args, env=env)


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
        PROVENANCE_IMAGE,
        PROVENANCE_GHCR_REPOSITORY,
        PROVENANCE_GITHUB_REPOSITORY,
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
            f"attestation verify oci://{PROVENANCE_IMAGE} "
            f"--repo {PROVENANCE_GITHUB_REPOSITORY} "
            "--cert-identity https://github.com/Wangjunkai-1996/"
            "xianyu-auto-reply-fix/.github/workflows/docker-image.yml@refs/heads/main "
            "--cert-oidc-issuer https://token.actions.githubusercontent.com "
            "--source-ref refs/heads/main --deny-self-hosted-runners --bundle-from-oci"
        ),
    ]
    assert gh_environment_log.read_text(encoding="utf-8").splitlines() == [
        "GH_TOKEN=unused-for-oci-bundle",
        "GITHUB_TOKEN=unset",
    ]


def test_provenance_verifier_rejects_github_and_ghcr_repository_mismatch(
    tmp_path: Path,
) -> None:
    verifier, gh_log, _ = _provenance_verifier_with_fake_gh(tmp_path)

    result = _run(
        verifier,
        PROVENANCE_IMAGE,
        PROVENANCE_GHCR_REPOSITORY,
        "OtherOwner/xianyu-auto-reply-fix",
        env=os.environ.copy(),
    )

    assert result.returncode == 2
    assert "does not correspond" in result.stderr
    assert not gh_log.exists()


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
        f'printf \'RESTORE_CURRENT=%s %s\\n\' "${{RESTORE_CURRENT-unset}}" "$*" '
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
        "XIANYU_GHCR_REPOSITORY=ghcr.io/example/xianyu-connector\n"
        "XIANYU_GITHUB_REPOSITORY=Example/xianyu-connector\n",
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
            "release_root=/opt/xianyu/releases": (f"release_root={shlex.quote(str(release_root))}"),
            "exec 9>/run/lock/xianyu-production-deploy.lock": (
                f"exec 9>{shlex.quote(str(lock_file))}"
            ),
            "/usr/bin/flock": shlex.quote(str(fake_flock)),
        },
    )

    result = _run(orchestrator, GHCR_IMAGE, "control", env=os.environ.copy())

    assert result.returncode == 42
    assert provenance_log.read_text(encoding="utf-8").splitlines() == [
        f"{GHCR_IMAGE} ghcr.io/example/xianyu-connector Example/xianyu-connector"
    ]
    assert promote_log.read_text(encoding="utf-8").splitlines() == [f"{GHCR_IMAGE} control"]
    assert verify_log.read_text(encoding="utf-8").splitlines() == [
        f"{PREVIOUS_GHCR_IMAGE} control",
        f"{PREVIOUS_GHCR_IMAGE} control",
    ]
    assert rollback_log.read_text(encoding="utf-8").splitlines() == ["RESTORE_CURRENT=true control"]


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
    environment, docker_log, docker_stdin_log, host_python_log = _release_verification_environment(
        tmp_path, release_target
    )

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        release_target,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    docker_commands = docker_log.read_text(encoding="utf-8").splitlines()
    assert any(
        command.startswith("exec -i control-container python -") for command in docker_commands
    )
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


def test_release_verification_accepts_current_internal_health_200(
    tmp_path: Path,
) -> None:
    environment = _release_verification_http_environment(tmp_path, "current")

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    requests = Path(environment["FAKE_HTTP_LOG"]).read_text(encoding="utf-8")
    assert "/internal/health|token=expected" in requests
    assert "/qr-sessions/" not in requests


def test_release_verification_accepts_authenticated_legacy_missing_qr_404(
    tmp_path: Path,
) -> None:
    environment = _release_verification_http_environment(tmp_path, "legacy")

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    requests = Path(environment["FAKE_HTTP_LOG"]).read_text(encoding="utf-8").splitlines()
    assert any("/internal/health|token=expected" in request for request in requests)
    assert any("/qr-sessions/" in request for request in requests)
    assert all("token=expected" in request for request in requests if "/internal/" in request)


def test_release_verification_rejects_wrong_token_after_legacy_health_404(
    tmp_path: Path,
) -> None:
    environment = _release_verification_http_environment(tmp_path, "wrong-token")

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode != 0
    requests = Path(environment["FAKE_HTTP_LOG"]).read_text(encoding="utf-8").splitlines()
    assert any("/internal/health|token=other" in request for request in requests)
    assert any("/qr-sessions/" in request and "token=other" in request for request in requests)


def test_release_verification_rejects_legacy_fallback_for_future_image(
    tmp_path: Path,
) -> None:
    environment = _release_verification_http_environment(
        tmp_path,
        "legacy",
        image=FAILED_GHCR_IMAGE,
    )

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        FAILED_GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode != 0
    requests = Path(environment["FAKE_HTTP_LOG"]).read_text(encoding="utf-8").splitlines()
    assert any("/internal/health" in request for request in requests)
    assert not any("/qr-sessions/" in request for request in requests)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE", FAILED_GHCR_IMAGE),
        ("XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID", "987654321"),
        ("XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT", "e" * 40),
        ("XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256", "f" * 64),
    ],
)
def test_release_verification_rejects_legacy_fallback_metadata_mismatch(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    environment = _release_verification_http_environment(
        tmp_path,
        "legacy",
        metadata_override={key: value},
    )

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode != 0
    requests = Path(environment["FAKE_HTTP_LOG"]).read_text(encoding="utf-8").splitlines()
    assert not any("/qr-sessions/" in request for request in requests)


def test_release_verification_rejects_legacy_fallback_without_probe_marker(
    tmp_path: Path,
) -> None:
    environment = _release_verification_http_environment(tmp_path, "legacy")
    release_env = tmp_path / "releases" / "connector" / "release-20260720" / "release.env"
    release_env.write_text(
        release_env.read_text(encoding="utf-8").replace(
            "XIANYU_INTERNAL_HEALTH_PROBE=legacy-qr-404",
            "XIANYU_INTERNAL_HEALTH_PROBE=disabled",
        ),
        encoding="utf-8",
    )

    result = _run(
        DEPLOY_ROOT / "verify-production-release.sh",
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode != 0
    requests = Path(environment["FAKE_HTTP_LOG"]).read_text(encoding="utf-8").splitlines()
    assert not any("/qr-sessions/" in request for request in requests)


@pytest.mark.parametrize(
    "image",
    [
        "registry.example.test/example/xianyu-connector@sha256:" + "a" * 64,
        "ghcr.io/example/other@sha256:" + "a" * 64,
    ],
)
def test_bootstrap_baseline_rejects_non_ghcr_or_wrong_repository(
    tmp_path: Path,
    image: str,
) -> None:
    environment, component_root, current_release, env_file = _bootstrap_baseline_environment(
        tmp_path
    )
    original_environment = env_file.read_bytes()
    original_current = os.readlink(component_root / "current")
    previous = current_release / "previous-current"
    original_previous = previous.read_bytes()

    result = _run_bootstrap(
        tmp_path,
        image,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert result.returncode == 2
    assert env_file.read_bytes() == original_environment
    assert os.readlink(component_root / "current") == original_current
    assert previous.read_bytes() == original_previous
    assert not list(component_root.glob("*ghcr-baseline*"))


def test_bootstrap_baseline_rejects_arm64_before_snapshot_creation(
    tmp_path: Path,
) -> None:
    environment, component_root, current_release, _ = _bootstrap_baseline_environment(tmp_path)
    environment["IMAGE_PLATFORM"] = "linux/arm64"
    previous = current_release / "previous-current"
    original_previous = previous.read_bytes()

    result = _run_bootstrap(
        tmp_path,
        GHCR_IMAGE,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert result.returncode == 2
    assert "linux/amd64" in result.stderr
    assert previous.read_bytes() == original_previous
    assert not list(component_root.glob("*ghcr-baseline*"))


def test_bootstrap_baseline_requires_github_run_identity(tmp_path: Path) -> None:
    environment, component_root, current_release, _ = _bootstrap_baseline_environment(tmp_path)
    previous = current_release / "previous-current"
    original_previous = previous.read_bytes()

    result = _run_bootstrap(
        tmp_path,
        GHCR_IMAGE,
        "connector",
        env=environment,
    )

    assert result.returncode == 2
    assert previous.read_bytes() == original_previous
    assert not list(component_root.glob("*ghcr-baseline*"))


def test_bootstrap_baseline_rejects_invalid_existing_previous_pointer(
    tmp_path: Path,
) -> None:
    environment, component_root, current_release, env_file = _bootstrap_baseline_environment(
        tmp_path,
        previous_current=str(tmp_path / "outside-release-root"),
    )
    previous = current_release / "previous-current"
    original_previous = previous.read_bytes()
    original_environment = env_file.read_bytes()
    original_current = os.readlink(component_root / "current")

    result = _run_bootstrap(
        tmp_path,
        GHCR_IMAGE,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert result.returncode != 0
    assert previous.read_bytes() == original_previous
    assert env_file.read_bytes() == original_environment
    assert os.readlink(component_root / "current") == original_current
    assert not list(component_root.glob("*ghcr-baseline*"))


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE", FAILED_GHCR_IMAGE),
        ("XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID", "987654321"),
        ("XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT", ""),
        ("XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256", "f" * 64),
    ],
)
def test_bootstrap_baseline_rejects_fixed_identity_mismatch(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    environment, component_root, current_release, env_file = _bootstrap_baseline_environment(
        tmp_path
    )
    environment_values = _environment_values(env_file)
    environment_values[key] = value
    env_file.write_text(
        "".join(f"{name}={item}\n" for name, item in environment_values.items()),
        encoding="utf-8",
    )
    previous = current_release / "previous-current"
    original_previous = previous.read_bytes()

    result = _run_bootstrap(
        tmp_path,
        GHCR_IMAGE,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert result.returncode != 0
    assert previous.read_bytes() == original_previous
    assert (component_root / "current").resolve() == current_release.resolve()
    assert not list(component_root.glob("*ghcr-baseline*"))


def test_bootstrap_baseline_creates_complete_sanitized_snapshot_without_reconcile(
    tmp_path: Path,
) -> None:
    environment, component_root, current_release, env_file = _bootstrap_baseline_environment(
        tmp_path
    )
    root_current = component_root.parent / "current"
    original_environment = env_file.read_bytes()
    original_current_link = os.readlink(component_root / "current")
    original_root_current_link = os.readlink(root_current)
    original_legacy = Path(
        (current_release / "previous-current").read_text(encoding="utf-8").strip()
    )
    protected_snapshot = {
        name: (current_release / name).read_bytes()
        for name in (
            "production.env",
            "release.env",
            "compose.production.yml",
        )
    }

    result = _run_bootstrap(
        tmp_path,
        GHCR_IMAGE,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    baseline = (component_root / BASELINE_ID).resolve()
    assert baseline.is_dir()
    assert (current_release / "previous-current").read_text(encoding="utf-8").strip() == str(
        baseline
    )
    baseline_environment = _environment_values(baseline / "production.env")
    source_environment = _environment_values(env_file)
    assert set(baseline_environment) == set(source_environment)
    assert baseline_environment["XIANYU_IMAGE"] == GHCR_IMAGE
    assert baseline_environment["XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE"] == GHCR_IMAGE
    assert baseline_environment["XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID"] == BASELINE_RUN_ID
    assert baseline_environment["XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT"] == BASELINE_COMMIT
    assert (
        baseline_environment["XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256"]
        == hashlib.sha256(protected_snapshot["compose.production.yml"]).hexdigest()
    )
    assert baseline_environment["XIANYU_REMOTE_VERIFICATION_ENABLED"] == "false"
    assert baseline_environment["XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN"] == ""
    baseline_release = _environment_values(baseline / "release.env")
    assert baseline_release["XIANYU_IMAGE"] == GHCR_IMAGE
    assert baseline_release["RELEASE_TARGET"] == "connector"
    assert baseline_release["XIANYU_ASSET_REVISION"] == "a" * 64
    assert baseline_release["XIANYU_RELEASE_ID"] == BASELINE_ID
    assert baseline_release["XIANYU_BASELINE_SOURCE_RUN_ID"] == BASELINE_RUN_ID
    assert baseline_release["XIANYU_BASELINE_SOURCE_COMMIT"] == BASELINE_COMMIT
    assert baseline_release["XIANYU_INTERNAL_HEALTH_PROBE"] == "legacy-qr-404"
    assert baseline_release["XIANYU_BASELINE_ORIGINAL_RELEASE"] == str(original_legacy.resolve())
    assert (baseline / "compose.production.yml").read_bytes() == protected_snapshot[
        "compose.production.yml"
    ]
    baseline_compose = (baseline / "compose.production.yml").read_text(encoding="utf-8")
    assert "XIANYU_RELEASE_ID: ${XIANYU_RELEASE_ID:?release identity required}" in baseline_compose
    assert (
        "XIANYU_ASSET_REVISION: ${XIANYU_ASSET_REVISION:?asset revision required}"
        in baseline_compose
    )
    assert env_file.read_bytes() == original_environment
    assert os.readlink(component_root / "current") == original_current_link
    assert os.readlink(root_current) == original_root_current_link
    for name, contents in protected_snapshot.items():
        assert (current_release / name).read_bytes() == contents
    docker_commands = Path(environment["DOCKER_LOG"]).read_text(encoding="utf-8").splitlines()
    assert f"pull --platform linux/amd64 {GHCR_IMAGE}" in docker_commands
    assert any(
        " compose " in f" {command} " and " config " in f" {command} "
        for command in docker_commands
    )
    for mutating_command in ("up", "start", "restart", "stop"):
        assert not any(f" {mutating_command} " in f" {command} " for command in docker_commands)
    bootstrap_source = (DEPLOY_ROOT / "bootstrap-ghcr-rollback-baseline.sh").read_text(
        encoding="utf-8"
    )
    assert "os.replace" in bootstrap_source


def test_bootstrap_baseline_is_idempotent_for_same_github_run(tmp_path: Path) -> None:
    environment, component_root, current_release, _ = _bootstrap_baseline_environment(tmp_path)
    script = _bootstrap_test_script(tmp_path)

    first = _run(
        script,
        GHCR_IMAGE,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert first.returncode == 0, first.stderr
    first_baseline = (current_release / "previous-current").read_text(encoding="utf-8").strip()
    first_directories = sorted(path.resolve() for path in component_root.glob("*ghcr-baseline*"))

    second = _run(
        script,
        GHCR_IMAGE,
        "connector",
        BASELINE_RUN_ID,
        env=environment,
    )

    assert second.returncode == 0, second.stderr
    assert (current_release / "previous-current").read_text(
        encoding="utf-8"
    ).strip() == first_baseline
    assert (
        sorted(path.resolve() for path in component_root.glob("*ghcr-baseline*"))
        == first_directories
    )
    assert (component_root / "current").resolve() == current_release.resolve()


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
