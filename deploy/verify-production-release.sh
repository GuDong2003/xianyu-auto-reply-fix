#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: verify-production-release.sh <ghcr-image@sha256:digest> <control|connector|all>" >&2
    exit 2
fi

expected_image=$1
release_target=$2
if ! printf '%s\n' "$expected_image" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
    echo "expected image must be a lowercase immutable GHCR digest" >&2
    exit 2
fi
case "$release_target" in
    control|connector|all) ;;
    *) echo "invalid release target" >&2; exit 2 ;;
esac

env_file=${ENV_FILE:-/opt/xianyu-production/production.env}
if [ ! -r "$env_file" ]; then
    echo "production environment file is not readable: $env_file" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

expected_repository=${XIANYU_GHCR_REPOSITORY:-}
if ! printf '%s\n' "$expected_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "XIANYU_GHCR_REPOSITORY must fix the allowed lowercase GHCR repository" >&2
    exit 2
fi
case "$expected_image" in
    "$expected_repository"@sha256:*) ;;
    *) echo "expected image repository is not allowed" >&2; exit 2 ;;
esac
if [ "${XIANYU_IMAGE:-}" != "$expected_image" ]; then
    echo "production environment image does not match requested digest" >&2
    exit 2
fi
if [ "${XIANYU_REMOTE_VERIFICATION_ENABLED:-}" != "false" ]; then
    echo "remote verification must remain disabled" >&2
    exit 2
fi
if [ -n "${XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN:-}" ]; then
    echo "remote verification public origin must remain empty" >&2
    exit 2
fi

release_root=${RELEASE_ROOT:-/opt/xianyu/releases}
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

snapshot_env="$current/production.env"
release_env="$current/release.env"
compose_file="$current/compose.production.yml"
for required in "$snapshot_env" "$release_env" "$compose_file"; do
    if [ ! -r "$required" ]; then
        echo "release snapshot is incomplete: $required" >&2
        exit 2
    fi
done

recorded_image=$(sed -n 's/^XIANYU_IMAGE=//p' "$release_env")
release_id=$(sed -n 's/^XIANYU_RELEASE_ID=//p' "$release_env")
asset_revision=$(sed -n 's/^XIANYU_ASSET_REVISION=//p' "$release_env")
if [ "$recorded_image" != "$expected_image" ]; then
    echo "release snapshot image does not match requested digest" >&2
    exit 2
fi
if [ "$asset_revision" != "${expected_image##*@sha256:}" ] || [ -z "$release_id" ]; then
    echo "release identity does not match the immutable image digest" >&2
    exit 2
fi
XIANYU_RELEASE_ID=$release_id
XIANYU_ASSET_REVISION=$asset_revision
export XIANYU_RELEASE_ID XIANYU_ASSET_REVISION
if ! grep -Fqx 'XIANYU_REMOTE_VERIFICATION_ENABLED=false' "$snapshot_env"; then
    echo "release snapshot does not explicitly disable remote verification" >&2
    exit 2
fi
if grep -Eq '^XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN=.+$' "$snapshot_env"; then
    echo "release snapshot contains a remote verification public origin" >&2
    exit 2
fi

container_id_for() {
    docker compose --env-file "$snapshot_env" -f "$compose_file" ps -q "$1"
}

verify_container() {
    service=$1
    container_id=$(container_id_for "$service")
    if [ -z "$container_id" ]; then
        echo "$service container is unavailable" >&2
        exit 1
    fi
    running=$(docker inspect --format '{{.State.Running}}' "$container_id")
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")
    restart_count=$(docker inspect --format '{{.RestartCount}}' "$container_id")
    restart_policy=$(docker inspect --format '{{.HostConfig.RestartPolicy.Name}}' "$container_id")
    configured_image=$(docker inspect --format '{{.Config.Image}}' "$container_id")
    if [ "$running" != "true" ] || [ "$health" != "healthy" ]; then
        echo "$service is not running and healthy" >&2
        exit 1
    fi
    if [ "$restart_count" != "0" ] || [ "$restart_policy" != "unless-stopped" ]; then
        echo "$service restart contract failed: count=$restart_count policy=$restart_policy" >&2
        exit 1
    fi
    if [ "$configured_image" != "$expected_image" ]; then
        echo "$service is not running the requested image digest" >&2
        exit 1
    fi
    container_environment=$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$container_id")
    printf '%s\n' "$container_environment" | grep -Fqx "XIANYU_RELEASE_ID=$release_id"
    printf '%s\n' "$container_environment" | grep -Fqx "XIANYU_ASSET_REVISION=$asset_revision"
    printf '%s\n' "$container_environment" | grep -Fqx 'XIANYU_REMOTE_VERIFICATION_ENABLED=false'
    printf '%s\n' "$container_id"
}

verify_connector() {
    connector_id=$(verify_container xianyu-connector)
    published_port=$(docker port "$connector_id" 8091/tcp 2>/dev/null || true)
    if [ -n "$published_port" ]; then
        echo "connector port 8091 must not be published: $published_port" >&2
        exit 1
    fi
    docker exec -i "$connector_id" python - <<'PY'
import time
import urllib.error
import urllib.request

deadline = time.monotonic() + 180
while True:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8091/health/live", timeout=5):
            break
    except (OSError, urllib.error.HTTPError):
        if time.monotonic() >= deadline:
            raise
        time.sleep(3)
PY
}

verify_control() {
    control_id=$(verify_container xianyu-control)
    control_port=${XIANYU_CONTROL_PORT:-9000}
    published_port=$(docker port "$control_id" 8090/tcp)
    if [ "$published_port" != "127.0.0.1:$control_port" ]; then
        echo "control port binding mismatch: $published_port" >&2
        exit 1
    fi
    python3 - "$release_id" "$asset_revision" "$control_port" <<'PY'
import json
import sys
import time
import urllib.error
import urllib.request

release_id, asset_revision, port = sys.argv[1:]
deadline = time.monotonic() + 180
while True:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=5):
            break
    except (OSError, urllib.error.HTTPError):
        if time.monotonic() >= deadline:
            raise
        time.sleep(3)
with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/release", timeout=5) as response:
    payload = json.load(response)
expected = {"release_id": release_id, "asset_revision": asset_revision}
if payload != expected:
    raise SystemExit(f"release identity mismatch: expected {expected!r}, got {payload!r}")
PY
}

verify_control_to_connector() {
    control_id=$(container_id_for xianyu-control)
    if [ -z "$control_id" ]; then
        echo "control container is unavailable for connector path verification" >&2
        exit 1
    fi
    control_running=$(docker inspect --format '{{.State.Running}}' "$control_id")
    control_health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$control_id")
    if [ "$control_running" != "true" ] || [ "$control_health" != "healthy" ]; then
        echo "control container is not healthy for connector path verification" >&2
        exit 1
    fi
    docker exec -i "$control_id" python - <<'PY'
import json
import urllib.request
from pathlib import Path

token = Path("/run/secrets/connector_internal_token").read_text(encoding="utf-8").strip()
if not token:
    raise SystemExit("connector internal token is empty")
request = urllib.request.Request(
    "http://xianyu-connector:8091/internal/health",
    headers={"X-Connector-Token": token},
)
with urllib.request.urlopen(request, timeout=5) as response:
    payload = json.load(response)
if payload != {"status": "healthy"}:
    raise SystemExit(f"connector internal health mismatch: {payload!r}")
PY
}

case "$release_target" in
    control) verify_control ;;
    connector) verify_connector ;;
    all)
        verify_connector
        verify_control
        ;;
esac
verify_control_to_connector

echo "verified $release_target release $release_id at $expected_image"
