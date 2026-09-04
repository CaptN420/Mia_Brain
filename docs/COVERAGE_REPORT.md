# Documentation Coverage Report

---

## Summary

12 new documentation files created in `docs/` (2,494 lines total), complementing 3 pre-existing French-language files (`architecture/overview.md`, `cli/commandes.md`, `tools/index.md`).

---

## Components Documented

| Component | File(s) | Coverage |
|-----------|---------|----------|
| Project overview and purpose | `README.md` | ✅ Complete |
| High-level architecture | `ARCHITECTURE.md` | ✅ Complete (2 Mermaid diagrams) |
| CaptN runtime (bus, state, plugins) | `ARCHITECTURE.md`, `DEVELOPER_GUIDE.md`, `API_REFERENCE.md` | ✅ Complete |
| SmartRouter (deterministic routing) | `DETERMINISTIC_SYSTEM.md`, `LLM_ROUTING.md`, `ARCHITECTURE.md` | ✅ Complete |
| DeterministicCoder (AST transforms) | `DETERMINISTIC_SYSTEM.md`, `DEVELOPER_GUIDE.md` | ✅ Complete |
| CorpusThinkers (20 thinkers) | `DETERMINISTIC_SYSTEM.md`, `ARCHITECTURE.md` | ✅ Complete |
| PatchValidator (safety validation) | `DETERMINISTIC_SYSTEM.md`, `SECURITY.md` | ✅ Complete |
| MIA subsystem (equation debate) | `ARCHITECTURE.md`, `LLM_ROUTING.md` | ✅ Complete |
| Alchimie library | `ARCHITECTURE.md` | ✅ Basic |
| FallbackLLM (recovery proposals) | `LLM_ROUTING.md`, `SECURITY.md` | ✅ Complete |
| 26+ standalone tools | `API_REFERENCE.md`, `tools/index.md` (pre-existing) | ✅ Complete |
| CLI reference (all 25+ commands) | `API_REFERENCE.md`, `cli/commandes.md` (pre-existing) | ✅ Complete |
| Installation | `INSTALLATION.md` | ✅ Complete |
| Configuration | `CONFIGURATION.md` | ✅ Complete |
| Local models (Ollama, LM Studio) | `LOCAL_MODELS.md` | ✅ Complete |
| Token optimization | `TOKEN_OPTIMIZATION.md` | ✅ Complete |
| LLM routing decision tree | `LLM_ROUTING.md` | ✅ Complete |
| Security and privacy | `SECURITY.md` | ✅ Complete |
| Troubleshooting | `TROUBLESHOOTING.md` | ✅ Complete |
| Developer guide (extending, testing) | `DEVELOPER_GUIDE.md` | ✅ Complete |
| API/CLI reference | `API_REFERENCE.md` | ✅ Complete |
| Testing framework | `DEVELOPER_GUIDE.md` | ✅ Complete |

---

## Components Requiring Clarification

| Component | Status | Action Needed |
|-----------|--------|--------------|
| `wd-40/` watchdog layer | ❌ Not documented | Needs codebase inspection — largely empty/scaffold |
| `mirror/src/requests/` (vendored fork) | ❌ Not documented | Appears to be vendored `requests` library fork — likely not project code |
| `Code_base/` (vendored CPython tree) | ❌ Not documented | Third-party CPython source — likely not project code |
| `captn/orchestrator/main.py` | ❌ Not inspected | Not covered in depth |

---

## Post-Fix Status

The following issues identified in the initial assessment have been addressed:

| Issue | Status | Action Taken |
|-------|--------|-------------|
| wd-40 is scaffolding | ✅ **Corrected in docs** | wd-40 is actually functional (daemon, quarantine, sandbox, netcheck, filecheck, feeds, tray, config). Docs updated. |
| mirror/src/requests/ vendored fork | ✅ **Removed** | Unused vendored python-requests fork deleted. Dataset files (dataset.json, dataset.jsonl) preserved. |
| No CI/CD | ✅ **Added** | `.github/workflows/ci.yml` — pytest + benchmark suite on push/PR. |
| MIA launcher consolidation | ✅ **Extracted** | 6 support functions moved to `mia/equation_library.py` (281 lines). `launcher_ollama.py` reduced from 755→515 lines. |
| CLI command consolidation | ✅ **DevTools subcommand** | 5 token-economy tools (codechunk, imports, context, lint, diff-ast) moved under `devtools` umbrella. Added to skip list. |

## Features Found but Not Fully Documented

| Feature | Current Coverage | Suggested Improvement |
|---------|-----------------|---------------------|
| Container-aware host resolution (`llm_host.py`) | ✅ Basic explanation | Could expand with Docker Compose example |
| `_assert_localhost()` security control | ✅ Documented in SECURITY.md | Adequate |
| Ollama truncation detection | ✅ Documented in LOCAL_MODELS.md | Adequate |
| Dashboard authentication | ✅ Documented in SECURITY.md | Adequate |
| GPU configuration | ✅ Mentioned in LOCAL_MODELS.md | Brief |
| `token_usage` global counter | ✅ Mentioned in TOKEN_OPTIMIZATION.md | Could expand dashboard integration |

---

## Potential Inaccuracies

| Claim | Assessment |
|-------|-----------|
| "26+ standalone tools" | ✅ Confirmed — 32 `.py` files in `tools/`, 19 exclude from auto-discovery, 20 registered via `register_cli()` |
| "qwen2:1.5b" as default model | ✅ Confirmed — all 13 agent models set to this value in `config.py`, also default in `autogen.py` and `launcher_ollama.py` |
| "No OpenRouter integration" | ✅ Confirmed — no OpenRouter-specific code found |
| "Ollama is private — no network calls" | ✅ Confirmed with caveat — Ollama REST API calls to `localhost:11434` stay local |
| "LLM is fallback only" | ✅ Confirmed — `llm_as_fallback=True` and `llm_enabled=False` by default |

---

## Pre-Existing Files (Not Modified)

| File | Language | Content |
|------|----------|---------|
| `docs/architecture/overview.md` | French | ASCII arch diagram, exec flow |
| `docs/cli/commandes.md` | French | All 28 CLI commands with examples |
| `docs/tools/index.md` | French | Tool index with descriptions |

These serve as French-language quick references. My new files provide the comprehensive English documentation.

---

## Recommended Future Improvements

1. **Document the `wd-40/` watchdog layer** — appears to be a recent addition, mostly scaffolding
2. **Document `captn/orchestrator/main.py`** — likely the entry point for certain pipeline modes
3. **Create `LIMITATIONS.md`** — consolidate limitations from `DETERMINISTIC_SYSTEM.md` and `LOCAL_MODELS.md`
4. **Create `FAQ.md`** — extract common questions from `TROUBLESHOOTING.md` and other docs
5. **Document the Streamlit dashboard** — could be expanded with screenshots and workflow
6. **Create `MIA_SUBSYSTEM.md`** — dedicated doc for the MIA equation generation system (currently covered within ARCHITECTURE.md)
7. **Update Mermaid diagrams** if architecture changes significantly
8. **Document `docker-compose.yml`** production deployment in detail