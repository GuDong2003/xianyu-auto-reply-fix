#!/bin/sh
set -eu

load_secret() {
    variable_name="$1"
    secret_path="$2"
    if [ ! -s "$secret_path" ]; then
        echo "required secret is missing: $secret_path" >&2
        exit 1
    fi
    value=$(tr -d '\r\n' < "$secret_path")
    export "$variable_name=$value"
}

role=${XIANYU_ROLE:-control}

if [ "$role" = "control" ] \
    && [ "${XIANYU_REMOTE_VERIFICATION_ENABLED:-false}" = "true" ]; then
    python - <<'PY'
import os
from urllib.parse import urlsplit

origin = os.environ.get("XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN", "").strip()
parsed = urlsplit(origin)
if (
    parsed.scheme.lower() != "https"
    or not parsed.hostname
    or parsed.path
    or parsed.query
    or parsed.fragment
    or parsed.username
    or parsed.password
):
    raise SystemExit("remote verification requires an HTTPS public origin")
PY
fi

if [ "${XIANYU_PRODUCTION:-false}" = "true" ] \
    && [ "${XIANYU_EXTERNAL_CONNECTOR:-false}" = "true" ] \
    && [ "${ENABLE_VNC:-false}" != "false" ]; then
    echo "external production mode forbids legacy VNC" >&2
    exit 2
fi

load_secret XIANYU_CONNECTOR_INTERNAL_TOKEN /run/secrets/connector_internal_token
load_secret SECRET_ENCRYPTION_KEY /run/secrets/legacy_secret_key

case "$role" in
    migrate)
        exec alembic upgrade head
        ;;
    connector)
        exec python -m xianyu_connector.main
        ;;
    control)
        load_secret ADMIN_PASSWORD /run/secrets/admin_password
        load_secret JWT_SECRET_KEY /run/secrets/jwt_secret
        exec python Start.py
        ;;
    backup)
        exec python -m xianyu_connector.ops backup
        ;;
    restore-check)
        exec python -m xianyu_connector.ops restore-check
        ;;
    *)
        echo "unsupported XIANYU_ROLE: $role" >&2
        exit 2
        ;;
esac
