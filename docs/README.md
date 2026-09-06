# CaptN-BRAIN Documentation

> Navigation guide for the CaptN-BRAIN documentation.

## Token Input Optimization

The token optimization stack comprises four layers built sequentially:

| # | Layer | File | Phase | Purpose |
|---|-------|------|-------|---------|
| 1 | **Token Profiler** | `captn/runtime/hermes_profiler.py` | 1.5 | Observation-only measurement of every context component |
| 2 | **Token Gap Analyzer** | `captn/runtime/token_gap.py` | 1.6 | Reconcile local estimates vs OpenRouter actual tokens |
| 3 | **Context Budget** | `captn/runtime/context_budget.py` | 2 | Relevance-based selection within fixed 30K token limits |
| 4 | **Adaptive Manager** | `captn/runtime/adaptive_manager.py` | 3 | Dynamic budget allocation by deterministic complexity analysis |

**Full reference:** [`TOKEN_INPUT_OPTIMIZATION.md`](TOKEN_INPUT_OPTIMIZATION.md) — architecture diagrams, CLI commands, Python API, field reference, and benchmark results.

## CLI Reference

All token optimization commands are under `devtools`:

```bash
python summon_agents.py devtools budget status|demo|telemetry
python summon_agents.py devtools adaptive status|demo
python summon_agents.py devtools hermes_profile records|csv|demo
python summon_agents.py devtools gap report
python summon_agents.py devtools reconcile records|report
```

## Test Suites

| Suite | Tests | File |
|-------|-------|------|
| Context Budget (base) | 15 | `tools/test_context_budget.py` |
| Context Budget (hardening) | 15 | `tools/test_context_budget_hardening.py` |
| Budget Telemetry | 11 | `tools/test_budget_telemetry.py` |
| Hermes Profile | 12 | `tools/test_hermes_profile.py` |
| Token Profiler | 19 | `tools/test_token_profiler.py` |
| Token Gap | 10 | `tools/test_token_gap.py` |
| Reconcile | 12 | `tools/test_reconcile.py` |
| Adaptive Manager | 22 | `tools/test_adaptive_manager.py` |
| **Total** | **106** | — |

## Quick Links

- [ROADMAP.md](../ROADMAP.md) — Project roadmap (Phase 1→6)
- [README.md](../README.md) — Top-level project overview
- [SECURITY.md](../SECURITY.md) — Secrets and token security
- [DOCKER.md](../DOCKER.md) — Docker deployment
- [CONTRIBUTING.md](../CONTRIBUTING.md) — Contribution guidelines