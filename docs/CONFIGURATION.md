# Configuration Reference

---

## Configuration Sources

Configuration in CaptN-BRAIN is resolved from three sources (in order of precedence):

1. **Environment variables** (highest priority)
2. **`.env` file** (project root, gitignored)
3. **`RuntimeConfig` defaults** (`mia/config.py`)
4. **Hardcoded defaults** in individual modules

---

## Environment Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `CAPTN_AUTH_PASSPHRASE` | — | Optional | Dashboard login passphrase. If set, ALL remote visitors must supply it. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CAPTN_AUTH_ENFORCE_LOCAL` | 0 | Optional | Set to `1` to force local clients (127.0.0.1) to authenticate too (defense in depth) |
| `GITHUB_TOKEN` | — | Optional | GitHub Personal Access Token — raises API rate limit for the crawler |
| `OPENAI_API_KEY` | — | Optional | API key for OpenAI-compatible providers (Ollama, LM Studio, OpenAI) |
| `OPENAI_BASE_URL` | `http://localhost:11434/v1` | Optional | Base URL for OpenAI-compatible API endpoint |
| `OLLAMA_HOST` | `http://localhost:11434` | Optional | Ollama server URL (auto-resolves `host.docker.internal` in containers) |
| `LM_STUDIO_HOST` | `http://localhost:1234` | Optional | LM Studio server URL |
| `IN_CONTAINER` | 0 | Optional | Set to `1` to force container-mode host resolution |

---

## RuntimeConfig (`mia/config.py`)

The `RuntimeConfig` dataclass defines all runtime parameters for the MIA subsystem.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `base_dir` | (required) | Base workspace directory |
| `session_dir` | `None` | Session-specific subdirectory |
| `api_url` | `http://localhost:11434/api/chat` | Ollama API endpoint |
| `request_timeout` | `60` | HTTP request timeout in seconds |
| `max_api_retries` | `1` | Maximum retries on API failure |
| `retry_backoff_seconds` | `0.7` | Backoff multiplier between retries |
| `api_key` | `None` | API key (Ollama needs none) |
| `gpu_devices` | `"1,0"` | GPU device ordering (GPU 1 first, then GPU 0) |
| `model_aurelius` | `"qwen2:1.5b"` | Agent model — ALL agents use the same model |
| `model_basilide` | `"qwen2:1.5b"` | (same) |
| `model_chymicus` | `"qwen2:1.5b"` | (same) |
| `model_archiviste` | `"qwen2:1.5b"` | (same) |
| `model_sentinelle` | `"qwen2:1.5b"` | (same) |
| `model_hermes` | `"qwen2:1.5b"` | (same) |
| `model_hermetica` | `"qwen2:1.5b"` | (same) |
| `model_synthetiseur` | `"qwen2:1.5b"` | (same) |
| `model_reviseur` | `"qwen2:1.5b"` | (same) |
| `model_variable_validator` | `"qwen2:1.5b"` | (same) |
| `model_equation_validator` | `"qwen2:1.5b"` | (same) |
| `model_final_validator` | `"qwen2:1.5b"` | (same) |
| `num_predict_debate` | `150` | Max tokens for debate generation |
| `num_predict_validation` | `80` | Max tokens for validation |
| `num_predict_archive` | `200` | Max tokens for archival |
| `num_predict_sentinelle` | `120` | Max tokens for sentinel |
| `num_predict_support` | `100` | Max tokens for support agent |
| `num_predict_synth` | `180` | Max tokens for synthesis |
| `num_predict_hermes` | `200` | Max tokens for Hermes agent |
| `temperature_debate` | `0.7` | Temperature for debate agents |
| `temperature_support` | `0.3` | Temperature for support agents |
| `temperature_hermes` | `0.5` | Temperature for Hermes agent |
| `max_history_messages` | `16` | Max conversation history length |
| `llm_as_fallback` | `True` | Use LLM only as last-resort fallback |
| `enable_role_guard_fallbacks` | `True` | Enable role guard fallback behavior |
| `llm_enabled` | `False` | **FULL DETERMINISTIC MODE**: when False, NO agent calls the LLM |

---

## SmartRouter Configuration

The SmartRouter keyword-to-worker routing table is defined in `captn/workers/orchestration/smart_router.py`.

Key constants that can be modified:

| Constant | Description |
|----------|-------------|
| `WORKER_ROUTES` | Dict of `keyword → [worker_names]` — 110+ route entries |
| `WORKER_CAPABILITIES` | Dict of `worker_name → {tags, domain, description}` |
| `OPPOSITION_DOMAINS` | Dict of `domain → opposing_domain` for diversity routing |

---

## CLI Arguments (summon_agents.py)

All commands accept `--help` for detailed options.

### Runtime commands (CaptN)

| Command | Description | Key Arguments |
|---------|-------------|---------------|
| `pipeline` | Run full analysis pipeline | `--project` (required), `--name`, `--wait` |
| `thinker` | Synthesize worker findings | `--findings` (required), `--output` |
| `mirror` | Mirror reasoning hypothesis | `--hypothesis` (required), `--output` |
| `deterministic` | AST code transforms (no LLM) | `--code` / `--file`, `--modes`, `--output` |
| `autogen` | Autogen loop (LLM fallback) | `--dataset`, `--iterations`, `--ollama-url` |
| `fixgen` | Generate fix template | `--task-id` (required), `--pgm` (required) |
| `raw2json` | Convert directory to JSONL | `--project` (required) |

### Tool commands

| Command | Description | Key Arguments |
|---------|-------------|---------------|
| `equation` | Math/physics/chemistry tools | `list`, `run <tool>` |
| `workbench` | Equation workbench | `list`, `run <tool>` |
| `codechunk` | Code chunk analysis | Various |
| `depgraph` | Dependency graph analysis | Various |
| `eqsolve` | Symbolic equation solver | `derive`, `integrate`, `solve`, etc. |
| `chemsym` | Chemistry utilities | Various |
| `codebase-map` | Codebase structure map | Various |
| `healthcheck` | Project health score | Various |

---

## Logging Configuration

Logging is configured at the module level with `logging.basicConfig()`:

| Module | Log File | Level |
|--------|----------|-------|
| `captn.runtime.runtime` | `runtime.log` | INFO |
| `captn.cli` | stdout | INFO |
| `mia.launcher_ollama` | `mia_evolution.log` | INFO |
| `summon_agents` | stdout | INFO |
| `autogen` | `autogen.log` | INFO |