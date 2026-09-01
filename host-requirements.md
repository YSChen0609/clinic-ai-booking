# Host machine requirements

Runbook for Docker + Ollama on this machine. The **app** never uses the GPU; only the **Ollama** service does (or a host Ollama you point at).

Images travel; **drivers and Docker GPU plumbing do not**. Base Compose = CPU. GPU = add `compose.gpu.yml`. There is **no automatic** “try GPU, else CPU” — you choose which command to run.

Sources: [Compose GPU](https://docs.docker.com/compose/how-tos/gpu-support/), [Docker Desktop GPU (Windows/WSL2)](https://docs.docker.com/desktop/features/gpu/), [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/index.html), [CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).

---

## Quick pick: CPU or GPU

| Mode | When | Compose files | Speed (7B chat) |
|------|------|---------------|-----------------|
| **CPU** | Any Docker host; no NVIDIA; GPU overlay fails | `compose.yml` only | Usable but often slow (tool loops stack latency) |
| **GPU** | NVIDIA host ready (checklist below) | `compose.yml` + `compose.gpu.yml` | Much snappier if the model fits in VRAM |

Default model: `qwen2.5:7b` (`OLLAMA_MODEL` in `.env`). Site: http://localhost:8000

---

## Use CPU

Works on any machine that can run Docker Compose.

```bash
cd clinic-ai-booking
cp .env.example .env    # once

docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:7b
docker compose exec ollama ollama list
```

Confirm inference path (after a chat or a short `ollama run`):

```bash
docker compose exec ollama ollama ps
```

Expect the model listed; processor will be **CPU** (or empty until something loads the model).

Stop:

```bash
docker compose down
```

---

## Use NVIDIA GPU

### 1. Host checklist (do once per machine)

| Need | Windows (Docker Desktop + WSL2) | Linux (Docker Engine) |
|------|----------------------------------|------------------------|
| NVIDIA GPU + enough VRAM | Yes (~4–6+ GiB typical for `qwen2.5:7b`; 4 GiB cards may spill to RAM) | Same |
| NVIDIA **driver** | On **Windows** only — do **not** install a Linux NVIDIA driver inside WSL | On the host |
| WSL2 + `wsl --update` | Required | N/A |
| Docker Desktop **WSL2 backend** | Required | Use Docker Engine instead |
| NVIDIA Container Toolkit | Usually **not** separate with Desktop | **Required** (`nvidia-ctk runtime configure --runtime=docker`, restart Docker) |
| Host CUDA Toolkit | Optional for *running* Ollama in Docker | Optional |

Smoke (host + Docker can see the GPU):

```bash
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

If either fails, fix the host first — or stay on **Use CPU**.

### 2. Start (or switch) with GPU

From a stopped stack, or when switching from CPU (recreates `ollama`; keeps the `ollama_data` volume / pulled models):

```bash
cd clinic-ai-booking
cp .env.example .env    # once, if needed

docker compose -f compose.yml -f compose.gpu.yml up --build -d
# already running on CPU? recreate only Ollama:
# docker compose -f compose.yml -f compose.gpu.yml up -d --force-recreate ollama

docker compose -f compose.yml -f compose.gpu.yml exec ollama ollama pull qwen2.5:7b
```

Use the **same `-f` flags** for later `exec` / `logs` / `ps` while on GPU.

### 3. Confirm GPU

```bash
docker compose -f compose.yml -f compose.gpu.yml exec ollama nvidia-smi
docker compose -f compose.yml -f compose.gpu.yml exec ollama ollama run qwen2.5:7b "Say hi in one short sentence."
docker compose -f compose.yml -f compose.gpu.yml exec ollama ollama ps
```

Expect `nvidia-smi` to work **inside** the `ollama` container, and `ollama ps` to show **GPU** once the model is loaded.

If Compose errors on device/driver: host checklist incomplete → fall back to **Use CPU** (no `compose.gpu.yml`).

### 4. Switch back to CPU

```bash
docker compose up -d --force-recreate ollama
# or full stack without the GPU file:
# docker compose up -d
```

---

## Shared prerequisites (CPU and GPU)

| Need | Why |
|------|-----|
| Docker Engine or Docker Desktop (Compose v2) | Runs `app`, `db`, `ollama` |
| Network (first run) | Pull images + model weights |
| Disk | Multi‑GB model; volume `ollama_data` keeps pulls across recreate |
| Free ports (or remap in `.env`) | App `8000`, Postgres `5432`, Ollama `11434` |

Tight VRAM: set a smaller `OLLAMA_MODEL` in `.env` and pull that tag instead.

---

## Optional: host Ollama (skip container GPU)

Native Ollama uses the host driver (no toolkit). Point the **app** at it:

1. Run Ollama on the host; `ollama pull qwen2.5:7b`.
2. In Compose for `app`, set `OLLAMA_BASE_URL` (e.g. `http://host.docker.internal:11434` on Docker Desktop).
3. You can leave Compose `ollama` unused or stop that service.

Env vars `OLLAMA_BASE_URL` / `OLLAMA_MODEL` are for when you re-wire an LLM adapter (chat agent currently unwired).

---

## What ships vs what stays on the host

| Ships (repo / images) | Each host must provide |
|-----------------------|-------------------------|
| App `Dockerfile`, pinned `ollama/ollama` (CUDA userspace in image) | NVIDIA driver (for GPU mode) |
| `compose.yml` (CPU) + `compose.gpu.yml` (GPU reservation) | Toolkit or Desktop WSL2 GPU |
| Model after `ollama pull` (named volume) | GPU + VRAM (for GPU mode) |

We do **not**: bake a custom CUDA Ollama image; ship the NVIDIA Container Toolkit inside an image; hard-require GPU in base Compose (that would break CPU-only hosts).
