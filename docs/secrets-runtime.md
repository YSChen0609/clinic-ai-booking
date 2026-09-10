# Config and secrets (runtime injection)

The published app image must **not** contain `.env` or OAuth/DB secrets. This repo already follows that: the `Dockerfile` copies only packaging files + `src/`; `.dockerignore` excludes `.env`.

Official framing: [Twelve-Factor — Config](https://12factor.net/config), [Compose secrets](https://docs.docker.com/compose/how-tos/use-secrets/), [Kubernetes Secrets](https://kubernetes.io/docs/concepts/configuration/secret/).

## What each new user must prepare

**Yes — every deployer brings their own secrets.** Shipping an image does not ship credentials.

| You ship | Each user provides |
|----------|-------------------|
| Image, Compose, `.env.example`, docs | Their own `.env` (or secret files / cloud secrets): DB password, `SESSION_SECRET`, `GOOGLE_*` / `MS_*`, etc. |

Copy the template, fill real values, never commit them:

```bash
cp .env.example .env
# edit .env locally
docker compose up --build -d
```

Your Google refresh token is not their clinic’s; their mailbox is not yours. Runtime injection only answers **how** values reach the container — not **who** creates them.

## Runtime injection (image stays clean)

Secrets are attached when the container **starts**, not when the image is **built**.

### 1. Environment variables (this project’s default)

Compose reads the host `.env` and sets container `environment:` (see `compose.yml`). The app loads settings from `os.environ` (`config.py`).

```bash
# same idea without Compose
docker run --env-file .env ghcr.io/OWNER/clinic-ai-booking:0.1.0
```

**Pros:** Simple; matches Twelve-Factor; app already supports it.  
**Cons:** Values can appear in `docker inspect`, process env, and sometimes logs.

For local Compose and a single-host demo, this is the normal approach.

### 2. Docker / Compose secret files

Docker mounts a **file** into the container (often `/run/secrets/<name>`). The process reads the file instead of (or as a fallback for) an env var.

```yaml
# illustrative — not wired in this repo today
services:
  app:
    image: ghcr.io/OWNER/clinic-ai-booking:0.1.0
    secrets:
      - google_client_secret
    environment:
      GOOGLE_CLIENT_SECRET_FILE: /run/secrets/google_client_secret

secrets:
  google_client_secret:
    file: ./secrets/google_client_secret.txt   # host path; not in the image
```

Host file holds only the secret string. Many official images use a `FOO_FILE` convention: if set, read the file for `FOO`.

**Pros:** Less exposure via `docker inspect` env lists.  
**Cons:** Slightly more setup; this app would need a small “read `*_FILE` if set” helper before relying on it.

### 3. Kubernetes Secrets (later / cloud)

Same idea at cluster scale: create a Secret, then inject into the Pod as env (`secretKeyRef`) or as mounted files. Production often syncs from a vault (AWS/GCP/Azure secret managers) into Kubernetes Secrets.

Not used in this repo (no Kubernetes until asked).

## What not to do

- Do **not** `COPY .env` in the Dockerfile or commit `.env`.
- Do **not** bake secrets into `ENV` / `ARG` in the image (they persist in layers / history).
- Do **not** treat a public GHCR image as a place to store clinic credentials.

Publish steps: [ghcr-publish.md](ghcr-publish.md). OAuth values: [oauth-setup.md](oauth-setup.md).
