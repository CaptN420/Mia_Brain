# API Reference

This document covers the unified CLI (`summon_agents.py`), all standalone tool commands, and the internal bus message contracts.

---

## Unified CLI: `summon_agents.py`

The main entry point for all CaptN-BRAIN operations.

```bash
python summon_agents.py <command> [options]
```

Commands are auto-discovered from:
- `captn/cli/` — runtime commands (thinker, mirror, deterministic, autogen, fixgen, pipeline, raw2json, equation, workbench)
- `tools/` — standalone tools with `register_cli()` (codechunk, depgraph, eqsolve, chemsym, etc.)

---

### Runtime Commands

#### `thinker` — Synthesize worker findings

```bash
python summon_agents.py thinker --findings results.json --output report.json
```

| Argument | Description | Required |
|----------|-------------|----------|
| `--findings, -f` | JSON file with worker results (or `-` for stdin) | Yes |
| `--alchimie-version` | Alchimie version to include in report | No |
| `--output, -o` | Output file (default: stdout) | No |

#### `mirror` — Mirror reasoning via MirrorAgent

```bash
python summon_agents.py mirror --hypothesis "E=mc^2" --output mirror.json
```

| Argument | Description | Required |
|----------|-------------|----------|
| `--hypothesis, -H` | Hypothesis to mirror | Yes |
| `--task-id` | Task ID (default: auto-generated) | No |
| `--output, -o` | Output file (default: stdout) | No |

#### `deterministic` — AST code transforms (no LLM)

```bash
# From string
python summon_agents.py deterministic --code "def add(a,b): return a+b"

# From file
python summon_agents.py deterministic --file input.py --output output.py

# From stdin
echo "def add(a,b): return a+b" | python summon_agents.py deterministic
```

| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--code, -c` | Source code string | No | — |
| `--file, -f` | Source file path | No | — |
| `--modes, -m` | Transform modes (docstrings, annotate, normalize) | No | all three |
| `--output, -o` | Output file | No | stdout |
| `--write-dir` | Also write via generate_to_file to this directory | No | — |

#### `autogen` — Run autogen loop (LLM fallback)

```bash
python summon_agents.py autogen --dataset dataset.jsonl --iterations 5
```

| Argument | Description | Default |
|----------|-------------|---------|
| `--dataset, -d` | Dataset JSONL path | `mirror/dataset.jsonl` |
| `--ollama-url` | Ollama base URL | `http://localhost:11434` |
| `--model` | Ollama model | `qwen2:1.5b` |
| `--iterations, -i` | Number of iterations | 3 |
| `--sample-per-iter, -s` | Samples per iteration | 4 |
| `--out-dir` | Output directory | `generated` |
| `--feed-corpus` | Feed validated code to corpus | False |
| `--corpus-dir` | Corpus directory | `Code_base` |
| `--seed` | Random seed | 42 |
| `--no-llm-fallback` | Disable LLM fallback mode | False |
| `--output, -o` | Result summary output file | stdout |

#### `fixgen` — Generate fix template

```bash
python summon_agents.py fixgen --task-id "TASK-001" --pgm "problem graph model description"
```

| Argument | Description | Required |
|----------|-------------|----------|
| `--task-id` | Task ID | Yes |
| `--pgm` | Problem graph model (description) | Yes |

#### `pipeline` — Run full CaptN pipeline

```bash
python summon_agents.py pipeline --project /path/to/project --name "My Project"
```

| Argument | Description | Required | Default |
|----------|-------------|----------|---------|
| `--project, -p` | Project directory path | Yes | — |
| `--name, -n` | Project name | No | `cli-project` |
| `--pipeline` | Pipeline ID to run | No | `analysis` |
| `--wait, -w` | Seconds to wait for completion | No | 10 |

#### `raw2json` — Convert directory to JSONL dataset

```bash
python summon_agents.py raw2json --project /path/to/project
```

| Argument | Description | Required |
|----------|-------------|----------|
| `--project, -p` | Project directory path | Yes |
| `--task-id` | Task ID (default: auto) | No |

#### `equation` — Run equation tools

```bash
# List available tools
python summon_agents.py equation list
python summon_agents.py equation list --domain math

# Run a tool
python summon_agents.py equation run solve_quadratic --kwargs '{"a":1,"b":-3,"c":2}'
```

| Subcommand | Description |
|------------|-------------|
| `list` | List available equation tools (optionally `--domain`) |
| `run <tool_name>` | Run a specific tool with `--kwargs '{"key":"val"}'` |

#### `workbench` — Equation workbench

```bash
# List tools
python summon_agents.py workbench list

# Run a tool
python summon_agents.py workbench run validate --kwargs '{"equation":"Ndot=k*A*B"}'
```

Same subcommands as `equation`: `list`, `run <tool_name>`.

---

### Standalone Tool Commands

These tools are auto-discovered and registered as CLI commands. Run any with `--help` for detailed options.

| Command | Description | Source |
|---------|-------------|--------|
| `codechunk` | Code chunk analysis | `tools/codechunk.py` |
| `eqsolve` | Symbolic equation solver | `tools/eqsolve.py` |
| `chemsym` | Chemistry utilities | `tools/chemsym.py` |
| `depgraph` | Dependency graph analysis | `tools/depgraph.py` |
| `codebase-map` | Codebase structure map | `tools/codebase_map.py` |
| `healthcheck` | Project health score | `tools/healthcheck.py` |
| `testgen` | Generate pytest stubs | `tools/testgen.py` |
| `deadscout` | Dead code detection | `tools/deadscout.py` |
| `apigen` | API documentation generation | `tools/apigen.py` |
| `changelog` | Git changelog generator | `tools/changelog.py` |
| `loganomaly` | Log anomaly detection | `tools/loganomaly.py` |
| `nl2eq` | Natural language → equation | `tools/nl2eq.py` |
| `dataset-tools` | Dataset utilities | `tools/dataset_tools.py` |
| `corpus-tools` | Corpus utilities | `tools/corpus_tools.py` |
| `pipeline-trace` | Pipeline tracing | `tools/pipeline_trace.py` |
| `lint-tool` | Code linting utility | `tools/lint_tool.py` |
| `imports-tool` | Import analysis | `tools/imports_tool.py` |
| `diff-ast` | AST diff tool | `tools/diff_ast.py` |
| `context-ai` | Context AI tool | `tools/context_ai.py` |

#### `eqsolve` subcommands

```bash
python -m tools.eqsolve derive "k * A * B" --variable A
python -m tools.eqsolve integrate "x**2" --variable x
python -m tools.eqsolve solve "x**2 - 4" --variable x
python -m tools.eqsolve solve-system '["x + y = 5", "x - y = 1"]'
python -m tools.eqsolve taylor "sin(x)" --variable x --order 4
python -m tools.eqsolve simplify "(x + y)**2 - (x**2 + 2*x*y + y**2)"
python -m tools.eqsolve limit "sin(x)/x" --variable x --to 0
```

---

## Direct Tool Execution

Every standalone tool can also be run directly:

```bash
python -m tools.eqsolve simplify "x**2 - 4"
python -m tools.chemsym parse "H2SO4"
python -m tools.depgraph analyze --path /path/to/project
python -m tools.benchmark --suite eqsolve
```

---

## Internal Bus Message Contracts

These are the communication contracts between components.

### Message Structure

```python
@dataclass
class Message:
    id: str               # auto-generated UUID
    sender: str           # who sent it
    destination: str      # who should receive it
    type: str             # "task", "state_update", "error", "response"
    payload: Dict[str, Any]
    timestamp: float
```

### Message Types

| Type | Direction | Purpose |
|------|-----------|---------|
| `task` | → Worker | Distribute work |
| `response` | → Orchestrator | Return results |
| `error` | → Orchestrator / User | Report failures |
| `state_update` | → StateStore | Update task state |
| `ui_command` | → Orchestrator | Dashboard/user commands |

### Task Payload Contract

```python
{
    "task_id": str,           # required
    "pipeline_id": str,       # which pipeline this belongs to
    "action": str,            # the action to perform
    "source": str,            # origin (cli, dashboard, internal)
    "priority": int,          # 1 (default)
    "payload": Dict,          # worker-specific data
    "file_path": Optional[str],  # optional file target
}
```

### Worker Response Contract

```python
{
    "task_id": str,
    "current_step_plugin": str,   # worker name
    "valid": bool,                # success/failure
    "result": Dict,               # worker-specific results
    "error_message": Optional[str],  # error detail if not valid
    "code": Optional[str],        # generated code (for coders)
    "file_path": Optional[str],   # output file path
    "method": Optional[str],      # transform method used
}
```

### Analysis Pipeline Result

The analysis pipeline produces a synthesis report via `Thinker.synthesize()`:

```python
{
    "summary": str,              # human-readable summary
    "total_findings": int,       # count of issues found
    "confidence_score": int,     # 0-100
    "detailed_findings": List,   # individual worker findings
    "worker_breakdown": Dict,    # per-worker detail
}
```

### FallbackLLM Recovery Proposal

```python
{
    "type": "recovery_proposal",
    "diagnosis": str,              # required
    "new_hypothesis": Optional[str],
    "proposed_strategy": str,      # required
    "expected_effect": Optional[str],
    "confidence": float,            # 0.0-1.0
    "requires_validation": True,    # ALWAYS True (defense-in-depth)
}
```

---

## Streamlit Dashboard API

The dashboard runs on port `8501` by default.

```bash
python -m captn.dashboard.app
```

Endpoints (internal):

| Path | Method | Purpose |
|------|--------|---------|
| `/` | GET | Main dashboard |
| `/Project Scanner` | GET | Load project context |
| `/Analysis Pipeline` | GET | Run analysis pipeline |
| `/Alchimie` | GET | Browse alchimie library |
| `/Debug` | GET | Debug view (state, logs) |
| `/Process Manager` | GET | Manage background processes |

The dashboard uses `streamlit-autorefresh` for live updates and `streamlit-cookies-controller` for session management.