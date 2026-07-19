#!/bin/sh
set -eu

umask 077
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV CDPATH ENV ENV_FILE RELEASE_ROOT COMPOSE_FILE XIANYU_IMAGE

if [ "$#" -ne 2 ]; then
    echo "usage: production-deploy-root.sh <ghcr-image@sha256:digest> <control|connector|all>" >&2
    exit 2
fi

image=$1
release_target=$2
if ! printf '%s\n' "$image" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
    echo "deployment image must be a lowercase immutable GHCR digest" >&2
    exit 2
fi
case "$release_target" in
    control|connector|all) ;;
    *) echo "invalid release target" >&2; exit 2 ;;
esac

staging_root=/opt/xianyu-production/staging
env_file=/opt/xianyu-production/production.env
release_root=/opt/xianyu/releases
promote="$staging_root/promote-ghcr-release.sh"
verify="$staging_root/verify-production-release.sh"
verify_provenance="$staging_root/verify-github-provenance.sh"
rollback="$staging_root/rollback.sh"
for executable in "$promote" "$verify" "$verify_provenance" "$rollback"; do
    if [ ! -x "$executable" ]; then
        echo "required deployment program is unavailable: $executable" >&2
        exit 2
    fi
done
if [ ! -r "$env_file" ]; then
    echo "production environment file is unavailable: $env_file" >&2
    exit 2
fi
expected_repository=$(sed -n 's/^XIANYU_GHCR_REPOSITORY=//p' "$env_file")
if ! printf '%s\n' "$expected_repository" \
    | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*$'; then
    echo "production environment must fix XIANYU_GHCR_REPOSITORY" >&2
    exit 2
fi
case "$image" in
    "$expected_repository"@sha256:*) ;;
    *) echo "image repository does not match XIANYU_GHCR_REPOSITORY" >&2; exit 2 ;;
esac

exec 9>/run/lock/xianyu-production-deploy.lock
if ! /usr/bin/flock -n 9; then
    echo "another production deployment is active" >&2
    exit 75
fi

clean_run() {
    /usr/bin/env -i \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        HOME=/root \
        ENV_FILE="$env_file" \
        RELEASE_ROOT="$release_root" \
        "$@"
}

current_release_image() {
    current="$release_root/$release_target/current"
    if [ ! -r "$current/release.env" ]; then
        echo "current $release_target release metadata is unavailable" >&2
        return 1
    fi
    recorded_image=$(sed -n 's/^XIANYU_IMAGE=//p' "$current/release.env")
    if ! printf '%s\n' "$recorded_image" \
        | grep -Eq '^ghcr\.io/([a-z0-9]+([._-][a-z0-9]+)*/)+[a-z0-9]+([._-][a-z0-9]+)*@sha256:[0-9a-f]{64}$'; then
        echo "current $release_target release has no valid immutable image" >&2
        return 1
    fi
    printf '%s\n' "$recorded_image"
}

verify_current_recovery() {
    recovery_image=$(current_release_image) || return 1
    clean_run "$verify" "$recovery_image" "$release_target"
}

current_recovery_is_committed() {
    current="$release_root/$release_target/current"
    if [ ! -r "$current/release.env" ] || [ ! -r "$current/production.env" ]; then
        return 1
    fi
    recovery_id=$(sed -n 's/^XIANYU_RELEASE_ID=//p' "$current/release.env")
    case "$recovery_id" in
        *-rollback-*) ;;
        *) return 1 ;;
    esac
    grep -Fqx 'XIANYU_REMOTE_VERIFICATION_ENABLED=false' "$current/production.env"
}

clean_reconcile_current() {
    /usr/bin/env -i \
        PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        HOME=/root \
        ENV_FILE="$env_file" \
        RELEASE_ROOT="$release_root" \
        RESTORE_CURRENT=true \
        "$rollback" "$release_target"
}

report_break_glass() {
    committed=${1:-false}
    desired_current=$(readlink -f "$release_root/$release_target/current" 2>/dev/null || true)
    desired_image=$(current_release_image 2>/dev/null || true)
    if [ "$committed" = "true" ]; then
        echo "recovery desired current is committed at ${desired_current:-unavailable}" >&2
        echo "recovery desired digest is ${desired_image:-unavailable}" >&2
    else
        echo "observed current after failed rollback is ${desired_current:-unavailable}" >&2
        echo "observed digest after failed rollback is ${desired_image:-unavailable}" >&2
    fi
    if [ -n "$desired_image" ]; then
        echo "break-glass through the locked root orchestrator:" >&2
        echo "/opt/xianyu-production/staging/production-deploy-root.sh $desired_image $release_target" >&2
    else
        echo "break-glass inspection is required before another deployment attempt" >&2
    fi
}

retry_committed_recovery_once() {
    recovery_context=$1
    if ! current_recovery_is_committed; then
        echo "$recovery_context has no committed sanitized recovery state; refusing an implicit retry" >&2
        report_break_glass false
        return 1
    fi
    echo "$recovery_context: retrying the committed current once under the existing deployment lock" >&2
    if clean_reconcile_current; then
        retry_status=0
    else
        retry_status=$?
        echo "$recovery_context: RESTORE_CURRENT reconciliation failed with status $retry_status" >&2
    fi
    if verify_current_recovery; then
        retry_verification_status=0
    else
        retry_verification_status=$?
        echo "$recovery_context: full postflight failed with status $retry_verification_status" >&2
    fi
    if [ "$retry_status" -eq 0 ] && [ "$retry_verification_status" -eq 0 ]; then
        echo "$recovery_context: reconciliation retry completed and recovery verified" >&2
        return 0
    fi
    echo "$recovery_context remains failed after the single reconciliation retry" >&2
    report_break_glass true
    return 1
}

clean_run "$verify_provenance" "$image" "$expected_repository"

if clean_run "$promote" "$image" "$release_target"; then
    :
else
    promotion_status=$?
    echo "promotion failed with status $promotion_status; verifying recovered current release" >&2
    if verify_current_recovery; then
        echo "promotion recovery verified for $release_target" >&2
    else
        recovery_verification_status=$?
        echo "promotion recovery verification failed with status $recovery_verification_status" >&2
        if retry_committed_recovery_once "promotion recovery"; then
            echo "promotion recovery verified after the single reconciliation retry" >&2
        else
            echo "promotion recovery requires break-glass intervention" >&2
        fi
    fi
    exit "$promotion_status"
fi

if clean_run "$verify" "$image" "$release_target"; then
    echo "XIANYU_DEPLOYMENT_VERIFIED $release_target $image"
    exit 0
else
    verification_status=$?
fi

echo "post-deployment verification failed; rolling back $release_target" >&2
if clean_run "$rollback" "$release_target"; then
    :
else
    rollback_status=$?
    echo "normal rollback reconciliation failed with status $rollback_status" >&2
    if retry_committed_recovery_once "postflight rollback"; then
        echo "postflight rollback recovery verified after the single reconciliation retry" >&2
    else
        echo "postflight rollback requires break-glass intervention" >&2
    fi
    exit "$verification_status"
fi

if verify_current_recovery; then
    echo "rollback completed and previous release passed postflight" >&2
else
    rollback_verification_status=$?
    echo "rollback postflight failed with status $rollback_verification_status" >&2
fi
exit "$verification_status"
