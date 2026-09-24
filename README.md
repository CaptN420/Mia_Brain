# CaptN-BRAIN

> Déterministe d'abord. LLM si nécessaire.

**CaptN-BRAIN** is a **deterministic-first multi-agent runtime** for computational tasks. It combines a message-bus orchestration layer, ~800 deterministic tools, a from-scratch symbolic algebra engine, and an optional LLM fallback path.

## What is it?

CaptN-BRAIN routes tasks to deterministic tools when possible, avoiding LLM token consumption for tasks with verifiable correct answers. When no tool matches, it falls back to an LLM (opt-in, `--llm-fallback` flag).

The system is a **research prototype** built around a specific insight: for well-defined computational tasks (algebra, physics, chemistry, code validation), LLM token consumption is 60-85% overhead — reasoning, formatting, explanation — not computation.

## Problem

LLM-based workflows consume tokens at every stage: system prompts, few-shot examples, chain-of-thought reasoning, natural language explanations, formatting, and error correction. For tasks with a single verifiable correct answer, most of this cost is structural overhead.

## Solution

Replace LLM computation with deterministic engines for tasks that have verifiable correct answers:

1. **Route first to deterministic tools** → zero LLM tokens, guaranteed correct
2. **Cache results** → LRU cache (1024 entries) for repeated calls
3. **Fall back to LLM only when necessary** → opt-in, not default

## Architecture

```
User → CLI / Dashboard / MIA
    ↓
SmartRouter (keyword-based routing)
    ↓
ToolRouter → ~800 deterministic tools
    ↓
LLM Fallback (optional, --llm-fallback)
    ↓
Validation → Output
```

See [docs/03_ARCHITECTURE.md](docs/03_ARCHITECTURE.md) for the full architecture diagram.

## Install

### Prerequisites

- Python 3.10+
- Git

### Quick Install

```bash
# Clone the repository
git clone https://github.com/CaptN420/Mia_Brain.git
cd Mia_Brain/CaptN-BRAIN-main

# Create and activate a virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Optional: MIA Subsystem (Ollama)

```bash
# Copy environment template
cp .env.example .env

# Edit .env and configure your Ollama endpoint
# Then run MIA
python -m mia.launcher_ollama
```

## How to Use

### CLI Usage

```bash
# Run the main CLI script
python summon_agents.py <domain> <command> [args]

# Algebra examples
python summon_agents.py algebra simplify "2*x+3*x"
python summon_agents.py algebra solve "x^2-5*x+6=0"
python summon_agents.py algebra derivative "x^3+4*x^2-7*x+2"
python summon_agents.py algebra evaluate "2*x^3-3*x^2+5*x-7" x=4
python summon_agents.py algebra expand "(x+3)*(x-5)"

# List available tools
python summon_agents.py tools list

# Run a specific tool
python summon_agents.py tools run math_gcd a=12 b=8

# With LLM fallback (optional)
python summon_agents.py algebra solve "x^2-5*x+6=0" --llm-fallback
```

### Dashboard

```bash
# Start the Streamlit dashboard
python -m captn.dashboard.app
```

### MIA (Multi-Agent Intelligence Alchemy)

```bash
# Run with Ollama (local LLM)
python -m mia.launcher_ollama

# Configure in .env:
# OLLAMA_HOST=http://localhost:11434
# OLLAMA_MODEL=llama3.2
```

### Benchmarks

```bash
# Run the 100-test algebra benchmark
python -m tools.hundred_tests

# Run cross-domain benchmark
python -m tools.cross_domain_bench

# Run reconciled benchmark
python -m tools.reconcile
```

## Key Features

- **Deterministic-first routing**: `SmartRouter` + `ToolRouter` with keyword-based worker selection
- **Scratch Algebra engine**: 1,041 LOC, zero dependencies, 89.1% token savings on 100 algebra tests
- **~800 deterministic tools**: math, physics, chemistry, coding, logic, finance, and more
- **LRU cache**: 1024 entries for identical tool calls
- **Output compression**: Pre-LLM noise reduction (progress bars, timestamps, banners)
- **Message bus**: Thread-safe pub/sub with ThreadPoolExecutor (max 8 workers)
- **25+ workers**: code generation, data ingestion, quality, transformation, orchestration
- **15+ persona-based thinkers**: Aristotle, Kant, Einstein, Newton, da Vinci, Turing, and more
- **LLM fallback**: OpenAI API (GPT-4o-mini, GPT-4o, o1) or Ollama (local)

## Benchmarks

| Benchmark | Tests | Pass rate | Token savings | Avg latency |
|---|---|---|---|---|
| Scratch Algebra (27 tests) | 27 | 100% | 87.8% | 0.14ms |
| Scratch Algebra (100 tests) | 100 | 100% | 89.1% | 0.23ms |
| Cross-Domain (50 tests) | 50 | 100% (det tools) | 42.8% | 0.3ms |

**Important**: Token savings are **text-equivalent token estimates** (chars/4), not actual LLM API measurements. No LLM calls were made during deterministic testing. See [docs/07_TOKEN_ACCOUNTING.md](docs/07_TOKEN_ACCOUNTING.md).

## Results

### Scratch Algebra on 100 tests (increasing difficulty)

| Difficulty | Tests | Pass | Savings | Avg latency |
|---|---|---|---|---|
| Simplify | 10 | 10/10 | 88.8% | 0.09ms |
| Linear | 20 | 20/20 | 91.7% | 0.12ms |
| Quadratic | 20 | 20/20 | 90.2% | 0.14ms |
| Expand | 10 | 10/10 | 88.9% | 0.14ms |
| Derivative | 10 | 10/10 | 91.1% | 0.07ms |
| Mixed | 30 | 30/30 | 86.0% | 0.35ms |
| **Total** | **100** | **100/100** | **89.1%** | **0.23ms** |

## Current Status

**Research prototype.** The deterministic-first architecture is implemented and functional. The most validated component is the Scratch Algebra engine (100/100 tests, 89.1% savings). Other components (OutputCompressor, FragmentRegistry, MirrorAgent, IntentParser) exist but lack individual benchmarks.

## Limitations

- **Domain-limited**: Scratch Algebra handles only polynomials (no trig, log, exp)
- **Small sample**: 100 tests is not sufficient for statistical significance
- **Estimated baseline**: All savings are estimates, not measured against real LLM calls
- **No production testing**: The system has not been tested in production
- **No competitive comparison**: Direct benchmarks against other systems have not been performed
- **Unverified components**: Several components exist but are not benchmarked

See [docs/13_LIMITATIONS.md](docs/13_LIMITATIONS.md) for the full list.

## Documentation

| File | Content |
|---|---|
| [Executive Summary](docs/01_EXECUTIVE_SUMMARY.md) | 1-page overview |
| [Problem](docs/02_PROBLEM.md) | Technical problem definition |
| [Architecture](docs/03_ARCHITECTURE.md) | Full system architecture |
| [Workflow](docs/04_WORKFLOW.md) | Step-by-step workflow |
| [Deterministic Engines](docs/05_DETERMINISTIC_ENGINES.md) | All deterministic components |
| [Scratch Algebra](docs/06_SCRATCH_ALGEBRA.md) | Algebra engine deep dive |
| [Token Accounting](docs/07_TOKEN_ACCOUNTING.md) | How tokens are measured |
| [Benchmarks](docs/08_BENCHMARKS.md) | All benchmark results |
| [Results](docs/09_RESULTS.md) | Verified results |
| [Instrumentation](docs/10_INSTRUMENTATION.md) | Measurement methodology |
| [Competitive Analysis](docs/11_COMPETITIVE_ANALYSIS.md) | Comparison with other systems |
| [Differentiators](docs/12_DIFFERENTIATORS.md) | What makes this project different |
| [Limitations](docs/13_LIMITATIONS.md) | Known limitations |
| [Roadmap](docs/14_ROADMAP.md) | Technical roadmap |
| [IP Disclosure](docs/15_IP_DISCLOSURE.md) | IP considerations |
| [Glossary](docs/16_GLOSSARY.md) | Terminology |

## Project Structure

```
summon_agents.py          — CLI entry point
captn/
  cli/                    — CLI modules (auto-discovered)
  runtime/                — Runtime: MessageBus, ToolWorker, Thinker, etc.
  workers/                — 25+ workers by domain
  thinkers/               — 15+ persona-based agents
  dashboard/              — Streamlit dashboard
tools/                    — ~800 deterministic tools
  scratch_algebra.py      — From-scratch algebra engine
  math_tools.py           — Math tools (22)
  physics_tools.py        — Physics tools (15)
  chemistry_tools.py      — Chemistry tools (14)
  coding_tools.py         — Coding tools (6)
  logic_tools.py          — Logic tools (~20)
  mass/                   — Mass-generated tools (~700)
  hundred_tests.py        — 100-test benchmark
  reconcile.py            — 27-test reconciled benchmark
  cross_domain_bench.py   — 50-test cross-domain benchmark
mia/                      — MIA subsystem (Ollama, private)
alchimie/                 — Symbolic transformation library
wd-40/                    — Watchdog/shield layer
docs/                     — Technical documentation
BenchMark/                — Benchmark results and visualizations
```

## License

See LICENSE file.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
