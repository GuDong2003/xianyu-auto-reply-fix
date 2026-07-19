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
and `compose.production.yml` together in a root-owned deployment directory. The promotion wrapper
expects root's Docker client to be authenticated to GHCR with a dedicated package-read credential;
do not store that credential in `production.env`. The wrapper accepts only lowercase
`ghcr.io/...@sha256:<64 hex>` references, pulls and verifies the linux/amd64 image, atomically updates
the root-owned environment file, and forces remote verification off before release:

```sh
# Run once as root with a dedicated machine-account token scoped to read:packages.
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u xianyu-deploy --password-stdin
chmod 0600 /root/.docker/config.json
install -d -o root -g root -m 0750 /opt/xianyu-production/staging
install -o root -g root -m 0750 \
  deploy/promote-ghcr-release.sh deploy/release.sh deploy/rollback.sh \
  /opt/xianyu-production/staging/
install -o root -g root -m 0644 deploy/compose.production.yml \
  /opt/xianyu-production/staging/compose.production.yml

promote-ghcr-release.sh <ghcr-image@sha256:digest> <control|connector|all>
ENV_FILE=/opt/xianyu-production/production.env \
  /opt/xianyu-production/staging/promote-ghcr-release.sh \
  ghcr.io/owner/repository@sha256:REPLACE_WITH_64_HEX_IMAGE_DIGEST control
```

Automation must always pass the release target explicitly. Install the wrapper as `root:root` mode
`0750`, invoke it through a forced SSH command or narrow sudo rule, and do not add the deployment
user to the Docker group.

Normal control-plane releases do not recreate the connector or its account workers:

```sh
ENV_FILE=/opt/xianyu-production/production.env RELEASE_TARGET=control ./deploy/release.sh
```

Connector releases must be explicit:

```sh
ENV_FILE=/opt/xianyu-production/production.env RELEASE_TARGET=connector ./deploy/release.sh
```

Each release creates an SQLite online backup, runs migrations before switching the selected
component, waits for its health check, and records the immutable image, Compose file, and
non-secret production environment. Roll back the same component with:

```sh
RELEASE_TARGET=control ./deploy/rollback.sh
# or: RELEASE_TARGET=connector ./deploy/rollback.sh
```

Each successful release records the `current` snapshot that preceded it. Normal rollback follows
that recorded path and never guesses from timestamp-sorted directories, so an incomplete release
directory cannot become the rollback target. If promotion fails after a service has started but
before release completion, the wrapper restores `production.env` and prints an explicit
`RESTORE_CURRENT=true` command that redeploys the last successful `current` snapshot.

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
