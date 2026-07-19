#!/bin/sh
set -eu

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
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

compose_file=${COMPOSE_FILE:-$script_dir/compose.production.yml}
legacy_container=${XIANYU_LEGACY_CONTAINER:-xianyu-auto-reply-fix}
export XIANYU_RELEASE_ID=initial-cutover-preflight
export XIANYU_ASSET_REVISION=initial-cutover-preflight
legacy_running=$(docker inspect --format '{{.State.Running}}' "$legacy_container" 2>/dev/null || true)
if [ "$legacy_running" != "true" ]; then
    echo "legacy connector is not running: $legacy_container" >&2
    exit 2
fi

docker compose --env-file "$env_file" -f "$compose_file" config -q
/usr/local/sbin/xianyu-egress-policy check

latest_handshake=$(wg show wg-hz latest-handshakes | awk 'NR == 1 {print $2}')
now=$(date +%s)
if [ -z "$latest_handshake" ] || [ "$latest_handshake" -eq 0 ] || [ $((now - latest_handshake)) -gt 180 ]; then
    echo "Hangzhou WireGuard peer has no recent handshake" >&2
    exit 2
fi

if [ "$XIANYU_EXPECTED_EGRESS_IP" = "104.223.77.152" ]; then
    echo "connector egress must not use the server public IP" >&2
    exit 2
fi

docker compose --env-file "$env_file" -f "$compose_file" run --rm --no-deps \
    --entrypoint python xianyu-connector -c \
    'import os; from xianyu_connector.egress_guard import verify_fixed_egress; verify_fixed_egress(os.environ["XIANYU_EXPECTED_EGRESS_IP"], "https://ifconfig.me/ip")'

cutover_complete=false
restore_legacy_on_failure() {
    status=$?
    trap - 0 1 2 15
    if [ "$cutover_complete" != "true" ]; then
        docker compose --env-file "$env_file" -f "$compose_file" stop \
            xianyu-control xianyu-connector >/dev/null 2>&1 || true
        docker start "$legacy_container" >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap restore_legacy_on_failure 0 1 2 15

docker stop --time 30 "$legacy_container"
docker run --rm --user 10001:10001 --read-only \
    --tmpfs /tmp:rw,noexec,nosuid,size=64m \
    --entrypoint python \
    -e XIANYU_LOG_DIR=/app/logs \
    -v "$XIANYU_LOGS_DIR:/app/logs" \
    "$XIANYU_IMAGE" -m xianyu_connector.ops sanitize-logs

ENV_FILE="$env_file" RELEASE_TARGET=all "$script_dir/release.sh"
cutover_complete=true
trap - 0 1 2 15
echo "initial cutover completed; legacy container remains stopped: $legacy_container"
