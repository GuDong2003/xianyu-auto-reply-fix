#!/bin/sh
set -eu

umask 077
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH

if [ "$(id -u)" -ne 0 ]; then
    echo "bootstrap-ghcr-rollback-baseline.sh must run as root" >&2
    exit 2
fi
if [ "$#" -ne 3 ]; then
    echo "usage: bootstrap-ghcr-rollback-baseline.sh <trusted-ghcr-digest> <control|connector|all> <source-run-id>" >&2
    exit 2
fi

image=$1
release_target=$2
source_run_id=$3
if ! printf '%s\n' "$image" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
    echo "baseline image must be a lowercase immutable GHCR digest" >&2
    exit 2
fi
case "$release_target" in
    control|connector|all) ;;
    *) echo "release target must be control, connector, or all" >&2; exit 2 ;;
esac
if ! printf '%s\n' "$source_run_id" | grep -Eq '^[1-9][0-9]*$'; then
    echo "source run id must be a positive GitHub Actions run id" >&2
    exit 2
fi

env_file=${ENV_FILE:-/opt/xianyu-production/production.env}
release_root=${RELEASE_ROOT:-/opt/xianyu/releases}
if [ ! -r "$env_file" ]; then
    echo "production environment file is not readable: $env_file" >&2
    exit 2
fi
repository_count=$(grep -c '^XIANYU_GHCR_REPOSITORY=' "$env_file" || true)
if [ "$repository_count" -ne 1 ]; then
    echo "production environment must define XIANYU_GHCR_REPOSITORY exactly once" >&2
    exit 2
fi
fixed_repository=$(sed -n 's/^XIANYU_GHCR_REPOSITORY=//p' "$env_file")
if ! printf '%s\n' "$fixed_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "production environment must fix a lowercase GHCR repository" >&2
    exit 2
fi
case "$image" in
    "$fixed_repository"@sha256:*) ;;
    *) echo "baseline image is outside XIANYU_GHCR_REPOSITORY" >&2; exit 2 ;;
esac
github_repository_count=$(grep -c '^XIANYU_GITHUB_REPOSITORY=' "$env_file" || true)
if [ "$github_repository_count" -ne 1 ]; then
    echo "production environment must define XIANYU_GITHUB_REPOSITORY exactly once" >&2
    exit 2
fi
github_repository=$(sed -n 's/^XIANYU_GITHUB_REPOSITORY=//p' "$env_file")
if ! printf '%s\n' "$github_repository" \
    | grep -Eq '^[A-Za-z0-9]+(-[A-Za-z0-9]+)*/[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$'; then
    echo "production environment must fix a canonical GitHub repository" >&2
    exit 2
fi
github_repository_lower=$(printf '%s\n' "$github_repository" | tr '[:upper:]' '[:lower:]')
if [ "$github_repository_lower" != "${fixed_repository#ghcr.io/}" ]; then
    echo "XIANYU_GITHUB_REPOSITORY does not correspond to XIANYU_GHCR_REPOSITORY" >&2
    exit 2
fi
trusted_image_count=$(grep -c '^XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE=' "$env_file" || true)
trusted_run_count=$(grep -c '^XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID=' "$env_file" || true)
trusted_commit_count=$(grep -c '^XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT=' "$env_file" || true)
trusted_compose_count=$(grep -c '^XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256=' "$env_file" || true)
if [ "$trusted_image_count" -ne 1 ] || [ "$trusted_run_count" -ne 1 ] \
    || [ "$trusted_commit_count" -ne 1 ] || [ "$trusted_compose_count" -ne 1 ]; then
    echo "production environment must define the rollback baseline trust tuple exactly once" >&2
    exit 2
fi
trusted_image=$(sed -n 's/^XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE=//p' "$env_file")
trusted_run_id=$(sed -n 's/^XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID=//p' "$env_file")
trusted_commit=$(sed -n 's/^XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT=//p' "$env_file")
trusted_compose_sha=$(sed -n 's/^XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256=//p' "$env_file")
if [ "$image" != "$trusted_image" ] || [ "$source_run_id" != "$trusted_run_id" ]; then
    echo "requested baseline does not match the fixed image and source run" >&2
    exit 2
fi
if ! printf '%s\n' "$trusted_commit" | grep -Eq '^[0-9a-f]{40}$'; then
    echo "rollback baseline commit must be a full lowercase commit" >&2
    exit 2
fi
if ! printf '%s\n' "$trusted_compose_sha" | grep -Eq '^[0-9a-f]{64}$'; then
    echo "rollback baseline Compose hash must be a lowercase sha256" >&2
    exit 2
fi

lock_file=${LOCK_FILE:-/run/lock/xianyu-production-deploy.lock}
exec 9>"$lock_file"
if ! /usr/bin/flock -n 9; then
    echo "another production deployment is active" >&2
    exit 75
fi

docker pull --platform linux/amd64 "$image" >/dev/null
repo_digests=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image")
if ! printf '%s\n' "$repo_digests" | grep -Fqx "$image"; then
    echo "pulled image RepoDigests do not contain the requested digest" >&2
    exit 2
fi
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
if [ "$platform" != "linux/amd64" ]; then
    echo "baseline image platform must be linux/amd64, got $platform" >&2
    exit 2
fi
image_revision=$(docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image")
image_source=$(docker image inspect \
    --format '{{index .Config.Labels "org.opencontainers.image.source"}}' "$image")
if [ "$image_revision" != "$trusted_commit" ]; then
    echo "baseline image revision does not match the fixed commit" >&2
    exit 2
fi
if [ "$image_source" != "https://github.com/$github_repository" ]; then
    echo "baseline image source does not match XIANYU_GITHUB_REPOSITORY" >&2
    exit 2
fi

asset_revision=${image##*@sha256:}
baseline_id="ghcr-baseline-$source_run_id-$(printf '%.16s' "$asset_revision")"
component_root=$(cd "$release_root/$release_target" && pwd -P)
baseline_dir="$component_root/$baseline_id"

python3 - "$release_root" "$release_target" "$image" "$fixed_repository" \
    "$github_repository" "$source_run_id" "$trusted_commit" "$trusted_compose_sha" \
    "$baseline_id" <<'PY'
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

release_root = Path(sys.argv[1]).resolve(strict=True)
(
    release_target,
    image,
    fixed_repository,
    github_repository,
    source_run_id,
    trusted_commit,
    trusted_compose_sha,
    baseline_id,
) = sys.argv[2:]
asset_revision = image.rsplit("@sha256:", 1)[1]
component_root = (release_root / release_target).resolve(strict=True)
if component_root.parent != release_root:
    raise SystemExit("release component root escapes RELEASE_ROOT")
current_link = component_root / "current"
if not current_link.is_symlink():
    raise SystemExit(f"no current {release_target} release is recorded")
current = current_link.resolve(strict=True)
if current.parent != component_root:
    raise SystemExit("current release points outside its component root")
previous_record = current / "previous-current"
if not previous_record.is_file():
    raise SystemExit("current release has no legacy previous-current record")


def read_unique_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key in result:
            raise SystemExit(f"duplicate {key} in {path}")
        result[key] = value
    return result


def require_snapshot(path: Path) -> None:
    if path.parent != component_root or not path.is_dir() or path.is_symlink():
        raise SystemExit(f"snapshot is outside component root: {path}")
    for name in ("compose.production.yml", "production.env", "release.env"):
        if not (path / name).is_file():
            raise SystemExit(f"snapshot is incomplete: {path / name}")


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sanitized_environment(source: Path) -> str:
    updates = {
        "XIANYU_IMAGE": image,
        "XIANYU_GHCR_REPOSITORY": fixed_repository,
        "XIANYU_GITHUB_REPOSITORY": github_repository,
        "XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE": image,
        "XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID": source_run_id,
        "XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT": trusted_commit,
        "XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256": trusted_compose_sha,
        "XIANYU_REMOTE_VERIFICATION_ENABLED": "false",
        "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN": "",
    }
    seen: set[str] = set()
    output: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        key, separator, _ = line.partition("=")
        if separator and key in updates:
            if key not in seen:
                output.append(f"{key}={updates[key]}")
                seen.add(key)
            continue
        output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    return "\n".join(output) + "\n"


baseline = component_root / baseline_id
require_snapshot(current)
current_release = read_unique_env(current / "release.env")
if current_release.get("RELEASE_TARGET") != release_target:
    raise SystemExit("current release target does not match the requested component")
current_image = current_release.get("XIANYU_IMAGE", "")
if not re.fullmatch(re.escape(fixed_repository) + r"@sha256:[0-9a-f]{64}", current_image):
    raise SystemExit("current release is not an immutable image in the fixed GHCR repository")
if current_release.get("XIANYU_ASSET_REVISION") != current_image.rsplit("@sha256:", 1)[1]:
    raise SystemExit("current release identity does not match its image digest")
current_compose = current / "compose.production.yml"
compose_sha256 = hashlib.sha256(current_compose.read_bytes()).hexdigest()
if compose_sha256 != trusted_compose_sha:
    raise SystemExit("trusted current Compose hash does not match the fixed baseline hash")

recorded_lines = previous_record.read_text(encoding="utf-8").splitlines()
if len(recorded_lines) != 1:
    raise SystemExit("current previous-current record is malformed")
recorded_previous = Path(recorded_lines[0]).resolve(strict=True)
if baseline.exists():
    require_snapshot(baseline)
    baseline_release = read_unique_env(baseline / "release.env")
    original_legacy = Path(
        baseline_release.get("XIANYU_BASELINE_ORIGINAL_RELEASE", "")
    )
else:
    original_legacy = recorded_previous

if recorded_previous != baseline:
    require_snapshot(recorded_previous)
    previous_release = read_unique_env(recorded_previous / "release.env")
    previous_image = previous_release.get("XIANYU_IMAGE", "")
    if previous_release.get("RELEASE_TARGET") != release_target:
        raise SystemExit("legacy snapshot target does not match the requested component")
    if not previous_image:
        raise SystemExit("legacy snapshot has no image identity")
    if re.fullmatch(r"ghcr\.io/.+@sha256:[0-9a-f]{64}", previous_image):
        raise SystemExit("current previous release is already an immutable GHCR snapshot")
    original_legacy = recorded_previous

require_snapshot(original_legacy)
release_values = {
    "XIANYU_IMAGE": image,
    "RELEASE_TARGET": release_target,
    "XIANYU_RELEASE_ID": baseline_id,
    "XIANYU_ASSET_REVISION": asset_revision,
    "XIANYU_BASELINE_SOURCE_RUN_ID": source_run_id,
    "XIANYU_BASELINE_SOURCE_COMMIT": trusted_commit,
    "XIANYU_INTERNAL_HEALTH_PROBE": "legacy-qr-404",
    "XIANYU_BASELINE_ORIGINAL_RELEASE": str(original_legacy),
}

if not baseline.exists():
    temporary = Path(tempfile.mkdtemp(prefix=f".{baseline_id}.", dir=component_root))
    try:
        shutil.copyfile(current_compose, temporary / "compose.production.yml")
        (temporary / "compose.production.yml").chmod(0o644)
        (temporary / "production.env").write_text(
            sanitized_environment(original_legacy / "production.env"),
            encoding="utf-8",
        )
        (temporary / "production.env").chmod(0o600)
        (temporary / "release.env").write_text(
            "".join(f"{key}={value}\n" for key, value in release_values.items()),
            encoding="utf-8",
        )
        (temporary / "release.env").chmod(0o600)
        for child in temporary.iterdir():
            os.chown(child, 0, 0)
            with child.open("rb") as stream:
                os.fsync(stream.fileno())
        temporary.chmod(0o700)
        os.chown(temporary, 0, 0)
        fsync_directory(temporary)
        os.replace(temporary, baseline)
        fsync_directory(component_root)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

require_snapshot(baseline)
if read_unique_env(baseline / "release.env") != release_values:
    raise SystemExit("existing baseline release identity does not match requested bootstrap")
baseline_env = read_unique_env(baseline / "production.env")
expected_environment = sanitized_environment(original_legacy / "production.env")
if (baseline / "production.env").read_text(encoding="utf-8") != expected_environment:
    raise SystemExit("existing baseline environment differs from the sanitized legacy source")
expected_env = {
    "XIANYU_IMAGE": image,
    "XIANYU_GHCR_REPOSITORY": fixed_repository,
    "XIANYU_GITHUB_REPOSITORY": github_repository,
    "XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE": image,
    "XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID": source_run_id,
    "XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT": trusted_commit,
    "XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256": trusted_compose_sha,
    "XIANYU_REMOTE_VERIFICATION_ENABLED": "false",
    "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN": "",
}
for key, value in expected_env.items():
    if baseline_env.get(key) != value:
        raise SystemExit(f"existing baseline has invalid {key}")
if (baseline / "compose.production.yml").read_bytes() != current_compose.read_bytes():
    raise SystemExit("existing baseline compose file differs from the trusted current release")
print(f"GHCR rollback baseline prepared: {baseline}")
print(f"original legacy snapshot: {original_legacy}")
PY

compose_json=$(XIANYU_RELEASE_ID=$baseline_id XIANYU_ASSET_REVISION=$asset_revision \
    docker compose --env-file "$baseline_dir/production.env" \
    -f "$baseline_dir/compose.production.yml" config --format json)
printf '%s\n' "$compose_json" | python3 -c '
import json
import sys

payload = json.load(sys.stdin)
release_id, asset_revision = sys.argv[1:]
for service_name in ("xianyu-control", "xianyu-connector"):
    environment = payload.get("services", {}).get(service_name, {}).get("environment", {})
    if environment.get("XIANYU_RELEASE_ID") != release_id:
        raise SystemExit(f"{service_name} has no matching release id")
    if environment.get("XIANYU_ASSET_REVISION") != asset_revision:
        raise SystemExit(f"{service_name} has no matching asset revision")
' "$baseline_id" "$asset_revision"

python3 - "$component_root" "$baseline_dir" <<'PY'
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

component_root = Path(sys.argv[1]).resolve(strict=True)
baseline = Path(sys.argv[2]).resolve(strict=True)
if baseline.parent != component_root:
    raise SystemExit("baseline escapes its component root")
current_link = component_root / "current"
if not current_link.is_symlink():
    raise SystemExit("current release link disappeared before baseline commit")
current = current_link.resolve(strict=True)
if current.parent != component_root:
    raise SystemExit("current release points outside its component root")
previous_record = current / "previous-current"
release_values = {}
for line in (baseline / "release.env").read_text(encoding="utf-8").splitlines():
    key, separator, value = line.partition("=")
    if separator:
        release_values[key] = value
original_legacy = Path(release_values["XIANYU_BASELINE_ORIGINAL_RELEASE"]).resolve(
    strict=True
)
recorded_previous = Path(previous_record.read_text(encoding="utf-8").strip()).resolve(
    strict=True
)
if recorded_previous not in {original_legacy, baseline}:
    raise SystemExit("current previous-current changed before baseline commit")

descriptor, temporary_name = tempfile.mkstemp(prefix=".previous-current.", dir=current)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
        temporary.write(f"{baseline}\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        os.fchmod(temporary.fileno(), stat.S_IRUSR | stat.S_IWUSR)
        os.fchown(temporary.fileno(), 0, 0)
    os.replace(temporary_name, previous_record)
    directory = os.open(current, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
except BaseException:
    Path(temporary_name).unlink(missing_ok=True)
    raise
if current_link.resolve(strict=True) != current:
    raise SystemExit("current release changed during baseline commit")
print(f"GHCR rollback baseline ready: {baseline}")
PY
