#!/bin/sh
set -eu

requested_release_target=${RELEASE_TARGET:-}
env_file=${ENV_FILE:-/opt/xianyu-production/production.env}
if [ ! -r "$env_file" ]; then
    echo "production environment file is not readable: $env_file" >&2
    exit 2
fi
set -a
# The root-owned deployment environment is selected at runtime.
# shellcheck disable=SC1090
. "$env_file"
set +a

case "${XIANYU_IMAGE:-}" in
    *@sha256:*) ;;
    *) echo "XIANYU_IMAGE must be an immutable image digest" >&2; exit 2 ;;
esac

compose_file=${COMPOSE_FILE:-deploy/compose.production.yml}
release_root=${RELEASE_ROOT:-/opt/xianyu/releases}
release_target=${requested_release_target:-${RELEASE_TARGET:-control}}
case "$release_target" in
    control|connector|all) ;;
    *) echo "RELEASE_TARGET must be control, connector, or all" >&2; exit 2 ;;
esac

docker run --rm --entrypoint python "$XIANYU_IMAGE" \
    -c "from xianyu_connector.api import create_connector_app"

legacy_container=${XIANYU_LEGACY_CONTAINER:-xianyu-auto-reply-fix}
if [ "$release_target" != "control" ]; then
    legacy_running=$(docker inspect --format '{{.State.Running}}' "$legacy_container" 2>/dev/null || true)
    if [ "$legacy_running" = "true" ]; then
        echo "legacy connector is still running: $legacy_container" >&2
        echo "use initial-cutover.sh for the first connector release" >&2
        exit 2
    fi
fi

release_id=$(date -u +%Y%m%dT%H%M%SZ)
asset_revision=${XIANYU_IMAGE##*@sha256:}
export XIANYU_RELEASE_ID="$release_id"
export XIANYU_ASSET_REVISION="$asset_revision"
component_root="$release_root/$release_target"
mkdir -p "$component_root"
component_root=$(cd "$component_root" && pwd -P)
previous_current=
if [ -L "$component_root/current" ]; then
    previous_current=$(readlink -f "$component_root/current")
    case "$previous_current" in
        "$component_root"/*) ;;
        *) echo "current release points outside $component_root" >&2; exit 2 ;;
    esac
fi
release_dir="$component_root/$release_id"
mkdir -p "$release_dir"
install -m 0644 "$compose_file" "$release_dir/compose.production.yml"
install -m 0600 "$env_file" "$release_dir/production.env"
if [ -n "$previous_current" ]; then
    printf '%s\n' "$previous_current" > "$release_dir/previous-current"
fi
printf 'XIANYU_IMAGE=%s\nRELEASE_TARGET=%s\nXIANYU_RELEASE_ID=%s\nXIANYU_ASSET_REVISION=%s\n' \
    "$XIANYU_IMAGE" "$release_target" "$release_id" "$asset_revision" \
    > "$release_dir/release.env"

docker compose --env-file "$env_file" -f "$compose_file" --profile ops run --rm xianyu-backup
docker compose --env-file "$env_file" -f "$compose_file" run --rm --no-deps xianyu-migrate

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
docker compose --env-file "$env_file" -f "$compose_file" ps

if [ "$release_target" = "control" ] || [ "$release_target" = "all" ]; then
    python3 - "$release_id" "$asset_revision" "${XIANYU_CONTROL_PORT:-9000}" <<'PY'
import json
import sys
import urllib.request

release_id, asset_revision, port = sys.argv[1:]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/release", timeout=10) as response:
    payload = json.load(response)
if payload != {"release_id": release_id, "asset_revision": asset_revision}:
    raise SystemExit(f"release identity mismatch: {payload!r}")
PY
fi

ln -sfn "$release_dir" "$component_root/current"
ln -sfn "$release_dir" "$release_root/current"
echo "released $release_target $release_id"
