# Local Model System

---

## Supported Local Models

The system is designed to work with **Ollama** as the primary local LLM backend. The recommended and default model is:

| Model | Size | Purpose |
|-------|------|---------|
| `qwen2:1.5b` | ~1 GB | All MIA agents, autogen loop, fallback recovery |

The single-model policy (`mia/config.py`) ensures every agent uses the same model, preventing "model not found" errors.

Other compatible models (tested via `--model` override):

| Model | Size | Notes |
|-------|------|-------|
| `qwen2.5:3b` | ~2 GB | Better quality, more RAM |
| `llama3.2:3b` | ~2 GB | Alternative option |
| `phi3:mini` | ~2 GB | Microsoft Phi-3, strong for code |
| `tinyllama:1.1b` | ~700 MB | Lightweight fallback |

---

## Configuration

### Basic Setup

```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the recommended model
ollama pull qwen2:1.5b

# Verify
ollama list
```

### Environment

Ollama runs on `localhost:11434` by default. CaptN-BRAIN auto-detects the correct URL:

- **On host:** `http://localhost:11434`
- **In Docker:** `http://host.docker.internal:11434`
- **Override:** Set `OLLAMA_HOST` environment variable

### Using a Different Model

```bash
# CLI override for autogen
python summon_agents.py autogen --model llama3.2:3b

# MIA override via environment (not implemented as env var — edit config.py)
# Or modify mia/launcher_ollama.py OLLAMA_MODEL constant
```

---

## Hardware Requirements

| Model | Minimum RAM | Recommended | GPU VRAM |
|-------|------------|-------------|----------|
| `qwen2:1.5b` | 2 GB | 4 GB | 2 GB |
| `qwen2.5:3b` | 4 GB | 8 GB | 4 GB |
| `llama3.2:3b` | 4 GB | 8 GB | 4 GB |
| `phi3:mini` | 4 GB | 8 GB | 4 GB |

The project has GPU configuration for dual-GPU setups (see `config.py`):
```python
gpu_devices: str = "1,0"  # GPU 1 (3080 12GB) first, then GPU 0 (3070 Ti 8GB)
```

---

## How Local Inference Works

### Via OllamaClient (`mia/ollama_client.py`)

The `OllamaClient` provides robust local inference with:

- **Retry with exponential backoff** (configurable via `max_retries` and `backoff_seconds`)
- **Truncation detection** — checks for incomplete output patterns:

```python
def _looks_truncated(self, text: str) -> bool:
    # Checks for dangling urls, unmatched markdown, open brackets
    bad_end = text.endswith(":") or text.endswith("-") or text.endswith("(")
    if t.count("**") % 2 == 1:  # unmatched bold markers
        return True
    if t.count("(") > t.count(")"):  # open parentheses
        return True
```

- **Output cleaning** — removes repeated lines, collapses blank lines, strips dangling tails
- **Security guard** — calls `guard_network_request()` from `action_monitor.py` before every request
- **Token accounting** — accumulates usage for dashboard monitoring

### Via Autogen (`captn/workers/code_generation/autogen.py`)

The autogen loop provides additional safety:

- **Host validation** — `_assert_localhost()` refuses non-localhost endpoints
- **Patch validation** — every generated file passes through `PatchValidator` before writing
- **Loop bounds** — configurable iterations and samples-per-iteration

### Direct REST API (`mia/launcher_ollama.py`)

For the MIA debate loop, the launcher uses direct HTTP requests to the Ollama REST API:

```python
OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2:1.5b"

def ollama_ask(messages, temperature=0.2, num_predict=120):
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    resp = requests.post(f"{OLLAMA_BASE_URL}/v1/chat/completions", json=payload, timeout=60)
```

---

## LM Studio Support

LM Studio (`http://localhost:1234/v1`) is supported via the same OpenAI-compatible interface:

```bash
# Configure via environment
export OPENAI_BASE_URL=http://localhost:1234/v1
```

The `llm_host.py` module provides `lm_studio_base_url()` with the same container-aware resolution logic.

---

## Full Deterministic Mode

The system can operate **without any LLM** by setting:

```python
# In mia/config.py
llm_enabled: bool = False         # Default — NO agent calls the LLM
llm_as_fallback: bool = True      # LLM as last-resort recovery
```

In this mode:
- All routing is deterministic (SmartRouter)
- All code generation is deterministic (DeterministicCoder)
- All validation is deterministic (PatchValidator, workers)
- All knowledge retrieval is deterministic (CorpusThinkers)
- The MIA debate loop uses **deterministic template generators** instead of LLM proposals
- The LLM is only called if a pipeline step fails (recovery proposals)

---

## Advantages and Limitations

### Advantages

- **Zero API cost** — all local inference
- **Privacy** — no data leaves your machine via LLM calls
- **No rate limits** — local inference has no API throttling
- **Low latency** — small models (1.5B) run fast even on consumer hardware
- **Offline capable** — no internet needed for basic operation

### Limitations

- **Model quality** — 1.5B parameter models have limited reasoning capability
- **Deterministic-first by design** — the LLM is intentionally limited to fallback/recovery roles
- **Hardware dependent** — larger models require more RAM/VRAM
- **Only one model** — single-model policy avoids "model not found" but limits flexibility