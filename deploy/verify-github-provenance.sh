#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
    echo "usage: verify-github-provenance.sh <ghcr-image@sha256:digest> <ghcr.io/owner/repository>" >&2
    exit 2
fi

image=$1
expected_repository=$2
if ! printf '%s\n' "$expected_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "expected repository must be a lowercase GHCR repository" >&2
    exit 2
fi
case "$image" in
    "$expected_repository"@sha256:*) ;;
    *) echo "provenance subject is outside the allowed GHCR repository" >&2; exit 2 ;;
esac

gh=/usr/bin/gh
if [ ! -x "$gh" ]; then
    echo "GitHub CLI is required at $gh" >&2
    exit 2
fi
docker_config=/root/.docker/config.json
if [ ! -r "$docker_config" ]; then
    echo "root GHCR registry authentication is unavailable: $docker_config" >&2
    exit 2
fi
version=$("$gh" --version | awk 'NR == 1 { print $3 }')
python3 - "$version" <<'PY'
import sys

try:
    current = tuple(int(part) for part in sys.argv[1].split("."))
except ValueError as exc:
    raise SystemExit(f"invalid GitHub CLI version: {sys.argv[1]!r}") from exc
required = (2, 96, 0)
if current < required:
    raise SystemExit(f"GitHub CLI {required!r} or newer is required, got {current!r}")
PY

github_repository=${expected_repository#ghcr.io/}
/usr/bin/env -i \
    PATH=/usr/bin:/bin \
    HOME=/root \
    "$gh" attestation verify "oci://$image" \
    --repo "$github_repository" \
    --signer-workflow "$github_repository/.github/workflows/docker-image.yml" \
    --source-ref refs/heads/main \
    --deny-self-hosted-runners \
    --bundle-from-oci \
    >/dev/null

echo "verified GitHub build provenance for $image"
