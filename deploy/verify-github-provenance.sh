#!/bin/sh
set -eu

if [ "$#" -ne 3 ]; then
    echo "usage: verify-github-provenance.sh <ghcr-image@sha256:digest> <ghcr.io/owner/repository> <Owner/repository>" >&2
    exit 2
fi

image=$1
expected_repository=$2
github_repository=$3
if ! printf '%s\n' "$expected_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "expected repository must be a lowercase GHCR repository" >&2
    exit 2
fi
case "$image" in
    "$expected_repository"@sha256:*) ;;
    *) echo "provenance subject is outside the allowed GHCR repository" >&2; exit 2 ;;
esac
if ! printf '%s\n' "$github_repository" \
    | grep -Eq '^[A-Za-z0-9]+(-[A-Za-z0-9]+)*/[A-Za-z0-9]+([._-][A-Za-z0-9]+)*$'; then
    echo "GitHub repository must be a canonical Owner/repository value" >&2
    exit 2
fi
github_repository_lower=$(printf '%s\n' "$github_repository" | tr '[:upper:]' '[:lower:]')
if [ "$github_repository_lower" != "${expected_repository#ghcr.io/}" ]; then
    echo "GitHub repository does not correspond to the allowed GHCR repository" >&2
    exit 2
fi

gh=/usr/bin/gh
if [ ! -x "$gh" ]; then
    echo "GitHub CLI is required at $gh" >&2
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

cert_identity="https://github.com/$github_repository/.github/workflows/docker-image.yml@refs/heads/main"
/usr/bin/env -i \
    PATH=/usr/bin:/bin \
    HOME=/root \
    GH_TOKEN=unused-for-oci-bundle \
    "$gh" attestation verify "oci://$image" \
    --repo "$github_repository" \
    --cert-identity "$cert_identity" \
    --cert-oidc-issuer https://token.actions.githubusercontent.com \
    --source-ref refs/heads/main \
    --deny-self-hosted-runners \
    --bundle-from-oci \
    >/dev/null

echo "verified GitHub build provenance for $image"
