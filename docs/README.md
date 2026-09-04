# CaptN-BRAIN

> *When one AI has an idea, another criticizes it, a third repairs it, a fourth mutates it, and a fifth archives it for posterity.*

**CaptN-BRAIN** is a **deterministic-first multi-agent runtime** for symbolic research, code generation, data ingestion, quality analysis, and multi-perspective reasoning.

The core design principle: **tools and deterministic generators run first, validators gate deterministically, and the LLM is only a last-resort fallback.**

This repository bundles four cooperating subsystems:

| Subsystem | Path | Purpose |
|-----------|------|---------|
| **CaptN** | `captn/` | Deterministic agent runtime: orchestrator, workers, thinkers, bus, plugins |
| **MIA** | `mia/` | Multi-Agent Intelligence Alchemy: symbolic equation debate & evolution |
| **Alchimie** | `alchimie/` | Transformation rule library (archive, backup, schema) |
| `wd-40/` | Watchdog/shield safety layer (daemon, quarantine, sandbox, netcheck, filecheck, tray) |

Plus standalone tools in `tools/`, deployment configs in `deploy/`, and the test suite in `tests/`.

---

## Philosophy

Most AI agent systems call an LLM for **every** step — routing, validation, planning, generation, summarization. Each call burns tokens, costs money, adds latency, and introduces non-determinism.

CaptN-BRAIN inverts this: **solve everything you can with deterministic code first.** Only when deterministic tools cannot produce an answer does the system fall back to an LLM — and even then, it prefers a local model (Ollama) over a cloud API.

This gives you:

- **Deterministic outputs** — same input always produces the same output
- **Zero token consumption** for most operations
- **Auditable decisions** — routing and validation are explicit rules, not model "opinions"
- **Privacy** — most processing stays on your machine
- **Lower latency** — deterministic operations return in milliseconds, not seconds

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env                    # edit if needed (optional)
python -m mia.launcher_ollama          # launch the MIA orchestrator
python -m captn.dashboard.app          # launch the Captn dashboard (optional)
```

LLM backend defaults to **Ollama** at `localhost:11434` with **qwen2:1.5b**. No API key required. The system works deterministically even without an LLM.

---

## License

See `LICENSE` and `CITATION.cff`.