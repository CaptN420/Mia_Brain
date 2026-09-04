# Troubleshooting Guide

---

## Common Problems and Solutions

---

### Problem: "Command not found" when running `summon_agents.py`

**Possible causes:**
- Not in the project root directory
- Virtual environment not activated
- Module path issues

**Solution:**
```bash
cd /path/to/CaptN-BRAIN-main
source venv/bin/activate
python summon_agents.py --help
```

---

### Problem: Import errors when running commands

**Possible causes:**
- Missing dependencies
- `captn/` or `tools/` not on `sys.path`
- Missing optional packages (sympy, chempy, etc.)

**Solution:**
- The project auto-adds the project root and `tools/` to `sys.path` via `summon_agents.py` and individual tool files.
- Install required packages: `pip install -r requirements.txt`
- For optional tools: `pip install sympy chempy`

---

### Problem: Ollama connection error

**Possible cause:**
```
[OLLAMA_ERROR] HTTP 404: ... "model not found"
```
- The model specified is not installed in Ollama
- Or Ollama is not running

**Solution:**
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags

# Pull the required model
ollama pull qwen2:1.5b

# Verify model is installed
ollama list

# Start Ollama if not running
ollama serve
```

---

### Problem: Dashboard shows no data or empty analysis

**Possible cause:**
- No project context loaded
- Project path doesn't exist
- No Python files found in the project directory

**Solution:**
```bash
# Verify project path exists and has Python files
python -c "from pathlib import Path; p = Path('/path/to/project'); print(p.exists(), list(p.glob('**/*.py'))[:5])"

# Load project context explicitly
python summon_agents.py pipeline --project /path/to/project --name my-project
```

---

### Problem: DeterministicCoder returns "nothing-to-do"

**Possible cause:**
- Source code already has docstrings and annotations
- Modes selected don't produce changes

**Solution:**
```bash
# Check what would change
echo "def bare_function(x): return x" | python summon_agents.py deterministic --modes docstrings annotate

# If "nothing-to-do" for bare code, check the source is valid Python
python -c "import ast; ast.parse('your code here')"
```

---

### Problem: Pipeline step fails with no recovery

**Possible cause:**
- Worker crashes unexpectedly
- Fallback LLM not available (no Ollama running)
- Recovery proposal fails schema validation

**Solution:**
- Check `runtime.log` for error details
- Ensure Ollama is running for fallback to work
- If running deterministic-only mode, failures should not occur for valid inputs

---

### Problem: "No thinkers available" for OppositionRouter

**Possible cause:**
- Thinker corpus files (.txt) are missing or empty
- Corpus files have incorrect format

**Solution:**
```bash
# Check which corpus files exist
ls captn/thinkers/*/*.txt

# Test loading all thinkers
python -c "from captn.thinkers import load_all_thinkers; print(len(load_all_thinkers()))"
```

Expected format for corpus entries:
```
1. Name - Description of contribution (at least 10 characters)
```

---

### Problem: MIA evolution loop fails

**Possible cause:**
- Ollama not running
- Model not installed or wrong model specified
- Session directory permissions

**Solution:**
```bash
# Ensure Ollama is running with the correct model
curl http://localhost:11434/api/chat -d '{"model":"qwen2:1.5b","messages":[{"role":"user","content":"test"}]}'

# Check session directory exists
ls -la mia/session/
```

---

### Problem: Slow performance

**Possible cause:**
- Large project being analyzed
- Many workers registered
- Ollama running on CPU (no GPU acceleration)

**Solution:**
- Reduce `--wait` time for pipeline analysis
- In `config.py`, check GPU configuration: `gpu_devices: str = "1,0"`
- Ensure Ollama is using GPU: check `ollama ps` shows GPU utilization
- Use the smallest possible model: `qwen2:1.5b`

---

### Problem: "Rule Violation" error from orchestrator

**Possible cause:**
- Task fails pre-flight validation
- Task payload missing required fields

**Solution:**
- Check `Task` requires: `task_id`, `source`, `action`, `pipeline_id`
- Verify all required payload fields are present
- Check `captn/runtime/runtime.py` for configured rule conditions

---

### Problem: Authentication blocked from dashboard

**Possible cause:**
- `CAPTN_AUTH_PASSPHRASE` is set but you don't have it
- Or `CAPTN_AUTH_ENFORCE_LOCAL=1` is blocking local access

**Solution:**
- Check `.env` for the passphrase
- Temporarily disable auth: `unset CAPTN_AUTH_PASSPHRASE`
- Restart the dashboard

---

### Problem: Tool not found in `summon_agents.py`

**Possible cause:**
- Tool is excluded from auto-discovery (in skip list)
- Tool doesn't expose `register_cli` function
- Tool file starts with `_` (underscore)

**Solution:**
- Check `captn/cli/__init__.py` skip list — the tool may be listed there
- Verify the tool has a `register_cli(subparsers)` function
- Can't find it? Run the tool directly: `python -m tools.mytool`

---

### Problem: Token tracking not working

**Possible cause:**
- `TOKEN_USAGE` not imported in all modules that make LLM calls
- Benchmark not running

**Solution:**
- Token tracking registers in `autogen.py` and `launcher_ollama.py`
- Run benchmark to see token economy: `python -m tools.benchmark`
- Check `TOKEN_USAGE` in global scope: `python -c "from captn.workers.code_generation.autogen import TOKEN_USAGE; print(TOKEN_USAGE)"`

---

## Log File Locations

| Log | Path | Content |
|-----|------|---------|
| Runtime | `runtime.log` | Captn runtime, bus, worker activity |
| Autogen | `autogen.log` | Autogen loop iterations |
| MIA Evolution | `mia_evolution.log` | MIA debate cycle progression |
| CLI | stdout | `summon_agents.py` output |
| Crawler | `autocrawl.log` | Web crawling activity |
| Raw2JSON | `raw2json.log` | Dataset conversion progress |

---

## Debugging Tips

### Enable debug logging

```python
# In any module, set level to DEBUG
logging.basicConfig(level=logging.DEBUG)
```

Or via environment:
```bash
export LOG_LEVEL=DEBUG
# Then modify the module to read it
```

### Trace message bus activity

The `MessageBus` logs every published message:
```log
Bus: Routing task from sender to destination
```

### Trace SmartRouter decisions

```python
router = SmartRouter()
result = router.route("your task description")
print(result["selected"])
print(result["scores"])
```