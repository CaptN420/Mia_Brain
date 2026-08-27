# Running CaptN-BRAIN in Docker

Two isolation layers:

1. **Container (outer sandbox)** — non-root user, `no-new-privileges`, secrets
   mounted read-only, host LLM reached only through `host.docker.internal`.
2. **`wd-40/run.py` (inner sandbox)** — run *untrusted* scripts inside the
   container with writes / subprocess spawns / secret reads / network egress
   all gated by a CPython audit hook. See `wd-40/README.md`.

## Secrets

`.captn/` (API tokens) is **never copied into the image** (see `.dockerignore`).
It is bind-mounted read-only at runtime. If you `docker history` the image you
will NOT find your token in any layer.

## Build & run

```bash
docker compose up --build        # dashboard -> http://localhost:8501
docker compose config            # validate the compose file
```

## Run an untrusted script inside the container

```bash
# default: writes/spawns/secrets/net all blocked
docker compose run --rm captn python wd-40/run.py scripts/some_llm_thing.py

# crawler that needs the internet
docker compose run --rm captn python wd-40/run.py --net captn/workers/crawler.py

# max isolation: only shield + an explicit read folder visible
docker compose run --rm captn python wd-40/run.py --strict --allow /app/crawled scripts/probe.py
```

## LLM backends live on the host

Ollama (`localhost:11434`) and LM Studio (`localhost:1234`) run on your Windows
host, not in the container. `extra_hosts` maps `host.docker.internal` to the host
gateway. Point your provider `base_url` at
`http://host.docker.internal:11434` (set via `OLLAMA_HOST` or in
`agents/config.py`).

## Caveats

- The audit-hook sandbox is per-interpreter. A script that *spawns a child
  process* escapes the hook — which is why `run.py` blocks every spawn whose
  binary is outside the shield/temp. For truly malicious input, prefer a fresh
  `docker compose run --rm` (ephemeral container) over only the hook.
- Docker is **not installed** on the machine where these files were generated,
  so the image has not been built here. Run `docker compose up --build` on a
  host with Docker Desktop to verify.
