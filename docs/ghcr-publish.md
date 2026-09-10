# Publish app image to GHCR (`ghcr.io`)

Push only the **app** image built from the repo `Dockerfile`. Postgres and Ollama stay upstream images. Voicebox is still built from git in Compose (their published GHCR package is private).

Official reference: [Working with the Container registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## 1. Token + login (once)

1. GitHub → **Settings → Developer settings → Personal access tokens**
   - Classic: enable **`write:packages`** (and `read:packages`).
   - Fine-grained: Packages **write** on the target user/org.
2. Log in (username = GitHub user or org). Image paths must be **lowercase**:

```bash
echo YOUR_PAT | docker login ghcr.io -u YOUR_GITHUB_USERNAME --password-stdin
```

## 2. Build, tag, push

From the `clinic-ai-booking/` repo root. Replace `OWNER` with your GitHub user or org (lowercase).

```bash
docker build -t ghcr.io/OWNER/clinic-ai-booking:0.1.0 .
docker push ghcr.io/OWNER/clinic-ai-booking:0.1.0

# Optional floating tag
docker tag ghcr.io/OWNER/clinic-ai-booking:0.1.0 ghcr.io/OWNER/clinic-ai-booking:latest
docker push ghcr.io/OWNER/clinic-ai-booking:latest
```

The package appears under GitHub → profile/org → **Packages**. Set **public** or **private** in package settings. Private pulls still need `docker login`.

Tag `0.1.0` matches `APP_IMAGE_TAG` / Compose’s default app tag; bump both when you cut a release.

## 3. Use the image from Compose

Local Compose today builds `clinic-ai-booking:${APP_IMAGE_TAG:-0.1.0}`. To run from GHCR instead, point `image:` at the registry (and drop or comment `build:` if you only want pulls):

```yaml
# compose.yml — app service
image: ghcr.io/OWNER/clinic-ai-booking:${APP_IMAGE_TAG:-0.1.0}
# build:
#   context: .
```

```bash
docker compose pull app
docker compose up -d
```

You can keep `build:` for local rebuilds and still tag/`image:` as `ghcr.io/...` so a push after build stays consistent.

## 4. Optional: GitHub Actions later

On push/tag: checkout → log in to `ghcr.io` with `GITHUB_TOKEN` → build/push `ghcr.io/${{ github.repository }}:…`. The job needs:

```yaml
permissions:
  contents: read
  packages: write
```

Not wired in this repo yet (CI deferred). Add when you want automated publishes.

## Notes

- Do **not** bake `.env` into the image (Dockerfile / `.dockerignore` already keep secrets out). Runtime config stays Compose / env — see [secrets-runtime.md](secrets-runtime.md).
- Voicebox: separate image if you ever mirror it to your own GHCR; Compose currently builds from `https://github.com/agjs/voicebox.git#v0.2.16`.
- Storage/bandwidth: depends on GitHub plan and public vs private packages — check **Billing → Packages** if it matters.
