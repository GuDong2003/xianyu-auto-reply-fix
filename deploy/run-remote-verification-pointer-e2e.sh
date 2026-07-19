#!/bin/sh
set -eu

if [ "$#" -gt 0 ] && [ -n "${XIANYU_POINTER_E2E_CHROMIUM:-}" ]; then
    if [ ! -x "$XIANYU_POINTER_E2E_CHROMIUM" ]; then
        echo "configured E2E Chromium is not executable: $XIANYU_POINTER_E2E_CHROMIUM" >&2
        exit 2
    fi
    if [ -z "${XIANYU_POINTER_E2E_HOST_RESOLVER_RULES:-}" ]; then
        echo "E2E Chromium host resolver rules are missing" >&2
        exit 2
    fi
    exec "$XIANYU_POINTER_E2E_CHROMIUM" \
        --ignore-certificate-errors \
        --allow-insecure-localhost \
        "--host-resolver-rules=$XIANYU_POINTER_E2E_HOST_RESOLVER_RULES" \
        "$@"
fi

if [ "$(uname -m)" != "x86_64" ]; then
    echo "remote verification pointer E2E must run in a linux/amd64 image" >&2
    exit 2
fi

for command in python Xvfb xvfb-run x11vnc; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "required E2E command is missing: $command" >&2
        exit 2
    fi
done

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH='' cd -- "$script_dir/.." && pwd)
export PYTHONPATH="$project_root${PYTHONPATH:+:$PYTHONPATH}"
exec python "$script_dir/remote_verification_pointer_e2e.py"
