#!/bin/sh
set -eu

umask 077
set -f

original_command=${SSH_ORIGINAL_COMMAND:-}
old_ifs=$IFS
IFS=' '
# Intentional tokenization; glob expansion is disabled above and every token is validated below.
# shellcheck disable=SC2086
set -- $original_command
IFS=$old_ifs

if [ "$#" -ne 3 ] || [ "$1" != "deploy" ]; then
    echo "only 'deploy <ghcr-image@sha256:digest> <control|connector|all>' is allowed" >&2
    exit 2
fi

image=$2
release_target=$3
if ! printf '%s\n' "$image" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
    echo "deployment image must be a lowercase immutable GHCR digest" >&2
    exit 2
fi
case "$release_target" in
    control|connector|all) ;;
    *) echo "invalid release target" >&2; exit 2 ;;
esac

PATH=/usr/bin:/bin
export PATH
unset BASH_ENV CDPATH ENV ENV_FILE RELEASE_ROOT COMPOSE_FILE XIANYU_IMAGE
exec /usr/bin/sudo -n \
    /opt/xianyu-production/staging/production-deploy-root.sh \
    "$image" "$release_target"
