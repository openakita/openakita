# Finance-Auto · Docker / Headless Deployment Guide

> **Audience**: operators running OpenAkita + finance-auto in a Linux
> container (`docker run`, `docker compose`, k8s, headless VPS).
> **Closes**: audit EX-P2-11 from `_finance_plugin_audit_extended_report.md`.
> **Last updated**: 2026-05-24

---

## §0 TL;DR — the one critical knob

Headless Linux containers do **not** ship a D-Bus session, which
means the [`keyring`](https://pypi.org/project/keyring/) backend used
by finance-auto to store the AES-256-GCM encryption seed is
unavailable. The plugin falls back to an environment variable:

```bash
export OPENAKITA_FINANCE_AUTO_PASSPHRASE="$(openssl rand -hex 32)"
```

**Without this env var, the plugin will start, but every encrypt /
decrypt path will degrade to plaintext and the audit log will record
the degraded state.** This is by design (so the container does not
crash-loop), but it is the single most important configuration step
for any container deployment.

Persist the passphrase in your secret manager (Vault, AWS Secrets
Manager, k8s Secret, docker compose `env_file`, …) — **the same
value must be present on every restart**, otherwise existing
ciphertext columns will no longer decrypt.

---

## §1 Quick start (single host, `docker run`)

```bash
# 1. Generate and stash the encryption seed in your secret manager.
PASSPHRASE="$(openssl rand -hex 32)"
echo "$PASSPHRASE" > /etc/openakita/finance-auto.passphrase
chmod 600 /etc/openakita/finance-auto.passphrase

# 2. Build the image (from the OpenAkita repo root).
#    The default Dockerfile installs OpenAkita core; finance-auto
#    plugin code ships under plugins/finance-auto/ but its runtime
#    deps require an extra install step (see §3).
docker build -t openakita:1.0.0-rc1 .

# 3. Run the container.
docker run -d \
  --name openakita \
  --restart=unless-stopped \
  -p 18900:18900 \
  -v "$PWD/data:/app/data" \
  -v "$PWD/plugins:/app/plugins:ro" \
  -e OPENAKITA_FINANCE_AUTO_PASSPHRASE="$PASSPHRASE" \
  openakita:1.0.0-rc1
```

| Mount | Purpose | Required? |
| --- | --- | --- |
| `-v ./data:/app/data` | Persistent SQLite + uploaded trial-balances + encrypted backups (`.tar.gz`) | **YES** — without it every container restart wipes the database |
| `-v ./plugins:/app/plugins:ro` | Plugin source tree (mounted read-only so the host venv doesn't try to write to it) | Recommended for now — until the official wheel ships with the plugin bundled, mounting from the host is the cleanest path |
| `-p 18900:18900` | OpenAkita HTTP API (FastAPI) | **YES** — the Setup Center desktop client connects here |

Verify the plugin loaded:

```bash
curl -sf http://127.0.0.1:18900/api/plugins/finance-auto/health
```

---

## §2 `docker-compose.yml` (recommended)

Append a `finance_auto` block to the repo-root `docker-compose.yml`
or create a standalone file:

```yaml
services:
  openakita:
    build: .
    container_name: openakita-finance-auto
    restart: unless-stopped
    ports:
      - "18900:18900"
    volumes:
      - ./data:/app/data
      - ./plugins:/app/plugins:ro
      - ./identity:/app/identity:ro
      - ./skills:/app/skills:ro
    env_file:
      - .env
      - .env.finance-auto   # KEEP THIS FILE OUT OF GIT (.gitignore)
    environment:
      - API_HOST=0.0.0.0
      - API_PORT=18900
      # OPENAKITA_FINANCE_AUTO_PASSPHRASE provided via .env.finance-auto
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:18900/api/plugins/finance-auto/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 20s
```

`.env.finance-auto` (1 line, chmod 600, **never commit**):

```env
OPENAKITA_FINANCE_AUTO_PASSPHRASE=<32-byte-hex>
```

Validate the compose file before bringing the stack up:

```bash
docker compose config
docker compose up -d
docker compose logs -f openakita
```

---

## §3 Installing finance-auto deps in the image

The repo-root `Dockerfile` installs OpenAkita core via
`pip install .` — but the finance-auto plugin's 6 runtime deps
(`openpyxl`, `xlrd==1.2.0`, `xltpl`, `keyring`, `cryptography`,
`pywin32` Windows-only) are declared in
`plugins/finance-auto/requirements.txt` and the
`[project.optional-dependencies].finance-auto` extra in the root
`pyproject.toml` — neither is installed by default.

**Two options**:

### Option A — Use the optional extra (preferred)

Patch the builder stage of your Dockerfile so the install line
becomes:

```dockerfile
RUN pip install --no-cache-dir ".[finance-auto]" \
    && pip install --no-cache-dir ./openakita-plugin-sdk
```

This brings in the same 6 packages as `requirements.txt`. The
`pywin32` marker (`sys_platform == "win32"`) means it is silently
skipped on Linux.

### Option B — Explicit requirements.txt install

If you prefer to keep the core install minimal and bolt finance-auto
on at runtime:

```dockerfile
RUN pip install --no-cache-dir . \
    && pip install --no-cache-dir ./openakita-plugin-sdk \
    && pip install --no-cache-dir -r plugins/finance-auto/requirements.txt
```

The trade-off is image rebuild cost (every requirements.txt change
invalidates a layer); for production we recommend Option A.

---

## §4 Troubleshooting

### Q1 — `KeyringUnavailable: no recommended backend was available`

**Cause**: D-Bus session not present (typical in containers, k8s,
WSL, and stripped Docker images).

**Fix**: Set `OPENAKITA_FINANCE_AUTO_PASSPHRASE` in the container
env (see §0). Verify with:

```bash
docker exec openakita env | grep OPENAKITA_FINANCE_AUTO_PASSPHRASE
```

### Q2 — `libssl.so.1.1: cannot open shared object file`

**Cause**: `cryptography` package built against OpenSSL 1.1 on a host
with OpenSSL 3.x.

**Fix**: Use the `python:3.11-slim` base (the repo-root Dockerfile
already does) and rebuild — the wheels ship with the OpenSSL
runtime baked in.

### Q3 — Persistent restart loop on first start

**Cause**: SQLite migrations need a writable `/app/data`. If the
host directory is owned by root and the container runs as a
non-root user, the v0→v11 migration cannot create the schema.

**Fix**:

```bash
mkdir -p ./data
chown -R 1000:1000 ./data   # or: chmod 777 ./data (lab only)
```

### Q4 — `pywin32` install fails when bind-mounting the plugin tree on Linux

**Cause**: A stale `pywin32` wheel was installed during a previous
Windows host run and is now incompatible with the Linux container.

**Fix**: `pywin32` is marked `sys_platform == "win32"` in both the
plugin manifest and `requirements.txt`, so a clean Linux install
will skip it. If you upgraded an existing image, rebuild without
cache:

```bash
docker build --no-cache -t openakita:1.0.0-rc1 .
```

### Q5 — Backups produced inside the container can't be restored on a different host

**Cause**: The encryption seed (in the env var) differs.

**Fix**: Ensure `OPENAKITA_FINANCE_AUTO_PASSPHRASE` is identical on
the source and target host. The backup `.tar.gz` is encrypted at
two layers: (a) `keys.bin` derived from the passphrase via PBKDF2,
and (b) the database file remains field-encrypted with the
component key. Restoring on a new host needs **both** the same
passphrase **and** the corresponding `key_meta` row inside the
backup.

---

## §5 Production checklist

- [ ] `OPENAKITA_FINANCE_AUTO_PASSPHRASE` set, ≥ 32 bytes high
      entropy, stored in your secret manager.
- [ ] `/app/data` mounted on a persistent volume.
- [ ] Container runs as non-root (UID 1000+) with `chown` applied
      to the data volume.
- [ ] `pip install ".[finance-auto]"` in your Dockerfile (or
      `requirements.txt` in entrypoint).
- [ ] Healthcheck pinned to `/api/plugins/finance-auto/health`,
      not just `/api/health`.
- [ ] Encrypted backups (`POST /api/plugins/finance-auto/backups`)
      scheduled via the OpenAkita scheduler or external cron.
- [ ] Backup destination volume is **separate** from the data
      volume (encrypted-at-rest store, off-host preferred).
- [ ] CHANGELOG `[Unreleased]` reviewed before each image tag
      promotion.

---

## §6 Cross-references

* `plugins/finance-auto/README.md` §3 Install + §7 Deployment modes
* `plugins/finance-auto/requirements.txt` — canonical dep list
* `pyproject.toml` `[project.optional-dependencies].finance-auto`
* `_finance_plugin_audit_extended_report.md` EX-P2-11 (deployment
  reasoning), EX-P1-5 (deps declaration), EX-P1-3 (KDF iteration
  count headroom for the passphrase entropy assumption above).
* `Dockerfile` (repo root) — base image stages and final runtime.
* `docker-compose.yml` (repo root) — single-service template this
  guide extends.
