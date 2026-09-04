# Token and Cost Optimization

This document explains how CaptN-BRAIN reduces LLM token consumption and API costs through its deterministic-first architecture.

---

## Confirmed Optimization Mechanisms

The following mechanisms are confirmed directly from the source code:

### 1. Deterministic Routing (SmartRouter)

**What it does:** Replaces LLM-based task routing with keyword matching + scoring.

**Source:** `captn/workers/orchestration/smart_router.py`

**Token savings per routing decision:** ~300-800 tokens saved vs. an equivalent LLM-based routing call.

**How it works:**
```python
# Instead of: "LLM, which worker should handle 'generate python code'?"
# Which costs ~300 tokens + ~100 response tokens

# Deterministic equivalent:
router = SmartRouter()
result = router.route("generate python code for fibonacci")
# Pure computation — 0 tokens
```

### 2. Deterministic Code Generation

**What it does:** AST-based code transformations without any LLM.

**Source:** `captn/workers/code_generation/deterministic_coder.py`

**Token savings per code transform:** ~500-2000+ tokens saved vs. an LLM-based code generation call.

**How it works:**
```python
dc = DeterministicCoder()
code, method = dc.generate("def add(a,b): return a+b", 
                            modes=["docstrings", "annotate", "normalize"])
# Pure AST operations — 0 tokens
```

### 3. Deterministic Validation

**What it does:** AST-based patch safety checking instead of LLM-based code review.

**Source:** `captn/runtime/validator.py`

**Token savings per validation:** ~200-500 tokens saved vs. LLM-based code review.

### 4. Deterministic Knowledge Retrieval

**What it does:** Regex-based corpus parsing instead of LLM-based retrieval.

**Source:** `captn/thinkers/base.py`

**Token savings per query:** ~200-1000 tokens saved vs. asking an LLM "what did [thinker] contribute?"

### 5. Medium of Exchange — LLM as Fallback Only

**What it does:** The LLM is called **only** when deterministic tools produce no result.

**Source:** `mia/config.py`, `captn/runtime/runtime.py`

```python
# From config.py
llm_as_fallback: bool = True   # LLM only as last resort
llm_enabled: bool = False      # FULL DETERMINISTIC MODE by default
```

### 6. Local-Only LLM (Ollama)

**What it does:** Even when an LLM is needed, it uses a local model via Ollama, eliminating API costs.

**Source:** `captn/runtime/llm_host.py`, `mia/ollama_client.py`

**Cost per token:** $0.00 (Ollama) vs. $0.0025-$0.015 per 1K tokens (cloud APIs).

### 7. Single-Model Policy

**What it does:** All 13 MIA agent models are pinned to the same small model (`qwen2:1.5b`).

**Source:** `mia/config.py`

This eliminates "model not found" errors and ensures consistent token consumption.

### 8. Strict Schema Validation on LLM Output

**What it does:** LLM output is validated against a strict JSON schema, truncated, and sanitized before entering the pipeline.

**Source:** `captn/runtime/llm_provider.py`

```python
# Failure context is truncated to 6000 chars
context_json = json.dumps(failure_context, indent=2, default=str)[:6000]

# Proposal must pass schema validation
def _validate_proposal(self, proposal):
    # ... field truncation + mandatory requires_validation=True
```

### 9. Bounded Token Generation

**What it does:** Each MIA agent has a bounded `num_predict` setting, preventing runaway generation.

| Agent | Max Tokens |
|-------|-----------|
| Debate | 150 |
| Validation | 80 |
| Archive | 200 |
| Sentinel | 120 |
| Support | 100 |
| Synthesis | 180 |
| Hermes | 200 |

### 10. Pipeline Completion Detection

**What it does:** The orchestrator detects pipeline completion and stops early, rather than always completing all steps.

**Source:** `captn/runtime/runtime.py` — `process_response()` checks `pending_count` and stops when all workers have reported.

---

## Measured Performance

The `tools/benchmark.py` suite measures:

- **Token economy** — % savings vs equivalent LLM approach
- **Correctness** — validation against ground truth
- **False positives** — tool reports success but answer is wrong

The benchmark covers 7 suites with 51 test cases:

| Suite | Description | Tools Tested |
|-------|-------------|--------------|
| `eqsolve` | Symbolic solver | `sympy` wrappers |
| `chemsym` | Chemistry | Formula parsing, balancing, molar mass |
| `equation` | Math/physics/chemistry | Equation tools |
| `nl2eq` | Natural language → equation | NL parser |
| `deadscout` | Dead code detection | Static analysis |
| `depgraph` | Dependency graph | Import parser |
| `healthcheck` | Project health scoring | Composite metrics |

**Run the benchmark:**
```bash
python -m tools.benchmark
python -m tools.benchmark --json
python -m tools.benchmark --suite eqsolve
```

---

## Potential Savings

Actual token and cost savings depend on workload, model selection, and configuration. Estimates:

| Scenario | Without CaptN-BRAIN | With CaptN-BRAIN | Savings |
|----------|-------------------|------------------|---------|
| Code transformation (100 files) | 50K-200K tokens via LLM | 0 tokens (deterministic) | **100%** |
| Task routing (1000 tasks) | 300K-800K tokens via LLM | 0 tokens (keyword match) | **100%** |
| Code review (100 patches) | 30K-100K tokens via LLM | 0 tokens (AST validation) | **100%** |
| Full project analysis | 100K-500K tokens via cloud | Local Ollama only | **~95%+** |
| MIA equation evolution (10 cycles) | 50K-200K tokens via cloud | Local Ollama only | **100% of API costs** |

**Key insight:** The majority of operations (routing, code transforms, validation, knowledge retrieval) use **zero tokens** regardless of scale. LLM usage is confined to the autogen loop and MIA debate proposals — both using local Ollama at zero API cost.

---

## Recommendations for Maximum Savings

1. **Set `llm_enabled=False`** (default) — disables all LLM calls in MIA
2. **Set `llm_as_fallback=True`** (default) — only uses LLM when deterministic processing fails
3. **Use Ollama** — zero API cost for LLM calls
4. **Use qwen2:1.5b** — smallest adequate model, lowest resource usage
5. **Bypass MIA debate loop** if only using CaptN runtime tools
6. **Run benchmarks** — `python -m tools.benchmark` to measure token economy for your workload