# Captn Roadmap

## Phase 1: Foundation (Completed)
- [x] Repository Structure & Documentation
- [x] Core Runtime (Message Bus, State Store, Rule Engine)
- [x] Initial Worker Suite (Extractor, Normalizer, Translator, Validator)
- [x] Self-Healing Loop (Tip Worker, Fix Generator)
- [x] Streamlit Dashboard (Nerve Center)

## Phase 2: Robustness & Persistence (Completed)
- [x] **Token Profiler & Context Attribution** — production Hermes Agent instrumentation at the `pre_api_request` hook; per-category token attribution with ProfileRecord (→ `docs/TOKEN_INPUT_OPTIMIZATION.md`)
- [x] **OpenRouter Token Gap Investigation** — reconciliation of local estimates vs provider `input_tokens`; discovery of `prompt_tokens = input_tokens + cache_read + cache_write`
- [x] **OpenRouter Actual Token Accounting** — field-level mapping from provider response to standardized categories; production reconciliation
- [x] **Context Budget** — 30K relevance-based selection across 5 categories (system, history, memory, tools, retrieved); 84 tests, 0 failures, feature flag OFF by default
- [x] **Production Validation & Observability** — budget telemetry integrated into ProfileRecord; JSONL export; aggregated budget_summary with p50/p95; 86 tests, 0 failures
- [x] **Real Traffic Rollout & Production Gate** — 22 scenarios, 22.2% token reduction, 100% quality, 0 runtime errors → **READY FOR LIMITED PRODUCTION**

## Phase 3: Token Optimization & Adaptive Management (Completed)
- [x] **Adaptive Context Manager** — deterministic complexity analysis (query, history depth, tool load, memory/retrieval density, context pressure); dynamic budget allocation; 22 tests, 0 failures
- [x] **Experimental Staging** — 12 scenario A/B: Adaptive beats Fixed by 4.0% (100% quality, p95 overhead 7.5ms); complexity model is monotonic (6/6 signals) with distinct allocation patterns (4/4)
- [x] **Production Calibration & Decision Gate** — 19-request calibration analysis; 2.7% reduction vs Fixed; perfect score↔budget correlation (1.0); decision: **CALIBRATE** (good but weights can be refined)
- [x] **Documentation** — comprehensive `docs/TOKEN_INPUT_OPTIMIZATION.md` covering all 4 layers (Profiler, Gap Analysis, Context Budget, Adaptive Manager) with architecture diagrams, field references, CLI commands, and Python API examples

## Phase 4: Accessibility & Integration (Next)
- [ ] Persistent State Store (SQLite/PostgreSQL integration)
- [ ] Advanced Rule Engine (Complex logical expressions)
- [ ] Rollback & Snapshot Versioning
- [ ] Project Context Loading (Folder mounting)
- [ ] Calibrated weight deployment for Adaptive Context Manager
- [ ] Production data gathering for ML-based complexity refinement candidate

## Phase 5: Scale & Production
- [ ] Event Bus (Asynchronous distributed messaging)
- [ ] Deterministic Replay (Record/Replay of agent actions)
- [ ] Worker Lifecycle Management (Dynamic spawning/scaling)
- [ ] Benchmarking Suite (Throughput, Latency, Accuracy)

## Phase 6: Agent & LLM Integration
- [ ] LLM Bridge (Native support for OpenAI, Anthropic, Local Models)
- [ ] Tool-Use Registry (Dynamic discovery of worker capabilities)
- [ ] Multi-Agent Orchestration (Hierarchical/Peer-to-Peer)
- [ ] Public API & SDK