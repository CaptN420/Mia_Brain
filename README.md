# 🧠 Mia_Brain — CaptN-BRAIN

> *Quand une IA a une idée, une autre la critique, une troisième la répare, une quatrième la mute, et une cinquième l'archive pour la postérité.*

**Mia_Brain** is the codebase for **CaptN-BRAIN**, a deterministic multi-agent runtime built around the separation of an **Orchestrator (the Brain)** and **Workers (the Hands)**. It bundles two cooperating subsystems:

- **`mia/`** — *Multi-Agent Intelligence Alchemy*: a symbolic research framework where multiple agents generate, validate, repair, mutate and archive symbolic equations (generators, Hermes validators, Chymicus repairers, mutators, archivists, sentinels). Runs locally via Ollama for full privacy.
- **`captn/`** — the deterministic agent runtime: orchestrator, workers (autogen, crawler, translator, normalizer, deterministic coder, …), runtime validators, and the Streamlit dashboard.
- **`wd-40/`** — a watchdog/shield layer (sandbox, quarantine, tray, daemon) for local safety/integrity monitoring.
- **`alchimie/`** — the alchemy transformation library (rules, transformations, domains, archive, backups).
- **`tools/`, `scripts/`, `tests/`, `deploy/`** — CLI tools, helpers, test suite, and deployment configs (Docker / Caddy / tunnel).

The design principle (per `captn/agents/config.py`): **tools and deterministic generators run first, validators gate deterministically, and the LLM is only a fallback** (`cfg.llm_as_fallback`).

---

## 🚀 Quick start

```bash
pip install -r requirements.txt        # or mia/requirement.txt for the MIA subsystem
# Point the LLM backend at a local Ollama instance (default: localhost:11434/v1)
cp .env.example .env                   # fill in only what you need — .env is gitignored
python -m mia.launcher_ollama          # launch the MIA orchestrator
# or run the Captn dashboard:
python -m captn.dashboard.app
```

> **LLM backend:** defaults to Ollama at `localhost:11434/v1`. Only a small model (e.g. `qwen2:1.5b`) is required for the fallback path. LM Studio (`localhost:1234`) is also supported via `OPENAI_BASE_URL`.

---

## 🔒 Security & what is NOT in this repo

This is a **private** repository. By design, the following are **never committed** (see `.gitignore`):

- `.captn/` — runtime auth token, OpenAI provider config, ACL backups
- `.env` — all secrets (use `.env.example` as the template)
- `*.log`, `cloudflared.log`, `mia_evolution.log`, `runtime.log` — runtime logs
- `generated/`, `crawled/`, `_converted/`, `mirror/`, `dogfood-output/` — crawler/derived output
- `Code_base/` — vendored third-party tree
- `mia/session/` — large local session artifacts (debate logs, etc.)

Keep credentials out of the tree. Rotate any token that was ever committed and force-push a clean history.

---

## 📁 Layout

| Path | Purpose |
|------|---------|
| `mia/` | Multi-Agent Intelligence Alchemy subsystem (see `mia/README.txt`) |
| `captn/` | Deterministic agent runtime + Streamlit dashboard |
| `wd-40/` | Watchdog / shield safety layer (see `wd-40/README.md`) |
| `alchimie/` | Transformation rule library, archive & backups |
| `tools/` | `crawler_cli`, `raw2json_cli`, alchimie managers |
| `scripts/` | Maintenance scripts (secret scan, legacy quarantine) |
| `tests/` | pytest suite for the runtime & workers |
| `deploy/` | Dockerfile, docker-compose, Caddyfile, tunnel docs |
| `Feature/` | Feature notes & ideation log |

---

## 📜 License

See `LICENSE` and `CITATION.cff`.
