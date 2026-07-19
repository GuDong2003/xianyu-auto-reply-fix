#!/bin/sh
set -eu

umask 077

if [ "$#" -ne 2 ]; then
    echo "usage: promote-ghcr-release.sh <ghcr-image@sha256:digest> <control|connector|all>" >&2
    exit 2
fi

image=$1
release_target=$2
requested_image=$image
requested_release_target=$release_target
case "$release_target" in
    control|connector|all) ;;
    *) echo "release target must be control, connector, or all" >&2; exit 2 ;;
esac
if ! printf '%s\n' "$image" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
    echo "image must be a lowercase GHCR reference pinned to a sha256 digest" >&2
    exit 2
fi

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
env_file=${ENV_FILE:-/opt/xianyu-production/production.env}
if [ ! -r "$env_file" ]; then
    echo "production environment file is not readable: $env_file" >&2
    exit 2
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
image=$requested_image
release_target=$requested_release_target
expected_repository=${XIANYU_GHCR_REPOSITORY:-}
if ! printf '%s\n' "$expected_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "XIANYU_GHCR_REPOSITORY must fix the allowed lowercase GHCR repository" >&2
    exit 2
fi
case "$image" in
    "$expected_repository"@sha256:*) ;;
    *) echo "image repository does not match XIANYU_GHCR_REPOSITORY" >&2; exit 2 ;;
esac
release_root=${RELEASE_ROOT:-/opt/xianyu/releases}
component_root="$release_root/$release_target"
pre_release_current=
if [ -L "$component_root/current" ]; then
    pre_release_current=$(readlink -f "$component_root/current")
fi

docker pull --platform linux/amd64 "$image"
repo_digests=$(docker image inspect \
    --format '{{range .RepoDigests}}{{println .}}{{end}}' "$image")
if ! printf '%s\n' "$repo_digests" | grep -Fqx "$image"; then
    echo "pulled image RepoDigests do not contain the requested digest" >&2
    exit 2
fi
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")
if [ "$platform" != "linux/amd64" ]; then
    echo "pulled image platform must be linux/amd64, got $platform" >&2
    exit 2
fi

env_backup=$(mktemp "${env_file}.pre-release.XXXXXX")
cp -p "$env_file" "$env_backup"
promotion_complete=false
restore_environment_on_failure() {
    status=$?
    trap - 0 1 2 15
    if [ "$promotion_complete" != "true" ]; then
        mv -f "$env_backup" "$env_file"
        echo "promotion failed; restored $env_file" >&2
        if [ -n "$pre_release_current" ]; then
            echo "recovering the last successful $release_target release" >&2
            echo "RESTORE_CURRENT=true RELEASE_TARGET=$release_target RELEASE_ROOT=$release_root $script_dir/rollback.sh" >&2
            if ENV_FILE="$env_file" RESTORE_CURRENT=true RELEASE_TARGET="$release_target" \
                RELEASE_ROOT="$release_root" "$script_dir/rollback.sh"; then
                echo "automatic recovery completed" >&2
            else
                rollback_status=$?
                echo "automatic recovery failed with status $rollback_status" >&2
                echo "retry with:" >&2
                echo "RESTORE_CURRENT=true RELEASE_TARGET=$release_target RELEASE_ROOT=$release_root $script_dir/rollback.sh" >&2
            fi
        fi
    fi
    exit "$status"
}
trap restore_environment_on_failure 0 1 2 15

python3 - "$env_file" "$image" <<'PY'
from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
updates = {
    "XIANYU_IMAGE": sys.argv[2],
    "XIANYU_REMOTE_VERIFICATION_ENABLED": "false",
    "XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN": "",
}
metadata = path.stat()
lines = path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
output: list[str] = []
for line in lines:
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

descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
        temporary.write("\n".join(output) + "\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        os.fchmod(temporary.fileno(), stat.S_IMODE(metadata.st_mode))
        if os.geteuid() == 0:
            os.fchown(temporary.fileno(), metadata.st_uid, metadata.st_gid)
    os.replace(temporary_name, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
except BaseException:
    Path(temporary_name).unlink(missing_ok=True)
    raise
PY

ENV_FILE="$env_file" RELEASE_TARGET="$release_target" "$script_dir/release.sh"
promotion_complete=true
rm -f "$env_backup"
trap - 0 1 2 15
echo "promoted $release_target to $image"
