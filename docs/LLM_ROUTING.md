# LLM Routing and Model Selection

---

## Overview

CaptN-BRAIN uses a **deterministic-first routing strategy**. The LLM is not the default processing path — it is a carefully-guarded fallback used only when deterministic tools cannot produce a result.

This document explains the routing decision tree, the conditions under which an LLM is called, and how cloud providers are integrated.

---

## Decision Tree

```
Task Description Enters System
│
├── SmartRouter scores > 0?
│   ├── Yes → Dispatch to deterministic worker(s)
│   │         Zero tokens. Zero API calls. Millisecond latency.
│   │
│   └── No (or worker fails)
│
├── Is llm_enabled=True in config?
│   ├── Yes → Route to Ollama (local model)
│   │         Token consumption: local only, no API cost.
│   │         Model: qwen2:1.5b (or as configured in .env)
│   │
│   └── No → llm_as_fallback=True?
│
├── Is llm_as_fallback=True?
│   ├── Yes → Use FallbackLLM for recovery proposals only
│   │         Token consumption: minimal (recovery, not full generation)
│   │         Schema validation before output re-enters pipeline
│   │
│   └── No → Return error/graceless degradation
│
└── All paths exhausted → Graceful error response
```

---

## Routing Components

### 1. SmartRouter (deterministic)

**File:** `captn/workers/orchestration/smart_router.py`

The SmartRouter is the **primary routing mechanism** — it selects workers based on keyword overlap scoring without any LLM involvement:

```python
router = SmartRouter()
result = router.route("generate python code for fibonacci")
# Returns: {"selected": ["deterministic_coder"], "scores": {...}, ...}
```

It never calls an LLM. Routing is pure keyword + tag matching with fairness boost.

### 2. FallbackLLM (recovery only)

**File:** `captn/runtime/llm_provider.py`

The `FallbackLLM` is called **only** when a pipeline step fails:

- Triggered by `Captn.process_response()` when a worker reports `valid=False`
- Generates a structured recovery proposal via the configured LLM provider
- The proposal **must** pass `_validate_proposal()` — a strict JSON schema check
- Key schema requirement: `requires_validation` is **always** set to `True` (defense-in-depth)

```python
class FallbackLLM:
    _PROPOSAL_SCHEMA = {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "diagnosis": {"type": "string"},
            "new_hypothesis": {"type": "string"},
            "proposed_strategy": {"type": "string"},
            "expected_effect": {"type": "string"},
            "confidence": {"type": "number"},
            "requires_validation": {"type": "boolean"}
        },
        "required": ["diagnosis", "proposed_strategy", "confidence"]
    }

    def _validate_proposal(self, proposal):
        # ... schema validation + field truncation + safety hardening
```

**Safety:** Failure context is truncated to 6000 chars and fenced with "BEGIN FAILURE CONTEXT (untrusted data)" markers to prevent prompt injection.

### 3. Autogen Loop

**File:** `captn/workers/code_generation/autogen.py`

The autogen loop uses Ollama for code generation from crawled datasets. It operates under these constraints:

- **Host-pinned** — only localhost (validated by `_assert_localhost()`)
- **No API key** used or logged
- **Output validated** by `PatchValidator` before writing
- **Loop guard** — each iteration is bounded (samples per iteration, total iterations)

```python
OLLAMA_BASE_URL = _ollama_base_url()  # Resolved per-environment (host vs container)
OLLAMA_MODEL = "qwen2:1.5b"
```

### 4. MIA Debate Loop

**File:** `mia/launcher_ollama.py`

The MIA subsystem runs a full evolution cycle:

1. **Variable Discovery** — agents propose variables via Ollama
2. **Equation Synthesis** — agents propose equations via Ollama  
3. **Deterministic Validation** — equation structure, duplication checks (no LLM)
4. **Diversification** — structural mutation (no LLM)
5. **Safety Gate** — safety testing before archiving (no LLM)
6. **Archive** — add to alchimie library (no LLM)

LLM calls are confined to steps 1-2. Steps 3-6 are 100% deterministic.

---

## Fallback Mechanics

### Conditions That Trigger Fallback

| Condition | Action | Consumer |
|-----------|--------|----------|
| SmartRouter returns score=0 for all workers | Pipeline uses generic fallback workers | `Captn.process_task()` |
| Worker returns `valid=False` | `FallbackLLM.generate_recovery_proposal()` called | `Captn.process_response()` |
| No pipeline registered for task ID | SmartRouter auto-generates pipeline from description | `Captn.process_task()` |
| Autogen loop iteration limit exceeded | Loop terminates gracefully | `autogen.run_autogen_loop()` |

### Failures That Do NOT Trigger Fallback

- Configuration errors
- Missing required dependencies
- Rule validation violations
- API key errors (Ollama never needs one)

These must be fixed by the operator.

---

## OpenRouter / Cloud Provider Integration

**Status based on source code:** No OpenRouter-specific code was found in the repository.

The system uses an **OpenAI-compatible API client** (`OpenAIProvider`) in `captn/runtime/llm_provider.py` that supports both Responses and Completions API modes. By setting `OPENAI_BASE_URL`, this can point to:

| Provider | URL | API Key Required |
|----------|-----|-----------------|
| Ollama (default) | `http://localhost:11434/v1` | No |
| LM Studio | `http://localhost:1234/v1` | No |
| OpenAI | `https://api.openai.com/v1` | Yes |
| OpenRouter | `https://openrouter.ai/api/v1` | Yes |
| Any OpenAI-compatible | Configurable | Optional |

However, the `OpenAIProvider` is **only used by the `FallbackLLM`** for recovery proposals. In practice:
- Without `OPENAI_API_KEY` set, the provider logs an error and returns a structured failure
- The `FallbackLLM` handles this gracefully, returning a low-confidence default proposal
- The default Ollama connection in `mia/` uses direct REST API calls, not the OpenAI client

---

## Host Resolution for Local LLMs

**File:** `captn/runtime/llm_host.py`

The system automatically resolves the correct local LLM URL based on the execution environment:

```python
def ollama_base_url() -> str:
    explicit = os.environ.get("OLLAMA_HOST")
    if explicit:
        return explicit
    in_container = os.path.exists("/.dockerenv") or os.environ.get("IN_CONTAINER") == "1"
    if in_container:
        return "http://host.docker.internal:11434"
    return "http://localhost:11434"
```

This ensures:
- **On host:** connects to `localhost:11434`
- **In Docker container:** connects to `host.docker.internal:11434`

---

## Model Selection

The system uses a **single-model policy**. All MIA agents use the same model (`qwen2:1.5b` by default):

```python
# From config.py
model_aurelius: str = "qwen2:1.5b"
model_basilide: str = "qwen2:1.5b"
model_chymicus: str = "qwen2:1.5b"
# ... (all 13 agent models set to the same value)
```

This prevents "model not found" errors when a particular fine-tune isn't installed.

The autogen loop also defaults to `qwen2:1.5b` but accepts a `--model` override.

---

## Error Handling

| Failure | Behavior | Recovery |
|---------|----------|----------|
| Ollama not running | `ollama_ask()` returns `[OLLAMA_ERROR] ...` string | FallbackLLM returns default proposal |
| HTTP error from Ollama | HTTP status code logged, error message returned | Retry up to `max_retries` with exponential backoff |
| Truncated response | `OllamaClient._looks_truncated()` detects incomplete output | Retry with increased `num_predict` |
| JSON parse failure | `FallbackLLM` cannot parse LLM response | Returns schema-valid default proposal |
| Pipeline step failure | `Captn.process_response()` triggers `trigger_fallback_recovery()` | Recovery proposal generated by FallbackLLM |