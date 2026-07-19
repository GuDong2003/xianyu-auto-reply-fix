#!/bin/sh
set -eu

release_root=${RELEASE_ROOT:-/opt/xianyu/releases}
release_target=${RELEASE_TARGET:-control}
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
        previous="$current"
        action="restored current"
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
        previous=$(cd "$recorded_previous" && pwd -P)
        action="rolled back"
        ;;
    *) echo "RESTORE_CURRENT must be true or false" >&2; exit 2 ;;
esac

release_identity_available=false
if grep -q '^XIANYU_RELEASE_ID=' "$previous/release.env" \
    && grep -q '^XIANYU_ASSET_REVISION=' "$previous/release.env"; then
    release_identity_available=true
fi

set -a
# Release snapshots are created by deploy/release.sh.
# shellcheck disable=SC1091
. "$previous/production.env"
# shellcheck disable=SC1091
. "$previous/release.env"
set +a
XIANYU_RELEASE_ID=${XIANYU_RELEASE_ID:-$(basename "$previous")}
XIANYU_ASSET_REVISION=${XIANYU_ASSET_REVISION:-$XIANYU_RELEASE_ID}
export XIANYU_RELEASE_ID
export XIANYU_ASSET_REVISION
env_file="$previous/production.env"
compose_file="$previous/compose.production.yml"

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

if { [ "$release_target" = "control" ] || [ "$release_target" = "all" ]; } \
    && [ "$release_identity_available" = "true" ]; then
    python3 - "$XIANYU_RELEASE_ID" "$XIANYU_ASSET_REVISION" "${XIANYU_CONTROL_PORT:-9000}" <<'PY'
import json
import sys
import urllib.request

release_id, asset_revision, port = sys.argv[1:]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/release", timeout=10) as response:
    payload = json.load(response)
if payload != {"release_id": release_id, "asset_revision": asset_revision}:
    raise SystemExit(f"rollback release identity mismatch: {payload!r}")
PY
fi

ln -sfn "$previous" "$component_root/current"
ln -sfn "$previous" "$release_root/current"
echo "$action $release_target to $(basename "$previous")"
