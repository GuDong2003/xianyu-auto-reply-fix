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
    if [ -z "${XIANYU_POINTER_E2E_BROWSER_STATE:-}" ]; then
        echo "E2E Chromium browser state root is missing" >&2
        exit 2
    fi
    browser_home="$XIANYU_POINTER_E2E_BROWSER_STATE/home"
    browser_config="$XIANYU_POINTER_E2E_BROWSER_STATE/config"
    browser_cache="$XIANYU_POINTER_E2E_BROWSER_STATE/cache"
    browser_data="$XIANYU_POINTER_E2E_BROWSER_STATE/data"
    browser_runtime="$XIANYU_POINTER_E2E_BROWSER_STATE/runtime"
    browser_crash="$XIANYU_POINTER_E2E_BROWSER_STATE/crash"
    umask 077
    mkdir -p \
        "$browser_home" \
        "$browser_config" \
        "$browser_cache" \
        "$browser_data" \
        "$browser_runtime" \
        "$browser_crash"
    chmod 700 \
        "$browser_home" \
        "$browser_config" \
        "$browser_cache" \
        "$browser_data" \
        "$browser_runtime" \
        "$browser_crash"
    export HOME="$browser_home"
    export XDG_CONFIG_HOME="$browser_config"
    export XDG_CACHE_HOME="$browser_cache"
    export XDG_DATA_HOME="$browser_data"
    export XDG_RUNTIME_DIR="$browser_runtime"
    export BREAKPAD_DUMP_LOCATION="$browser_crash"
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
