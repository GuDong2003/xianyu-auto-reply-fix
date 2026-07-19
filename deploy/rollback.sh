#!/bin/sh
set -eu

umask 077

if [ "$#" -gt 1 ]; then
    echo "usage: rollback.sh [control|connector|all]" >&2
    exit 2
fi

release_root=${RELEASE_ROOT:-/opt/xianyu/releases}
release_target=${1:-${RELEASE_TARGET:-control}}
deployment_env_file=${ENV_FILE:-}
case "$release_target" in
    control|connector|all) ;;
    *) echo "RELEASE_TARGET must be control, connector, or all" >&2; exit 2 ;;
esac

component_root="$release_root/$release_target"
if [ ! -L "$component_root/current" ]; then
    echo "no current $release_target release is recorded" >&2
    exit 2
fi
component_root=$(cd "$component_root" && pwd -P)
current=$(readlink -f "$component_root/current")
case "$current" in
    "$component_root"/*) ;;
    *) echo "current release points outside $component_root" >&2; exit 2 ;;
esac

restore_current=${RESTORE_CURRENT:-false}
case "$restore_current" in
    true)
        source_release="$current"
        action="recovered current"
        ;;
    false)
        previous_record="$current/previous-current"
        if [ ! -r "$previous_record" ]; then
            echo "current release has no recorded previous release" >&2
            exit 2
        fi
        IFS= read -r recorded_previous < "$previous_record"
        case "$recorded_previous" in
            "$component_root"/*) ;;
            *) echo "recorded previous release points outside $component_root" >&2; exit 2 ;;
        esac
        if [ ! -d "$recorded_previous" ]; then
            echo "recorded previous release is unavailable: $recorded_previous" >&2
            exit 2
        fi
        source_release=$(cd "$recorded_previous" && pwd -P)
        action="rolled back"
        ;;
    *) echo "RESTORE_CURRENT must be true or false" >&2; exit 2 ;;
esac

source_env="$source_release/production.env"
source_release_env="$source_release/release.env"
source_compose="$source_release/compose.production.yml"
for required in "$source_env" "$source_release_env" "$source_compose"; do
    if [ ! -r "$required" ]; then
        echo "rollback source snapshot is incomplete: $required" >&2
        exit 2
    fi
done

source_image=$(sed -n 's/^XIANYU_IMAGE=//p' "$source_release_env")
source_asset_revision=$(sed -n 's/^XIANYU_ASSET_REVISION=//p' "$source_release_env")
if ! printf '%s\n' "$source_image" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
    echo "rollback source image is not an immutable GHCR digest" >&2
    exit 2
fi
if [ "$source_asset_revision" != "${source_image##*@sha256:}" ]; then
    echo "rollback source release identity does not match its image digest" >&2
    exit 2
fi

repository_source=$source_env
if [ -n "$deployment_env_file" ]; then
    if [ ! -r "$deployment_env_file" ]; then
        echo "deployment environment file is not readable: $deployment_env_file" >&2
        exit 2
    fi
    repository_source=$deployment_env_file
fi
fixed_repository=$(sed -n 's/^XIANYU_GHCR_REPOSITORY=//p' "$repository_source")
if ! printf '%s\n' "$fixed_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "rollback requires a fixed XIANYU_GHCR_REPOSITORY" >&2
    exit 2
fi
case "$source_image" in
    "$fixed_repository"@sha256:*) ;;
    *) echo "rollback image is outside XIANYU_GHCR_REPOSITORY" >&2; exit 2 ;;
esac

recovery_id="$(date -u +%Y%m%dT%H%M%SZ)-rollback-$$"
recovery_dir="$component_root/$recovery_id"
mkdir -p "$recovery_dir"
install -m 0644 "$source_compose" "$recovery_dir/compose.production.yml"

python3 - "$source_env" "$recovery_dir/production.env" "$source_image" "$fixed_repository" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
updates = {
    "XIANYU_IMAGE": sys.argv[3],
    "XIANYU_GHCR_REPOSITORY": sys.argv[4],
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
destination.write_text("\n".join(output) + "\n", encoding="utf-8")
destination.chmod(0o600)
PY

printf 'XIANYU_IMAGE=%s\nRELEASE_TARGET=%s\nXIANYU_RELEASE_ID=%s\nXIANYU_ASSET_REVISION=%s\n' \
    "$source_image" "$release_target" "$recovery_id" "$source_asset_revision" \
    > "$recovery_dir/release.env"
if [ -r "$source_release/previous-current" ]; then
    install -m 0600 "$source_release/previous-current" "$recovery_dir/previous-current"
fi

env_file="$recovery_dir/production.env"
compose_file="$recovery_dir/compose.production.yml"
XIANYU_IMAGE=$source_image
XIANYU_RELEASE_ID=$recovery_id
XIANYU_ASSET_REVISION=$source_asset_revision
XIANYU_REMOTE_VERIFICATION_ENABLED=false
XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=
export XIANYU_IMAGE XIANYU_RELEASE_ID XIANYU_ASSET_REVISION
export XIANYU_REMOTE_VERIFICATION_ENABLED XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN

ln -sfn "$recovery_dir" "$component_root/current"
ln -sfn "$recovery_dir" "$release_root/current"

if [ -n "$deployment_env_file" ]; then
    python3 - "$env_file" "$deployment_env_file" <<'PY'
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
metadata = destination.stat()
descriptor, temporary_name = tempfile.mkstemp(
    prefix=f".{destination.name}.rollback.",
    dir=destination.parent,
)
try:
    with os.fdopen(descriptor, "wb") as temporary:
        temporary.write(source.read_bytes())
        temporary.flush()
        os.fsync(temporary.fileno())
        os.fchmod(temporary.fileno(), stat.S_IMODE(metadata.st_mode))
        if os.geteuid() == 0:
            os.fchown(temporary.fileno(), metadata.st_uid, metadata.st_gid)
    os.replace(temporary_name, destination)
    directory = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
except BaseException:
    Path(temporary_name).unlink(missing_ok=True)
    raise
PY
fi

echo "committed sanitized rollback desired state $recovery_id before service reconciliation" >&2

case "$release_target" in
    control)
        docker compose --env-file "$env_file" -f "$compose_file" up -d --no-deps --wait --wait-timeout 180 xianyu-control
        ;;
    connector)
        docker compose --env-file "$env_file" -f "$compose_file" up -d --no-deps --wait --wait-timeout 180 xianyu-connector
        ;;
    all)
        docker compose --env-file "$env_file" -f "$compose_file" up -d --no-deps --wait --wait-timeout 180 xianyu-connector
        docker compose --env-file "$env_file" -f "$compose_file" up -d --no-deps --wait --wait-timeout 180 xianyu-control
        ;;
esac

if [ "$release_target" = "control" ] || [ "$release_target" = "all" ]; then
    python3 - "$XIANYU_RELEASE_ID" "$XIANYU_ASSET_REVISION" "${XIANYU_CONTROL_PORT:-9000}" <<'PY'
import json
import sys
import urllib.request

release_id, asset_revision, port = sys.argv[1:]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/release", timeout=10) as response:
    payload = json.load(response)
expected = {"release_id": release_id, "asset_revision": asset_revision}
if payload != expected:
    raise SystemExit(f"rollback release identity mismatch: expected {expected!r}, got {payload!r}")
PY
fi

echo "$action $release_target as sanitized release $recovery_id from $(basename "$source_release")"
