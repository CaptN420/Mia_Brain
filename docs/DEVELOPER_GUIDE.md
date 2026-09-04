# Developer Guide

This document explains the project structure, core modules, and how to extend CaptN-BRAIN with new workers, tools, thinkers, and routing rules.

---

## Directory Structure

```
CaptN-BRAIN-main/
├── captn/                    # Deterministic agent runtime
│   ├── __init__.py
│   ├── cli/                  # CLI modules (auto-discovered by summon_agents.py)
│   │   ├── __init__.py       # auto_discover() + register_tool_modules()
│   │   ├── _codegen.py       # thinker, mirror, deterministic, autogen, fixgen
│   │   ├── _equations.py     # equation, workbench
│   │   └── _orchestrator.py  # pipeline, raw2json
│   ├── dashboard/            # Streamlit dashboard
│   │   └── app.py
│   ├── orchestrator/
│   │   └── main.py
│   ├── runtime/              # Core runtime (the engine)
│   │   ├── base.py           # Message, Task, Rule, Pipeline, Plugin
│   │   ├── runtime.py        # Captn, MessageBus, StateStore, RuleEngine
│   │   ├── manager.py        # PluginManager
│   │   ├── llm_provider.py   # OpenAIProvider, FallbackLLM
│   │   ├── llm_host.py       # Ollama host resolution (host vs container)
│   │   ├── mirror_agent.py   # MirrorAgent
│   │   ├── mirror_rules.py   # MirrorRules
│   │   ├── thinker.py        # Thinker
│   │   ├── validator.py      # PatchValidator
│   │   ├── workers.py        # SyntaxWorker, BugWorker
│   │   └── schemas.py        # UniversalData, AgentTask, AgentResponse
│   ├── thinkers/             # 20 deterministic corpus thinkers
│   │   ├── base.py           # CorpusThinker base class
│   │   ├── philosophy/       # 9 philosophers
│   │   ├── science/          # 8 scientists/mathematicians
│   │   ├── esoteric/         # 1 alchemist
│   │   └── polymath/         # 2 polymaths
│   └── workers/              # Worker plugins
│       ├── code_generation/   # DeterministicCoder, autogen, fixgen, polyglot
│       ├── data_ingestion/    # Crawler, extractor, raw2json
│       ├── orchestration/     # SmartRouter, opposition_router, distributor
│       ├── quality/           # Validator, diversity, no_repetition
│       └── transformation/    # Normalizer, translator, diversifier, math/chem validation
├── mia/                      # Multi-Agent Intelligence Alchemy
├── tools/                    # 26+ standalone deterministic tools
├── tests/                    # 8 test files
├── alchimie/                 # Transformation rule library
├── wd-40/                    # Watchdog safety layer
├── deploy/                   # Docker, Caddy, tunnel
├── scripts/                  # Maintenance scripts
├── summon_agents.py          # Unified CLI entry point (67 lines)
├── .env.example              # Environment template
└── requirements.txt          # Python dependencies
```

---

## Core Modules

### Runtime Data Types (`captn/runtime/base.py`)

```python
@dataclass
class Message:
    id: str
    sender: str
    destination: str
    type: str          # "task", "state_update", "error", "response"
    payload: Dict
    timestamp: float

@dataclass
class Task:
    task_id: str
    source: str
    action: str
    pipeline_id: str
    priority: int = 1
    payload: Dict
    retries: int = 0
    file_path: Optional[str] = None

class Plugin:
    name: str = "base"
    def initialize(self) -> bool: ...
    def execute(self, message: Message) -> None: ...
    def shutdown(self) -> bool: ...
```

### Plugin Manager (`captn/runtime/manager.py`)

The `PluginManager` provides:
- `register_plugin(plugin)` — add an instantiated plugin to the registry
- `get_plugin(name)` — retrieve plugin by name
- `discover_plugins()` — find `.py` files in plugin directory
- `load_plugin(file)` — dynamic import and instantiation

### SmartRouter (`captn/workers/orchestration/smart_router.py`)

The SmartRouter provides three main methods:

| Method | Purpose |
|--------|---------|
| `route(description, top_k, opposition_mode)` | Select best workers for a task |
| `score_worker(name, description)` | Score a single worker against a description |
| `resolve_pipeline_steps(description, top_k)` | Generate ordered pipeline steps |
| `status()` | Current state (workers + usage metrics) |

### Captn Orchestrator (`captn/runtime/runtime.py`)

The `Captn` class provides:
- `register_pipeline(pipeline)` — register a named pipeline
- `load_project_context(path, name)` — load workspace for analysis
- `run_analysis_pipeline()` — execute the full analysis pipeline
- `resolve_pipeline_for_task(description)` — use SmartRouter for ad-hoc pipelines
- `handle_message(message)` — dispatch to task/response/error handlers
- `process_task(message)` — validate, dispatch to pipeline steps
- `process_response(message)` — advance pipeline or trigger synthesis/fallback

### CLI Discovery (`captn/cli/__init__.py`)

```python
def auto_discover(subparsers):
    """Load every *registered* cli module from this package."""
    for _, mod_name, _ in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(full_name)
        if hasattr(mod, "register_cli"):
            mod.register_cli(subparsers)

def register_tool_modules(subparsers):
    """Discover and register standalone tools that expose register_cli."""
    for py_file in sorted(tools_dir.glob("*.py")):
        mod = importlib.import_module(f"tools.{mod_name}")
        if hasattr(mod, "register_cli"):
            mod.register_cli(subparsers)
```

---

## How to Add a New Feature

### Adding a New Deterministic Worker

1. **Create the worker class** in `captn/workers/<category>/`:

```python
# captn/workers/quality/my_new_worker.py
from captn.runtime.base import Message, Plugin

class MyNewWorker(Plugin):
    name = "my_new_worker"
    
    def __init__(self, bus=None):
        self.bus = bus
    
    def initialize(self) -> bool:
        return True
    
    def execute(self, message: Message) -> None:
        # Process the task
        result = {"my_result": "processed"}
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={"task_id": message.payload.get("task_id"),
                     "current_step_plugin": self.name,
                     "valid": True,
                     "result": result}
        )
        if self.bus:
            self.bus.publish(response)
    
    def shutdown(self) -> bool:
        return True
```

2. **Register the worker** in the `Captn` initializer (in `captn/runtime/runtime.py`):

```python
from captn.workers.quality.my_new_worker import MyNewWorker
self.manager.register_plugin(MyNewWorker(bus))
```

3. **Add to SmartRouter** in `captn/workers/orchestration/smart_router.py`:

```python
WORKER_CAPABILITIES["my_new_worker"] = {
    "tags": ["my", "new", "keyword1"],
    "domain": "quality",
    "description": "My new worker does X"
}
```

4. **Add keyword routes** for automatic selection:

```python
WORKER_ROUTES["my_keyword"] = ["my_new_worker", "validator", ...]
```

### Adding a New Standalone Tool

1. **Create the tool** in `tools/`:

```python
#!/usr/bin/env python3
"""mytool.py — My new deterministic tool."""

def run_tool(input_data):
    """Core function."""
    return {"result": processed_data, "ok": True}

def register_cli(subparsers):
    """Register CLI command for summon_agents.py."""
    p = subparsers.add_parser("mytool", help="New deterministic tool")
    p.add_argument("--input", required=True, help="Input data")
    p.set_defaults(func=_cmd_mytool)

def _cmd_mytool(args):
    import json
    result = run_tool(args.input)
    print(json.dumps(result, indent=2))
```

2. The tool is **auto-discovered** by `register_tool_modules()` in `captn/cli/__init__.py`.

3. **Add exclude** to the skip list if you want to prevent auto-registration:
```python
if mod_name.startswith("_") or mod_name in ("mytool", ...):
    continue
```

### Adding a New Thinker

1. **Create a corpus file** in `captn/thinkers/<domain>/`:

```
# captn/thinkers/science/myson_thinker.txt
1. My Person - Made a notable contribution in year (description...)
2. Another Person - Another important contribution...
```

2. **Create the thinker class**:

```python
# captn/thinkers/science/my_thinker.py
from captn.thinkers.base import CorpusThinker

class MyThinker(CorpusThinker):
    corpus_file = "thinkers/science/myson_thinker.txt"
    domain = "science"
```

3. **Register** in `captn/thinkers/__init__.py`:

```python
from captn.thinkers.science.my_thinker import MyThinker

CORPUS_THINKERS["my_thinker"] = MyThinker
```

4. The trainer is automatically available via `load_all_thinkers()`.

### Adding a New CLI Command to CaptN

1. **Create a CLI module** in `captn/cli/`:

```python
#!/usr/bin/env python3
"""CLI command: mycommand."""

def register_cli(subparsers):
    p = subparsers.add_parser("mycommand", help="Does something")
    p.add_argument(...)
    p.set_defaults(func=_cmd_mycommand)

def _cmd_mycommand(args):
    # Your implementation
    pass
```

2. The module is **auto-discovered** by `auto_discover()`.

---

## Testing

### Test Framework

The project uses **pytest** for testing. Configuration is in `pyproject.toml` or `setup.cfg` (project standard).

### Test Files

Located in `tests/`:

| Test File | What It Tests |
|-----------|--------------|
| `test_core_runtime.py` | Core runtime components (bus, state, plugins) |
| `test_deterministic_coder.py` | DeterministicCoder AST transforms |
| `test_mirror_transformation.py` | MirrorAgent mirror operations |
| `test_crawler.py` | Crawler worker |
| `test_raw2json.py` | Raw-to-JSON conversion |
| `test_raw2json_worker.py` | Raw2JsonWorker bus integration |
| `test_alchimie_library_manager.py` | Alchimie library management |
| `test_corpus_dedup.py` | Corpus deduplication |

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_deterministic_coder.py -v

# Run with coverage
python -m pytest tests/ --cov=captn

# Run benchmark suite
python -m tools.benchmark
```

### Writing Tests

```python
# tests/test_my_feature.py
from captn.runtime.base import Message, Task

def test_message_creation():
    msg = Message(sender="test", destination="captn", type="task")
    assert msg.sender == "test"
    assert msg.destination == "captn"
    assert msg.type == "task"

def test_task_validation():
    task = Task(task_id="t1", source="cli", action="test", pipeline_id="p1")
    assert task.status == "pending"
    assert task.to_json()["task_id"] == "t1"
```

### Integration Test Considerations

- Tests should mock the bus to avoid threading issues
- Tests should never call a real LLM
- Use controlled input files for deterministic worker tests
- The SmartRouter can be tested without any external dependencies

---

## Request Lifecycle (Detailed)

```
1. CLI/Dashboard sends request to Captn
2. Captn.process_task() validates with RuleEngine
3. If valid: determine pipeline (registered or SmartRouter-generated)
4. For each step in pipeline:
   a. dispatch_to_plugin() sends Message via bus
   b. Worker processes and publishes response
   c. Captn.process_response() advances pipeline
5. On pipeline completion: notify caller
6. On worker failure: FallbackLLM generates recovery proposal
```