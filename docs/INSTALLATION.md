# Installation Guide

---

## Requirements

### Operating System

- **Linux** (primary target, tested on Ubuntu/Debian)
- **Windows** (via Docker, see Dockerfile)
- **macOS** (should work, not primary target)

### Runtime

- **Python 3.11+** (tested with 3.11.16)
- **pip** (Python package manager)
- **Git** (for version control)

### Optional but Recommended

- **Ollama** — for local LLM inference (fallback path)
  - Install from [ollama.ai](https://ollama.ai)
  - Pull the recommended model: `ollama pull qwen2:1.5b`
  - Default port: `localhost:11434`
- **Docker** — for containerized deployment
- **Caddy / cloudflared** — for production deployment with TLS/tunnel

---

## Installation Steps

### 1. Clone the repository

```bash
git clone <repository-url> CaptN-BRAIN-main
cd CaptN-BRAIN-main
```

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Core dependencies (from `requirements.txt`):

| Package | Purpose |
|---------|---------|
| `streamlit` | Dashboard UI |
| `streamlit-autorefresh` | Auto-refresh dashboard |
| `streamlit-cookies-controller` | Dashboard session management |
| `requests` | HTTP client (Ollama API calls) |
| `openai` | OpenAI-compatible client (Ollama, LM Studio, OpenAI) |
| `pandas` | Data manipulation |
| `numpy` | Numerical operations |
| `psutil` | System resource monitoring |
| `pytest` | Testing framework |

### 4. (Optional) Install optional dependencies

For full deterministic tool functionality:

```bash
# Symbolic math (eqsolve tool)
pip install sympy

# Chemistry (chemsym tool)
pip install chempy

# Equation workbench
pip install scipy matplotlib
```

### 5. Configure environment

```bash
cp .env.example .env
```

Edit `.env` as needed (all fields are optional):

```env
# Dashboard access passphrase (generate one)
CAPTN_AUTH_PASSPHRASE=your_secure_token_here

# Force local auth too (optional)
# CAPTN_AUTH_ENFORCE_LOCAL=1

# GitHub PAT (raises crawl rate limit, optional)
# GITHUB_TOKEN=ghp_...

# OpenAI-compatible provider (Ollama/LM Studio/OpenAI)
# OPENAI_API_KEY=sk-...
# OPENAI_BASE_URL=http://localhost:11434/v1
```

### 6. (Optional) Set up Ollama

```bash
# Install Ollama (Linux/macOS)
curl -fsSL https://ollama.ai/install.sh | sh

# Pull the recommended model (~1GB)
ollama pull qwen2:1.5b

# Verify it's running
curl http://localhost:11434/api/tags
```

---

## Verification

### Verify the CLI works

```bash
python summon_agents.py --help
```

Expected output:

```
usage: summon_agents.py [-h]
                         ...

Summon CaptN-BRAIN agents/workers

options:
  -h, --help  show this help message and exit
```

### List available commands

```bash
python summon_agents.py --help
```

Should show commands auto-discovered from `captn/cli/` and `tools/`.

### Verify deterministic tools

```bash
python -m tools.eqsolve simplify "(x + y)**2 - (x**2 + 2*x*y + y**2)"
```

Expected output: `0` (deterministic, no LLM needed).

### Verify deterministic code transform

```bash
echo "def add(a,b): return a+b" | python summon_agents.py deterministic
```

Expected output: a version of the function with docstring and type annotations added.

### Run the benchmark suite

```bash
python -m tools.benchmark --list
python -m tools.benchmark --suite eqsolve
```

### Verify MIA subsystem (without LLM)

```bash
python -m mia.start_v2 --help
```

---

## Docker Deployment

### Build and run

```bash
docker-compose up -d
```

This starts:
- The CaptN dashboard on port 8501
- The MIA orchestrator
- Configuration for Ollama connection via `host.docker.internal`

### Container environment configuration

See `captn/runtime/llm_host.py` for environment-aware URL resolution:

```env
# For containerized deployment
OLLAMA_HOST=http://host.docker.internal:11434
# or
IN_CONTAINER=1
```

---

## Directory Structure After Installation

```
CaptN-BRAIN-main/
├── captn/              # Deterministic agent runtime
│   ├── cli/            # CLI command modules (auto-discovered)
│   ├── dashboard/      # Streamlit dashboard
│   ├── orchestrator/   # Pipeline orchestration
│   ├── runtime/        # Core runtime (bus, state, plugins)
│   ├── thinkers/       # 20 deterministic knowledge workers
│   └── workers/        # Worker plugins (coding, ingestion, quality, etc.)
├── mia/                # Multi-Agent Intelligence Alchemy
├── alchimie/           # Transformation rule library
├── tools/              # 26+ standalone deterministic tools
├── tests/              # Pytest suite (8 test files)
├── deploy/             # Docker/Caddy/tunnel configs
├── docs/               # Documentation
├── wd-40/              # Watchdog/shield layer (daemon, quarantine, sandbox, netcheck)
├── summon_agents.py    # Unified CLI entry point
├── .env.example        # Environment template
└── requirements.txt    # Python dependencies
```