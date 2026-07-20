# Production rollout

The production compose file deliberately refuses to start without an immutable image digest,
encrypted secret files, and the expected fixed egress IP.

Start from `deploy/production.env.example`. Keep the production copy outside the repository and
leave `XIANYU_EXPECTED_EGRESS_IP` empty until the compliant fixed egress is confirmed; Compose will
then fail closed instead of starting the connector accidentally.

Required host settings:

- `XIANYU_IMAGE`: registry reference ending in `@sha256:...`.
- `XIANYU_EXPECTED_EGRESS_IP`: the compliant fixed public IP used by the account in normal use.
- `XIANYU_CONFIG_FILE`: existing `global_config.yml` path.
- `XIANYU_DATA_DIR`: existing host directory containing `xianyu_data.db`.
- `XIANYU_PROFILES_DIR`: persistent per-account Chrome profile directory.
- `XIANYU_LOGS_DIR`: persistent application log directory.
- `XIANYU_BACKUPS_DIR`: persistent SQLite backup directory.
- `XIANYU_UPLOADS_DIR`: persistent uploads directory.
- `XIANYU_TRAJECTORY_DIR`: persistent legacy browser trajectory directory.
- `XIANYU_MASTER_KEY_FILE`: base64 encoded 32-byte AES-GCM key, mode `0640`, owner
  `root:10001`.
- `XIANYU_CONNECTOR_TOKEN_FILE`: random internal token of at least 32 characters, mode `0640`,
  owner `root:10001`.
- `XIANYU_LEGACY_SECRET_FILE`: the existing Fernet key from `data/.secret_encryption.key`.
- `XIANYU_ADMIN_PASSWORD_FILE`: new non-default administrator password, at least 16 characters.
- `XIANYU_JWT_SECRET_FILE`: random value of at least 32 characters.

Install `deploy/egress/xianyu-egress-policy`, its nftables rules, and
`deploy/systemd/xianyu-egress-policy.service` before the first cutover. The policy routes only
`172.31.203.2` through `wg-hz`; a blackhole route and nftables reject rule prevent fallback to the
server public interface. The connector uses `10.203.0.2` as its only DNS upstream.

Do not generate a new legacy secret when migrating an existing database. Losing that key makes the
stored Cookie and password fields unreadable.

Local-browser verification handoff is disabled by default. While
`XIANYU_LOCAL_VERIFICATION_HANDOFF_ENABLED=false`,
`XIANYU_LOCAL_VERIFICATION_PUBLIC_ORIGIN` may remain empty. Before enabling the handoff, set the
origin to the canonical external HTTPS origin used by operators, for example
`https://xy.kkrich.ltd` (scheme and host only, without a path, query, or fragment). An enabled
handoff with a missing or invalid public origin remains unavailable with HTTP 503.

Remote RFB verification is independently disabled by default with
`XIANYU_REMOTE_VERIFICATION_ENABLED=false`. Keep
`XIANYU_REMOTE_VERIFICATION_PUBLIC_ORIGIN` empty while disabled; when enabling the feature,
configure the canonical external HTTPS origin used by the control service. The connector keeps
the RFB bridge unavailable until the remote verification flag is enabled.

Every release exports one `XIANYU_RELEASE_ID` and `XIANYU_ASSET_REVISION` to the control and
connector containers. The control plane exposes the active values at `/api/release`, and operator
HTML is private/no-store while all application, remote viewer, and noVNC assets are served below
`/static/releases/<asset-revision>/`. The release and rollback scripts refuse to finish when the
runtime signal does not match the recorded release.

The production image includes a real remote-pointer acceptance test. Run it on linux/amd64 before
enabling remote verification:

```sh
docker run --rm --platform linux/amd64 --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --entrypoint /app/deploy/run-remote-verification-pointer-e2e.sh \
  xianyu-connector:ci
```

Before rollout, protect `https://xy.kkrich.ltd/` with Cloudflare Access or a fixed source-IP rule.
The compose file binds the control port to `127.0.0.1` and does not publish connector, VNC, or
noVNC ports.

Initial release in shadow mode:

```sh
install -m 0600 deploy/production.env.example /opt/xianyu-production/production.env
ENV_FILE=/opt/xianyu-production/production.env ./deploy/initial-cutover.sh
```

The initial cutover verifies the real public egress before touching the legacy container. It then
stops the legacy connector, sanitizes the formerly active log, and starts the new stack. If any
later step fails, it stops the new connector before restarting the legacy container. Normal
connector releases refuse to run while the legacy container is still active.

For normal production promotion, install `promote-ghcr-release.sh`, `release.sh`, `rollback.sh`,
`verify-production-release.sh`, both production deployment entrypoints, and `compose.production.yml`
together in a root-owned deployment directory. The production package and its attached OCI
attestation are public and are fetched anonymously; do not configure a GitHub PAT or root Docker
registry credential for this deployment path. The wrapper accepts only lowercase
`ghcr.io/...@sha256:<64 hex>` references, pulls and verifies the linux/amd64 image, atomically updates
the root-owned environment file, and forces remote verification off before release:

```sh
install -d -o root -g root -m 0750 /opt/xianyu-production/staging
install -o root -g root -m 0750 \
  deploy/promote-ghcr-release.sh deploy/release.sh deploy/rollback.sh \
  deploy/verify-production-release.sh deploy/verify-github-provenance.sh \
  deploy/production-deploy-root.sh deploy/bootstrap-ghcr-rollback-baseline.sh \
  /opt/xianyu-production/staging/
install -o root -g root -m 0755 deploy/production-deploy-entrypoint.sh \
  /usr/local/bin/xianyu-production-deploy
install -o root -g root -m 0644 deploy/compose.production.yml \
  /opt/xianyu-production/staging/compose.production.yml
```

Install GitHub CLI 2.96.0 or newer at the fixed `/usr/bin/gh` path. No GitHub PAT or Docker registry
credential is required. The provenance verifier supplies a fixed non-secret `GH_TOKEN` placeholder
only to satisfy the CLI's login precondition, then loads the public attestation bundle directly from
OCI. The root orchestrator fails before Docker promotion if the CLI, OCI attestation, exact workflow
certificate identity, GitHub Actions OIDC issuer, protected-main source ref, repository identity, or
GitHub-hosted runner provenance cannot be verified. Its effective policy is:

```sh
GH_TOKEN=unused-for-oci-bundle gh attestation verify "oci://$XIANYU_IMAGE" \
  --repo Owner/repository \
  --cert-identity \
    https://github.com/Owner/repository/.github/workflows/docker-image.yml@refs/heads/main \
  --cert-oidc-issuer https://token.actions.githubusercontent.com \
  --source-ref refs/heads/main \
  --deny-self-hosted-runners \
  --bundle-from-oci
```

Automation must always pass the release target explicitly. Create a dedicated locked-password
`xianyu-deploy` account used only by the restricted key, disable PTY/forwarding with `restrict`, and
do not add it to the Docker group. Restrict its SSH key to the checked-in parser so GitHub Actions
can send only `deploy <digest> <target>`:

```text
restrict,command="/usr/local/bin/xianyu-production-deploy" ssh-ed25519 AAAA... github-production
```

Grant passwordless sudo only to the root orchestrator. Do not grant `SETENV`; the orchestrator uses
`env -i`, fixed executable paths, the fixed `/opt/xianyu-production/production.env`, and a host-level
`flock` before it invokes the lower-level scripts:

```text
xianyu-deploy ALL=(root) NOPASSWD: /opt/xianyu-production/staging/production-deploy-root.sh *
```

Do not configure `AcceptEnv` for deployment variables. Even if the host has unrelated global
`AcceptEnv` settings, the forced parser ignores them and the root orchestrator clears its child
environment. Set both `XIANYU_GHCR_REPOSITORY=ghcr.io/owner/repository` and the case-preserving
`XIANYU_GITHUB_REPOSITORY=Owner/repository` in the root-owned production env. Their repository paths
must match case-insensitively; automation rejects a mismatch or every other GHCR repository before
Docker is invoked. The case-preserving value is required because the certificate SAN comparison is
exact and GitHub owner casing is significant there.

The first GHCR rollback compatibility baseline is a fixed trust tuple, not an arbitrary older image.
Configure `XIANYU_GHCR_ROLLBACK_BASELINE_IMAGE`, `XIANYU_GHCR_ROLLBACK_BASELINE_RUN_ID`, and
`XIANYU_GHCR_ROLLBACK_BASELINE_COMMIT` with the reviewed immutable digest, successful Actions run,
and full image revision commit. Pin the reviewed current Compose file separately with
`XIANYU_GHCR_ROLLBACK_BASELINE_COMPOSE_SHA256`. The bootstrap requires its image and run arguments
to match these values and verifies the pulled image's OCI source and revision labels before writing
a snapshot.

Configure the GitHub `production` Environment with required reviewers and these secrets:
`PRODUCTION_SSH_HOST`, `PRODUCTION_SSH_PORT`, `PRODUCTION_SSH_USER`,
`PRODUCTION_SSH_PRIVATE_KEY`, and a pinned `PRODUCTION_SSH_KNOWN_HOSTS` entry. The workflow never
uses `ssh-keyscan` and deploys only the digest produced by the gated publish job.
The repository cannot create or protect that Environment: deployment remains fail-closed until an
administrator creates it, adds required reviewers, and defines all five environment-scoped secrets.
The deploy job is code-gated to `refs/heads/main` with `github.ref_protected == true`; dispatches from
feature branches, including the implementation branch, can publish a candidate but cannot deploy.
All third-party Actions in the production publisher are pinned to full commit SHAs. The publisher
creates a GitHub build attestation and pushes the same provenance as an OCI attestation for the
immutable digest; the server verifies that OCI subject before promotion.

After the first successful legacy-to-GHCR `all` promotion, create a trusted GHCR rollback baseline
before any later production deployment. The current release still records the legacy snapshot as
its `previous-current`; that local-image snapshot cannot satisfy the immutable GHCR rollback policy.
Run the root-only bootstrap once with the trusted old GHCR digest and the GitHub Actions run that
produced it:

```sh
/opt/xianyu-production/staging/bootstrap-ghcr-rollback-baseline.sh \
  ghcr.io/wangjunkai-1996/xianyu-auto-reply-fix@sha256:1190e6a3195b63d108b2d4db0a09482b468b9687545220525d56c138d9254499 \
  all \
  29699661211
```

The bootstrap shares the production deployment lock and verifies the fixed trust tuple, requested
digest, `linux/amd64` platform, and image source/revision labels. It keeps the legacy snapshot only as
the environment and source-history input; the baseline copies the current trusted GHCR release's
Compose file so release identity variables exist. Before changing rollback metadata, it expands that
Compose configuration and requires both control and connector services to contain the new release id
and asset revision. The resulting snapshot records the original legacy path, source run, source
commit, and Compose hash, forces remote verification off, and atomically changes only the current
release's `previous-current` file. It does not switch either `current` symlink and does not create,
start, stop, or restart containers. Repeated execution with the same arguments validates and reuses
the same deterministic baseline; any conflicting or incomplete state fails closed.

Routine production changes must use the protected GitHub workflow and forced SSH entrypoint. Do not
invoke `promote-ghcr-release.sh`, `release.sh`, or `rollback.sh` directly: doing so bypasses the root
orchestrator's provenance gate, environment clearing, and deployment lock. The only supported root
console break-glass promotion still uses the same orchestrator:

```sh
/opt/xianyu-production/staging/production-deploy-root.sh \
  ghcr.io/owner/repository@sha256:REPLACE_WITH_64_HEX_IMAGE_DIGEST control
```

Choose `connector` or `all` explicitly when those components are intended; `control` remains the
default operational target in the protected workflow.

Each release creates an SQLite online backup, runs migrations before switching the selected
component, waits for its container health check, and records the immutable image, Compose file, and
non-secret production environment. The automated postflight additionally verifies `/health/live`,
loopback-only control port binding, no published connector port, zero container restarts,
`unless-stopped` restart policy, container health, image digest, release identity, and remote
verification remaining explicitly disabled. For every target (`control`, `connector`, or `all`) it
also checks the connector's `/health/live`, reads the connector token inside the control container,
and calls the connector's authenticated `/internal/health` endpoint over the Compose network. A
trusted older GHCR image that predates this endpoint can fall back on HTTP 404 only when the expected
image equals the fixed baseline digest and the release snapshot contains the exact configured image,
run, commit, and `XIANYU_INTERNAL_HEALTH_PROBE=legacy-qr-404` marker. The authenticated read-only
lookup for a deliberately missing QR session must then return 404; 401 or every other result fails
verification. This prevents a connector-only upgrade from bypassing the real control-to-connector
path or granting fallback behavior to an unreviewed image. It intentionally does not require
`/health/ready`,
because a healthy shadow, logged-out, or manual-verification deployment may have no ONLINE account.
Failed promotions and postflight checks invoke rollback automatically; manual lower-level rollback
is break-glass only.

Each successful release records the `current` snapshot that preceded it. Normal rollback follows
that recorded path and never guesses from timestamp-sorted directories, so an incomplete release
directory cannot become the rollback target. If promotion fails after a service has started but
before release completion, the wrapper restores `production.env` and automatically redeploys the
last successful `current` snapshot. If automated postflight fails after promotion, the forced SSH
entrypoint invokes normal rollback to the recorded previous release. The root orchestrator also
re-verifies the immutable digest and full postflight contract after either promotion recovery or
postflight rollback. Recovery success is reported only after that old `current` release passes;
the deployment still exits with the original promotion or postflight failure status. Rollback never
edits a historical snapshot: it creates a new sanitized recovery snapshot with a new release ID,
the recorded old digest, remote verification disabled, and matching production/current metadata.
Rollback commits that recovery snapshot as desired `current` and root environment state before
reconciling containers. If `docker compose up` or release identity verification fails midway, disk
state remains an explicit, sanitized, retryable old-digest target instead of claiming the failed-new
release while some containers have already switched back. If normal rollback returns nonzero after
committing that desired state, the same root orchestrator performs exactly one
`RESTORE_CURRENT=true` reconciliation retry under the existing `flock`, then runs the complete
postflight regardless of the retry exit status. A second failure prints the committed current path,
desired digest, and the exact locked root-orchestrator break-glass command; it never silently leaves
operators to infer which digest should be reconciled. Promotion failures use the same shared retry
path when the promotion wrapper has committed a sanitized recovery current but its first postflight
fails, preventing the promotion and postflight rollback branches from drifting apart.

Image rollback does not undo a database migration. Before any non-backward-compatible migration,
stop application writes and retain the exact pre-release SQLite backup. Recovery requires stopping
the new services, preserving the failed database for analysis, restoring that backup atomically,
running the restore/integrity check, and only then redeploying the previous image snapshot. Do not
run `rollback.sh` alone for a schema-incompatible release.

Install the units under `deploy/systemd/` for nightly backups and a monthly restore check. Daily
backups retain 14 copies; Sunday backups retain eight weekly copies.

Do not rescan an account until fixed egress, persistent profile storage, secret rotation, and
Cloudflare Access are all in place. After the first targeted scan, keep shadow mode enabled for 72
hours. Enable deterministic delivery first; low-risk automatic replies remain the final stage.
