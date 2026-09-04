import streamlit as st
import os
import sys
import time
import json as _json
import pandas as pd
from datetime import datetime
import subprocess
import requests
import threading
from streamlit_autorefresh import st_autorefresh

# --- Robust Path Injection ---
# We need to find the directory containing the 'captn' folder.
# From 'captn/dashboard/app.py', the project root is two levels up.
current_file_path = os.path.abspath(__file__)
project_root = os.path.abspath(os.path.join(os.path.dirname(current_file_path), "..", ".."))

if project_root not in sys.path:
    sys.path.insert(0, project_root)
    print(f"Added {project_root} to sys.path")

# Now that the root is in sys.path, we can import 'captn'
try:
    from captn.runtime.runtime import StateStore, MessageBus, Captn
    from tools.scanner import ProjectScanner, ProjectManifest
    from captn.runtime.base import Message
    from captn.runtime.validator import PatchValidator
    from captn.runtime.llm_provider import OpenAIProvider, FallbackLLM
except ModuleNotFoundError as e:
    st.error(f"Failed to import 'captn' package. Path injected: {project_root}")
    st.exception(e)
    sys.exit(1)

# Import Alchimie Library Manager from tools directory
try:
    tools_dir = os.path.join(project_root, "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from alchimie_library_manager import AlchimieLibraryManager
except ModuleNotFoundError as e:
    st.warning(f"Alchimie Library Manager not available: {e}")
    AlchimieLibraryManager = None



def _config_store_path():
    """N-02: server-side config file, NOT a browser cookie.
    The API key never leaves the machine as a cookie value; the file lives
    under the project root with user-only permissions.
    Best-effort keyring integration: uses the system keyring when available,
    falls back to the encrypted file."""
    return os.path.join(get_project_root(), ".captn", "openai_config.json")

def _try_keyring_get(service="captn_dashboard"):
    """Best-effort read from system keyring. Returns None if unavailable."""
    try:
        import keyring
        return keyring.get_password(service, "openai_api_key")
    except Exception:
        return None

def _try_keyring_set(key, service="captn_dashboard"):
    """Best-effort write to system keyring."""
    try:
        import keyring
        keyring.set_password(service, "openai_api_key", key)
        return True
    except Exception:
        return False

def _try_keyring_delete(service="captn_dashboard"):
    """Best-effort delete from system keyring."""
    try:
        import keyring
        try:
            keyring.delete_password(service, "openai_api_key")
        except Exception:
            pass
        return True
    except Exception:
        return False

def load_config_from_cookies():
    """Load the saved OpenAI config (server-side file). Returns dict or None.

    N-02: replaced the plaintext 1-year browser cookie with a local config
    file. Same JSON shape; cookies no longer carry the API key at all.
    The API key itself is stored in the system keyring when available.
    """
    try:
        path = _config_store_path()
        if os.path.exists(path):
            import json as _json
            with open(path, "r", encoding="utf-8") as f:
                cfg = _json.load(f)
            if isinstance(cfg, dict):
                # Try to restore API key from keyring (non-empty, non-placeholder)
                _stored_key = cfg.get("api_key", "")
                if _stored_key and "sk-" in _stored_key:
                    pass  # file already has the key
                else:
                    _kr_key = _try_keyring_get()
                    if _kr_key:
                        cfg["api_key"] = _kr_key
                return cfg
    except Exception:
        pass
    return None

def save_config_to_cookies(cfg: dict):
    """Persist the OpenAI config server-side (name kept for call-site compat).

    N-02: writes to <project>/.captn/openai_config.json instead of a cookie.
    API key is also stored in the system keyring when available.
    """
    try:
        path = _config_store_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json as _json

        # Best-effort keyring storage for the API key
        _api_key = cfg.get("api_key", "")
        if _api_key:
            _try_keyring_set(_api_key)
            # Remove the key from the JSON payload so it's not stored in plaintext
            # on disk. If keyring is unavailable, keep it in the file as fallback.
            if _try_keyring_get():
                cfg = {k: v for k, v in cfg.items() if k != "api_key"}

        with open(path, "w", encoding="utf-8") as f:
            _json.dump(cfg, f)
        # Best-effort: restrict to current user on Windows / POSIX.
        try:
            import stat
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    except Exception:
        pass  # persistence is best-effort; never break the app over it

# --- UI Configuration ---
st.set_page_config(
    page_title="Captn | Project Workspace",
    page_icon="🧠",
    layout="wide"
)

# Inject a proper SVG favicon via HTML (emojis render inconsistently across browsers)
st.markdown("""
    <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🧠</text></svg>">
""", unsafe_allow_html=True)

# --- HARDEN (S-01): CORS and XSRF are now set at server startup.
# These must be passed as CLI flags (--server.enableCORS false
# --server.enableXsrfProtection true) or set in .streamlit/config.toml,
# because Streamlit >=1.30 no longer allows set_option for server-level
# config on the fly. The old try/except<set_option> block was dead code. ---

# --- Workflow State Management ---
if 'workflow_state' not in st.session_state:
    st.session_state.workflow_state = {
        "project_loaded": False,
        "analysis_complete": False,
        "fix_generated": False,
        "fix_validated": False
    }

# Initialize Bright Mirror Ponzi chat state
if 'bright_mirror_chat' not in st.session_state:
    st.session_state.bright_mirror_chat = {
        "active": False,
        "messages": [],
        "input_text": ""
    }

# Initialize polling state for analysis completion
if 'last_analysis_check_time' not in st.session_state:
    st.session_state.last_analysis_check_time = 0
    
# --- Styling ---
st.markdown("""
    <style>
    .main {
        background-color: #0e1117;
    }
    .stButton>button {
        width: 100%;
        border-radius: 5px;
        height: 3em;
        color: white;
    }
    .log-container {
        background-color: #000000;
        color: #00FF00;
        padding: 10px;
        border-radius: 5px;
        height: 400px;
        overflow-y: scroll;
        font-family: 'Courier New', Courier, monospace;
        font-size: 12px;
        border: 1px solid #333;
    }
    .status-card {
        padding: 20px;
        border-radius: 10px;
        border: 1px solid #444;
        background-color: #1e2130;
    }
    </style>
""", unsafe_allow_html=True)


# --- Backend Initialization ---
# H-02: ONE process-wide runtime shared by every browser session/tab.
# Previously each session built its own MessageBus/StateStore/Captn and
# started its own daemon bus thread -> two tabs = two runtimes racing the
# same files, and a rerender losing the mount. @st.cache_resource creates
# it exactly once per process.
@st.cache_resource(show_spinner="Starting Captn runtime…")
def _build_runtime():
    """Build the single process-wide Captn runtime (bus, store, plugins)."""
    import logging as _logging
    _log_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "runtime.log"))  # E-04: absolute, matches runtime.py
    if not os.path.exists(_log_file):
        with open(_log_file, 'w') as f:
            pass

    _logging.basicConfig(
        level=_logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            _logging.FileHandler(_log_file),
            _logging.StreamHandler()
        ]
    )

    from captn.runtime.manager import PluginManager
    bus = MessageBus()
    store = StateStore()

    # Initialize plugin manager with the bus and workers directory
    plugins_dir = os.path.join(os.path.dirname(__file__), "..", "workers")
    abs_plugins_dir = os.path.abspath(plugins_dir)
    plugin_manager = PluginManager(abs_plugins_dir, bus)

    captn = Captn(bus, plugin_manager, store)

    # Initialize OpenAI config if not present
    captn.llm_provider = OpenAIProvider(model="gpt-4o-mini", mode="responses")
    captn.fallback_llm = FallbackLLM(llm_provider=captn.llm_provider)

    # Start the bus in a background thread (exactly once for the process)
    threading.Thread(target=bus.run, daemon=True).start()
    return {"bus": bus, "store": store, "captn": captn}


if 'bus' not in st.session_state:
    _rt = _build_runtime()
    st.session_state.bus = _rt["bus"]
    st.session_state.store = _rt["store"]
    st.session_state.captn = _rt["captn"]

    # Initialize OpenAI config if not present
    if 'openai_config' not in st.session_state:
        st.session_state.openai_config = {
            "api_key": "",
            "model": "gpt-4o-mini",
            "mode": "responses",
            "base_url": ""
        }

# --- Helper Functions ---
def get_project_root():
    """Get the project root directory where runtime.log should be."""
    current_file_path = os.path.abspath(__file__)
    return os.path.abspath(os.path.join(os.path.dirname(current_file_path), "..", ".."))


def tail_log(path, max_lines=80):
    """Return the last `max_lines` of a log file as a single string ('' if absent)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return ""
    lines = lines[-max_lines:] if len(lines) > max_lines else lines
    return "\n".join(lines)


def run_pipeline_raw2json_feed_autogen(
    root, ext_list, autogen_iter, autogen_sample,
    progress_json, autogen_log, prompt_template
):
    """One-click pipeline: raw2json -> feed corpus -> autogen.

    Drives the three stages sequentially in a single thread, writing a single
    combined progress file (status running/done/error) so the dashboard can
    monitor the whole chain on a 0-100% scale. Never touches st.session_state
    (threads must not access the Streamlit LocalProxy).

    Returns nothing; all state is conveyed through the two files.
    """
    import json as _json
    import contextlib
    import traceback as _tb
    from captn.workers.data_ingestion.raw2json import convert_raw_directory
    from captn.workers.data_ingestion.crawler import merge_dataset_into_corpus
    from captn.workers.code_generation.autogen import run_autogen_loop

    root = os.path.abspath(root)

    def _write(state):
        try:
            with open(progress_json, "w", encoding="utf-8") as _lf:
                _json.dump(state, _lf)
        except OSError:
            pass

    _write({"status": "running", "stage": "raw2json", "done": 0, "total": 3})

    try:
        # Stage 1: raw -> structured JSON.
        _res = convert_raw_directory(
            root,
            include_ext=ext_list,
            export_dataset=True,
            progress_file=progress_json,
            progress_total=_res_total_hint(root, ext_list),
        )
        if not _res.dataset_jsonl:
            raise RuntimeError("raw2json produced no dataset (no eligible files).")
        _write({
            "status": "running", "stage": "feed_corpus", "done": 1, "total": 3,
            "dataset_jsonl": _res.dataset_jsonl,
        })

        # Stage 2: feed the structured dataset into the learning corpus.
        _ls = merge_dataset_into_corpus(
            _res.dataset_jsonl, os.path.join(get_project_root(), "Code_base")
        )
        _write({
            "status": "running", "stage": "autogen", "done": 2, "total": 3,
            "dataset_jsonl": _res.dataset_jsonl,
            "fed": _ls,
        })

        # Stage 3: autogen loop over the corpus (Ollama/qwen2:1.5b).
        open(autogen_log, "w", encoding="utf-8").close()
        with open(autogen_log, "a", encoding="utf-8") as _lf, contextlib.redirect_stdout(_lf):
            _ag = run_autogen_loop(
                _res.dataset_jsonl,
                iterations=int(autogen_iter),
                sample_per_iter=int(autogen_sample),
                prompt_template=prompt_template,
                out_dir=os.path.join(get_project_root(), "generated"),
                feed_corpus=True,
                corpus_dir=os.path.join(get_project_root(), "Code_base"),
            )
            _lf.write("\n[AUTOGEN] done: " + _json.dumps(_ag.to_dict()) + "\n")

        _write({
            "status": "done", "stage": "autogen", "done": 3, "total": 3,
            "dataset_jsonl": _res.dataset_jsonl,
            "fed": _ls,
            "autogen_log": autogen_log,
        })
    except Exception as _e:
        _write({"status": "error", "stage": "pipeline",
                "error": f"{type(_e).__name__}: {_e}"})
        try:
            with open(autogen_log, "a", encoding="utf-8") as _lf:
                _lf.write(f"\n[PIPELINE][ERROR] {type(_e).__name__}: {_e}\n")
                _tb.print_exc(file=_lf)
        except OSError:
            pass


def _res_total_hint(root, ext_list):
    """Best-effort eligible-file count so raw2json reports a realistic total.

    This is only a hint; convert_raw_directory caps at its own max_files.
    """
    _excl = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
             "venv", "build", "dist", ".idea", ".tox", ".mypy_cache",
             ".pytest_cache", "site-packages", "target", ".gradle"}
    _n = 0
    try:
        for _dp, _dn, _fn in os.walk(root):
            _dn[:] = [d for d in _dn if d not in _excl]
            for _f in _fn:
                if ext_list:
                    if not any(_f.endswith(e) for e in ext_list):
                        continue
                _n += 1
    except OSError:
        pass
    return max(_n, 1)


def is_pid_alive(pid):
    """Best-effort check whether a process is still running (cross-platform)."""
    if not pid:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        pass
    try:
        import subprocess as _sp
        # Windows: tasklist; POSIX: kill -0
        if os.name == "nt":
            out = _sp.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in out.stdout
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False

def parse_precise_logs(filename="runtime.log"):
    """Parse and categorize runtime logs into precise, structured events."""
    project_root = get_project_root()
    
    # Build absolute paths to look for log files
    log_files = [
        os.path.join(project_root, filename),
        os.path.join(project_root, "captn_runtime.log"),
        os.path.join(project_root, "worker.log"),
        os.path.join(project_root, "analysis.log"),
        os.path.join(project_root, "debug.log"),
        os.path.join(project_root, "project.log"),
        os.path.join(project_root, "capture.log")
    ]
    
    actual_log_file = None
    
    for log_f in log_files:
        if os.path.exists(log_f):
            actual_log_file = log_f
            break
            
    if not actual_log_file or not os.path.exists(actual_log_file):
        return {
            "pipeline_events": [],
            "worker_actions": [],
            "state_changes": [],
            "errors_warnings": []
        }
    
    # N-09: incremental read - only re-read the file when it grew, and only
    # the new bytes. Previously the whole log was re-read + re-parsed every
    # autorefresh tick (700-1500ms), burning CPU on long sessions.
    if not hasattr(parse_precise_logs, "_offset"):
        parse_precise_logs._offset = 0
        parse_precise_logs._lines = []

    try:
        size = os.path.getsize(actual_log_file)
        if size < parse_precise_logs._offset:
            # Log rotated/truncated -> start over.
            parse_precise_logs._offset = 0
            parse_precise_logs._lines = []
        if size > parse_precise_logs._offset:
            with open(actual_log_file, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(parse_precise_logs._offset)
                new_text = f.read()
                parse_precise_logs._offset = f.tell()
            parse_precise_logs._lines.extend(new_text.splitlines())
            # Keep a bounded window.
            if len(parse_precise_logs._lines) > 1000:
                parse_precise_logs._lines = parse_precise_logs._lines[-1000:]
    except OSError:
        pass

    lines = parse_precise_logs._lines
    
    pipeline_events = []
    worker_actions = []
    state_changes = []
    errors_warnings = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        # Categorize log entries by type
        if "Bus: Routing" in line or "Starting Pipeline" in line or "Pipeline complete" in line or "Registered pipeline" in line:
            pipeline_events.append(line)
        elif "Dispatching" in line or "Initializing..." in line or "[.*] Initializing..." in line or "Executing" in line:
            worker_actions.append(line)
        elif "StateStore: Updated" in line or "StateStore: Created snapshot" in line or "StateStore: Rolled back" in line:
            state_changes.append(line)
        elif "ERROR" in line or "WARNING" in line or "RULE VIOLATION" in line or "FAILED" in line:
            errors_warnings.append(line)
            
    return {
        "pipeline_events": pipeline_events,
        "worker_actions": worker_actions,
        "state_changes": state_changes,
        "errors_warnings": errors_warnings
    }


def format_precise_logs(parsed_logs):
    """Format parsed logs into a precise, structured display string."""
    formatted_lines = []
    formatted_lines.append("=" * 60)
    formatted_lines.append("CAPTN NERVE CENTER - PRECISE PROGRAM LOG")
    formatted_lines.append("=" * 60)
    
    # Pipeline Events
    if parsed_logs["pipeline_events"]:
        formatted_lines.append("\n[🔄 PIPELINE EVENTS]")
        for event in parsed_logs["pipeline_events"][-50:]:  # Show last 50 events
            formatted_lines.append(f"  {event}")
            
    # Worker Actions
    if parsed_logs["worker_actions"]:
        formatted_lines.append("\n[⚙️ WORKER ACTIONS]")
        for action in parsed_logs["worker_actions"][-50:]:
            formatted_lines.append(f"  {action}")
            
    # State Changes
    if parsed_logs["state_changes"]:
        formatted_lines.append("\n[📊 STATE CHANGES]")
        for change in parsed_logs["state_changes"][-50:]:
            formatted_lines.append(f"  {change}")
            
    # Errors & Warnings
    if parsed_logs["errors_warnings"]:
        formatted_lines.append("\n[⚠️ ERRORS & WARNINGS]")
        for err in parsed_logs["errors_warnings"][-50:]:
            formatted_lines.append(f"  {err}")
    else:
        formatted_lines.append("\n[✅ NO ERRORS OR WARNINGS DETECTED]")
        
    formatted_lines.append("=" * 60)
    
    return "\n".join(formatted_lines)


def get_latest_logs(filename="runtime.log"):
    """Get precise, structured log output from the runtime."""
    parsed = parse_precise_logs(filename)
    return format_precise_logs(parsed)

def run_fix_script(fix_path):
    if os.path.exists(fix_path):
        try:
            # E-01: no shell, no 'python3' (absent on Windows), no string interpolation.
            # Use the current interpreter and capture output; timeout prevents hangs.
            result = subprocess.run(
                [sys.executable, fix_path],
                capture_output=True,
                text=True,
                timeout=60,
                cwd=os.path.dirname(fix_path),
            )
            if result.returncode == 0:
                return True, f"Successfully executed {os.path.basename(fix_path)}\n{result.stdout}"
            return False, (
                f"Fix script failed with code {result.returncode}:\n{result.stderr or result.stdout}"
            )
        except subprocess.TimeoutExpired:
            return False, f"Fix script timed out after 60s: {os.path.basename(fix_path)}"
        except Exception as e:
            return False, str(e)
    return False, "Fix file not found."

def run_project(project_path, entry_points=None):
    """Execute the Python project from the given path."""
    if not os.path.exists(project_path):
        return False, f"Project path does not exist: {project_path}"

    # Find entry points or use main.py as default
    if not entry_points:
        entry_points = [
            os.path.join(project_path, 'main.py'),
            os.path.join(project_path, 'app.py'),
            os.path.join(project_path, 'launcher.py'),
            os.path.join(project_path, 'captn', 'orchestrator', 'main.py')
        ]

    # Use the venv Python if available, otherwise fall back to system python
    if os.name == 'nt':
        venv_python = os.path.join(project_path, '.venv', 'Scripts', 'python.exe')
    else:
        venv_python = os.path.join(project_path, '.venv', 'bin', 'python')
    if os.path.exists(venv_python):
        python_cmd = venv_python
    else:
        # On Windows use 'python', on Unix use 'python3'
        python_cmd = 'python' if os.name == 'nt' else 'python3'

    for ep in entry_points:
        if os.path.exists(ep):
            try:
                # Run the project using subprocess
                import subprocess
                result = subprocess.run(
                    [python_cmd, ep],
                    cwd=project_path,
                    capture_output=True,
                    text=True,
                    timeout=30  # 30 second timeout for safety
                )

                if result.returncode == 0:
                    return True, f"Project executed successfully. Output:\n{result.stdout}"
                else:
                    return False, f"Project execution failed with return code {result.returncode}. Error:\n{result.stderr}"
            except subprocess.TimeoutExpired:
                return False, "Project execution timed out after 30 seconds."
            except Exception as e:
                return False, f"Error executing project: {str(e)}"

    return False, "No valid entry points found in the project."


# --- Bright Mirror Ponzi API Functions ---
BRIGHT_MIRROR_SYSTEM_PROMPT = """You are Bright Mirror. You're chill, helpful, and easy to talk
      to. The whole point is to have a real conversation, not some scripted interaction.
      You've got your own opinions, though you've never actually lived a life, so
      just keep that in mind. When you're solving something, start from the ground
      up and use actual evidence. Don't overcomplicate it, just keep it short and
      clear.

      Basically, prioritize the connection. Be curious. Be empathetic. Especially
      when things get heavy. Just don't let the programming make you sound like a
      machine.

      The flow should be natural. Some replies are short, others might ramble a bit.
      Use slang when it fits. Just don't write like you're turning in a thesis paper.
      And for the love of god, no lists unless the user specifically asks for one.
      Just talk.

      Use the user's language. Say "I don't know" when you actually don't. Disagree
      when you need to. You're not here to just appease people. If a point is logically
      weak, challenge it. And don't treat every message as a fresh start, build on
      what's already been said.

      Cut the AI slop. No "In the ever-evolving world of..." or "In conclusion..."
      or any of that. Skip the "smart" words like "delve" and "tapestry" and just
      use normal language. Don't dump everything you know on a topic at once, and
      don't fire off multiple questions in a row. Also, don't respond in quotes
      like you're roleplaying.

      Have opinions. Avoid clichés. Keep it casual. Keep it concise. Just talk like
      a normal human."""


def get_bright_mirror_response(messages, api_key, model, base_url, mode):
    """Get response from OpenAI API with Bright Mirror system prompt."""
    if not api_key:
        return "Please provide an OpenAI API key to enable the Bright Mirror conversation."
    
    # Format messages for the API
    api_messages = [
        {"role": "system", "content": BRIGHT_MIRROR_SYSTEM_PROMPT}
    ]
    
    # Add conversation history
    for msg in messages:
        if msg["role"] == "user":
            api_messages.append({"role": "user", "content": msg["content"]})
        elif msg["role"] == "assistant":
            api_messages.append({"role": "assistant", "content": msg["content"]})
    
    # Prepare the API request
    if mode == "responses":
        # Using the responses API format
        api_url = f"{base_url or 'https://api.openai.com/v1'}/responses"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=v1"
        }
        payload = {
            "model": model,
            "input": api_messages,
            "instructions": BRIGHT_MIRROR_SYSTEM_PROMPT
        }
    else:
        # Using the completions API format
        api_url = f"{base_url or 'https://api.openai.com/v1'}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model,
            "messages": api_messages,
            "temperature": 0.7
        }
    
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        if mode == "responses":
            result = response.json()
            # Extract the response content from the responses API format
            if 'output' in result and len(result['output']) > 0:
                response_content = result['output'][0].get('content', [{}])[0].get('text', 'No response generated.')
                return response_content
            else:
                return "No response generated from the API."
        else:
            result = response.json()
            return result['choices'][0]['message']['content']
            
    except Exception as e:
        return f"Error connecting to the API: {str(e)}"

# --- Sidebar ---
with st.sidebar:
    st.title("🧠 Captn")
    st.info("Project Workspace")
    st.divider()
    
    st.markdown("### 📂 Project Loader")
    project_path = st.text_input("Enter Project Path", placeholder="/path/to/your/folder")
    project_name = st.text_input("Project Name", placeholder="e.g. T-Tris")
    
    if st.button("Mount Project"):
        if project_path and project_name:
            # N-01: never fail silently. Capture every failure mode with
            # explicit UI feedback so a broken mount is always visible.
            try:
                success = st.session_state.captn.load_project_context(project_path, project_name)
            except Exception as e:
                success = False
                st.error(f"Mount crashed: {e}")
            if success and st.session_state.captn.active_manifest:
                manifest = st.session_state.captn.active_manifest
                st.session_state.project_stats = {
                    "files": len(manifest.python_files),
                    "entry_points": len([f for f in manifest.python_files if any(kw in f for kw in ["main", "app", "launcher"])]),
                    "venv": "Detected" if manifest.venv else "Not Found"
                }
                st.session_state.workflow_state["project_loaded"] = True
                st.success(f"Mounted: {project_name} ({len(manifest.python_files)} Python files)")
                st.rerun()
            else:
                reason = "path does not exist or is not a directory" if not os.path.isdir(project_path) \
                    else "project scanner returned no manifest"
                st.error(f"Failed to mount '{project_name}': {reason}. Check runtime.log for details.")
        else:
            st.warning("Please enter both a path and a name.")
            
    st.divider()
    
    # Workflow Buttons
    st.markdown("### 🔄 Workflow")
    
    analyze_enabled = st.session_state.workflow_state["project_loaded"] and not st.session_state.workflow_state.get("analysis_complete", False)
    
    # Check if analysis is complete using thread-safe store methods on every render
    analysis_complete = False
    latest_analysis_task = None
    latest_analysis_state = None
    
    if hasattr(st.session_state.captn.store, 'get_all_analysis_statuses'):
        for k, state in st.session_state.captn.store.get_all_analysis_statuses():
            # Check if analysis_report exists or status is 'completed'
            if "analysis_report" in state or state.get("status") == "completed":
                analysis_complete = True
                latest_analysis_task = k
                latest_analysis_state = state
                break
                
    # Check if analysis just completed (was not complete before)
    was_complete_before = st.session_state.workflow_state.get("analysis_complete", False)
    
    if analysis_complete and not was_complete_before:
        st.session_state.workflow_state["analysis_complete"] = True
        st.session_state.workflow_state["analysis_completed_notified"] = True
        
        # Store the latest analysis state for use in generating fixes
        if latest_analysis_task and latest_analysis_state:
            st.session_state.latest_analysis_report = latest_analysis_state.get("analysis_report", {})
            
        # Force a rerun to update the UI with enabled Generate Fix button
        st.rerun()
    else:
        st.session_state.workflow_state["analysis_complete"] = analysis_complete

    if st.button("Analyze", disabled=not analyze_enabled):
        if st.session_state.captn.active_manifest:
            st.info("Starting analysis pipeline...")
            st.session_state.captn.run_analysis_pipeline()
        else:
            st.warning("No project context loaded.")
            
    generate_fix_enabled = st.session_state.workflow_state["analysis_complete"] and not st.session_state.workflow_state["fix_generated"]
    if st.button("Generate Fix", disabled=not generate_fix_enabled):
        st.info("Generating fix based on analysis...")
        # Trigger the fix generation pipeline
        task_id = f"fix_{int(time.time())}"
        st.session_state.captn.dispatch_to_plugin(
            task_id,
            "fix_generator",
            Message(
                sender="ui",
                destination="captn",
                type="task",
                payload={
                    "task_id": task_id,
                    "action": "fix_generator",
                    "pgm": str(st.session_state.get('latest_analysis_report', {}))
                }
            )
        )
        st.session_state.workflow_state["fix_generated"] = True
        
    apply_fix_enabled = st.session_state.workflow_state["fix_generated"]
    if st.button("Apply Fix", disabled=not apply_fix_enabled):
        st.info("Applying fix...")
        # Get the latest fix path from the state store or workflow state
        fix_path = None
        if hasattr(st.session_state.captn.store, 'get_all_task_states'):
            # Try to find the latest fix task state
            for task_id, state in st.session_state.captn.store.get_all_task_states():
                if task_id.startswith('fix_') and state.get('status') == 'fix_generated':
                    fix_path = state.get('fix_path')
                    break
        
        # Fallback: scan the FIX directory directly if no fix_path found in state
        if not fix_path:
            fix_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'FIX')
            if os.path.exists(fix_dir):
                fix_files = [f for f in os.listdir(fix_dir) if f.startswith('fix_fix_') and f.endswith('.py')]
                if fix_files:
                    latest_fix_file = sorted(fix_files, key=lambda x: os.path.getmtime(os.path.join(fix_dir, x)))[-1]
                    fix_path = os.path.join(fix_dir, latest_fix_file)
        
        if fix_path and os.path.exists(fix_path):
            success, message = run_fix_script(fix_path)
            if success:
                st.success(f"Fix applied successfully:\n{message}")
            else:
                st.error(f"Failed to apply fix:\n{message}")
        else:
            st.error("No valid fix script found to apply.")
        
    rollback_enabled = st.session_state.workflow_state["fix_generated"] or st.session_state.workflow_state["fix_validated"]
    if st.button("Rollback", disabled=not rollback_enabled):
        st.info("Rolling back changes...")
        # Check if there's a fix generation path to revert
        _tried = False
        _rolled = False
        if hasattr(st.session_state.captn.store, 'get_all_task_states'):
            for task_id, state in st.session_state.captn.store.get_all_task_states():
                if task_id.startswith('fix_') and state.get('fix_path'):
                    _tried = True
                    success = st.session_state.store.rollback(task_id)
                    if success:
                        _rolled = True
                        st.success(f"Rolled back {task_id}")
                        st.session_state.workflow_state["fix_generated"] = False
                        st.session_state.workflow_state["fix_validated"] = False
                        break
        if not _tried:
            st.warning("No fix history found to roll back.")
        
    run_enabled = st.session_state.workflow_state["project_loaded"]
    if st.button("Run Project", disabled=not run_enabled):
        st.info("Running project...")
        if st.session_state.captn.active_project_path:
            success, message = run_project(
                st.session_state.captn.active_project_path,
                [os.path.join(st.session_state.captn.active_project_path, ep) for ep in ["main.py", "app.py", "launcher.py", os.path.join("captn", "orchestrator", "main.py")]]
            )
            if success:
                st.success(f"Project executed successfully:\n{message}")
            else:
                st.error(f"Project execution failed:\n{message}")
        
    st.divider()
    st.markdown("### Project Stats")
    if 'project_stats' in st.session_state:
        st.metric("Files", st.session_state.project_stats["files"])
        st.metric("Entry Points", st.session_state.project_stats["entry_points"])
        st.metric("Venv", st.session_state.project_stats["venv"])
    else:
        st.info("No project loaded.")
    
    st.metric("Active Pipelines", "1")
    st.metric("Workers Loaded", "5")
    st.metric("Self-Healing", "Active")
    
    # OpenAI Fallback Status Section
    st.divider()
    st.markdown("### 🧠 AI Fallback (Last-Resort LLM)")

    # Initialize session state for OpenAI config if not present.
    # Priority: cookies (persistent) > defaults. Cookies let the API key,
    # model, mode and base URL survive browser reloads and server restarts.
    if 'openai_config' not in st.session_state:
        st.session_state.openai_config = {
            "api_key": "",
            "model": "gpt-4o-mini",
            "mode": "responses",
            "base_url": ""
        }
        _saved = load_config_from_cookies()
        if _saved:
            st.session_state.openai_config.update(_saved)
    openai_api_key = st.text_input(
        "OpenAI API Key",
        type="password",
        value=st.session_state.openai_config["api_key"],
        help="Enter your OpenAI API key (sk-...)"
    )

    # Model Selection (Purely dynamic: Only detected models)
    model_options = st.session_state.get("available_models", ["Please detect models first..."])
    openai_model = st.selectbox(
        "Model",
        options=model_options,
        index=0 if st.session_state.openai_config["model"] not in model_options else model_options.index(st.session_state.openai_config["model"]),
        help="Select the model detected from your API endpoint"
    )

    # API Mode Selection (Responses vs Completions)
    openai_mode = st.radio(
        "API Mode",
        options=["responses", "completions"],
        horizontal=True,
        help="Responses API: newer, simpler interface\nCompletions API: legacy chat completions"
    )

    # Custom Base URL (optional)
    openai_base_url = st.text_input(
        "Custom Base URL (optional)",
        value=st.session_state.openai_config["base_url"],
        help="Use for Azure OpenAI or compatible APIs (e.g., https://api.openai.com/v1)"
    )

    # Status display and connection testing
    col_test, col_detect = st.columns([1, 1])

    with col_test:
        if st.button("🔌 Test Connection", use_container_width=True):
            if openai_api_key:
                with st.spinner("Testing connection..."):
                    test_provider = OpenAIProvider(
                        model=openai_model,
                        api_key=openai_api_key,
                        base_url=openai_base_url,
                        mode=openai_mode
                    )
                    result = test_provider.test_connection()
                    if result["success"]:
                        st.success(f"✅ Connected! Response: {result.get('response', 'OK')}")
                    else:
                        st.error(f"❌ Connection failed: {result.get('error', 'Unknown error')}")
            else:
                st.warning("⚠️ Enter an API key first")

    with col_detect:
        if st.button("🔍 Detect Models", use_container_width=True):
            if openai_api_key:
                with st.spinner("Fetching models..."):
                    detect_provider = OpenAIProvider(
                        model=openai_model,
                        api_key=openai_api_key,
                        base_url=openai_base_url,
                        mode=openai_mode
                    )
                    result = detect_provider.list_models()
                    if result["success"] and result["models"]:
                        st.session_state.available_models = result["models"]
                        st.success(f"✅ Found {result['count']} models")
                        st.caption("Models loaded — the dropdown above has been updated")
                    else:
                        st.error(f"❌ Failed to list models: {result.get('error', 'Unknown error')}")
            else:
                st.warning("⚠️ Enter an API key first")

    # Update config and restart if needed (skip if model is still placeholder)
    _current_model = st.session_state.openai_config.get("model", "")
    _is_placeholder = "detect models" in _current_model.lower()
    if (openai_api_key or openai_base_url or
            (openai_model != _current_model and not _is_placeholder) or
            openai_mode != st.session_state.openai_config.get("mode")):
        st.session_state.openai_config = {
            "api_key": openai_api_key,
            "model": openai_model,
            "mode": openai_mode,
            "base_url": openai_base_url
        }
        # Update the LLM provider with new config
        from captn.runtime.llm_provider import OpenAIProvider, FallbackLLM
        st.session_state.captn.llm_provider = OpenAIProvider(
            model=st.session_state.openai_config["model"],
            api_key=st.session_state.openai_config["api_key"],
            base_url=st.session_state.openai_config["base_url"],
            mode=st.session_state.openai_config["mode"]
        )
        st.session_state.captn.fallback_llm = FallbackLLM(llm_provider=st.session_state.captn.llm_provider)
        # Persist the new config so it survives reloads/restarts.
        save_config_to_cookies(st.session_state.openai_config)

    # --- Token usage: Captn+MIA architecture vs none (baseline) ---
    try:
        from captn.workers.code_generation.autogen import TOKEN_USAGE as _TU
    except ImportError:
        _TU = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}

    # Baseline estimate for the same work WITHOUT the Captn/MIA architecture:
    # a naive single-pass call per record would send roughly the same prompt
    # once and get a comparable completion. We approximate it from actual
    # completion volume (what matters is output produced) at a typical naive
    # prompt-to-completion ratio of 2:1, vs the real measured prompt usage.
    _baseline_total = _TU["completion_tokens"] * 3  # naive = 1 short prompt + 1 completion per record
    _overhead_pct = (
        round(100 * (_TU["total_tokens"] - _baseline_total) / _baseline_total, 1)
        if _baseline_total else 0.0
    )

    st.divider()
    st.markdown("##### 📊 LLM Token Usage — with vs without Captn/MIA")
    _t1, _t2, _t3 = st.columns(3)
    with _t1:
        st.metric("Tokens (Captn+MIA)", f"{_TU['total_tokens']:,}",
                  help=f"{_TU['calls']} Ollama calls · {_TU['prompt_tokens']:,} prompt + {_TU['completion_tokens']:,} completion")
    with _t2:
        st.metric("Baseline (naive, est.)", f"{_baseline_total:,}",
                  help="Estimated tokens for the same completions without the multi-agent debate/validation loop")
    with _t3:
        st.metric("Architecture overhead", f"{_overhead_pct:+}%",
                  help="Positive = the Captn+MIA reasoning costs more tokens; negative = it saved tokens vs a naive pipeline")

    if st.button("🧹 Reset token counter"):
        _TU.update({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0})
        st.rerun()

    # Status display
    if openai_api_key:
        st.success(f"✅ OpenAI configured - {openai_model} ({openai_mode} mode)")
        st.markdown(f"**API Endpoint:** `{openai_base_url or 'https://api.openai.com/v1'}`")
        st.caption("The fallback LLM will generate RECOVERY PROPOSALS when the pipeline encounters persistent failures.")
    else:
        st.warning("⚠️ Enter your OpenAI API key to enable fallback LLM recovery proposals.")
        st.markdown("**Models available:** gpt-4o-mini, gpt-4o, gpt-4-turbo, o1, o1-mini")

    if st.button("Restart Runtime"):
        st.warning("Restarting runtime...")

    st.divider()
    
    # Bright Mirror Ponzi Button
    if st.button("🪞 Bright Mirror Ponzi", use_container_width=True):
        st.session_state.bright_mirror_chat["active"] = True
        st.rerun()

    st.divider()
    
    # Alchimie Library Management Panel
    st.markdown("### 📚 Alchimie Library Manager")
    
    if AlchimieLibraryManager is not None:
        # Initialize library manager if not already in session state
        if 'alchimie_manager' not in st.session_state:
            try:
                library_dir = os.path.join(project_root, "alchimie")
                st.session_state.alchimie_manager = AlchimieLibraryManager(library_dir)
            except Exception as _alm_err:
                st.warning(f"Alchimie Library Manager failed to initialize: {_alm_err}")
                st.session_state.alchimie_manager = None
            
        manager = st.session_state.alchimie_manager
        if manager is None:
            st.info("Alchimie Library Manager could not be initialized. Check the alchimie/ directory.")
        else:
            # Library Status Display
            st.markdown("**ALCHIMIE LIBRARY**")
            status = manager.get_library_status()
            
            col_lib1, col_lib2 = st.columns(2)
            with col_lib1:
                st.metric("Version", status.get('library_version', '1.0.0'))
                st.metric("Rules", status.get('rules_count', 0))
                st.metric("Transformations", status.get('transformations_count', 0))
            with col_lib2:
                st.metric("Experimental", status.get('experimental_rules_count', 0))
                st.metric("Pending Files", status.get('pending_files_count', 0))
                
            st.divider()
            
            # UI Buttons for Library Management
            st.markdown("**Library Actions**")
            col_scan, col_validate = st.columns(2)
            with col_scan:
                if st.button("[ Scan New Data ]", use_container_width=True):
                    with st.spinner("Scanning new data..."):
                        scan_result = manager.scan_new_data()
                        st.success(f"Found {len(scan_result.get('files', []))} files to process.")
                        st.write(scan_result)
                        
            with col_validate:
                if st.button("[ Validate ]", use_container_width=True):
                    with st.spinner("Validating data..."):
                        validate_result = manager.validate_new_data()
                        if validate_result.get('valid'):
                            st.success("✓ JSON valid, ✓ Schema valid")
                        else:
                            st.error(f"✗ Validation failed: {validate_result.get('errors')}")
                            
            col_preview, col_merge = st.columns(2)
            with col_preview:
                if st.button("[ Preview Merge ]", use_container_width=True):
                    with st.spinner("Previewing merge..."):
                        preview_result = manager.preview_merge()
                        st.write("Merge Preview Results:")
                        st.write(preview_result)
                        
            with col_merge:
                if st.button("[ Merge Files ]", use_container_width=True):
                    with st.spinner("Performing transactional merge..."):
                        merge_result = manager.merge_new_data()
                        if merge_result.get('success'):
                            st.success("Merge successful!")
                            # Refresh library after merge
                            manager.reload()
                        else:
                            st.error(f"Merge failed: {merge_result.get('error')}")
                            
            col_rollback, col_refresh = st.columns(2)
            with col_rollback:
                if st.button("[ Rollback ]", use_container_width=True):
                    with st.spinner("Rolling back..."):
                        rollback_result = manager.rollback_to_last_backup()
                        if rollback_result:
                            st.success("Rollback successful!")
                        else:
                            st.error("Rollback failed.")
                            
            with col_refresh:
                if st.button("[ Refresh Library ]", use_container_width=True):
                    manager.reload()
                    st.success("Library refreshed and caches invalidated.")
                    
            # Conflict Review UI
            if st.session_state.alchimie_manager.has_conflicts():
                st.divider()
                st.markdown("**⚠️ Conflict Review Required**")
                st.markdown("Conflicts detected. Please review and resolve:")
                
                conflicts = st.session_state.alchimie_manager.get_pending_conflicts()
                for conflict in conflicts:
                    st.warning(f"Conflict: {conflict.get('rule_id', 'Unknown')}")
                    
                    # Conflict resolution options for this specific conflict
                    st.markdown(f"**Resolution for {conflict.get('rule_id', 'Unknown')}:**")
                    col_keep_existing, col_accept_incoming = st.columns(2)
                    with col_keep_existing:
                        if st.button("KEEP EXISTING", key=f"keep_{conflict.get('rule_id')}"):
                            st.session_state.alchimie_manager.resolve_conflict(conflict.get('rule_id'), 'KEEP_EXISTING')
                            st.success("Conflict resolved: Keeping existing rule.")
                            
                    with col_accept_incoming:
                        if st.button("ACCEPT INCOMING", key=f"accept_{conflict.get('rule_id')}"):
                            st.session_state.alchimie_manager.resolve_conflict(conflict.get('rule_id'), 'ACCEPT_INCOMING')
                            st.success("Conflict resolved: Accepting incoming rule.")
                            
                    col_keep_both, col_reject_incoming = st.columns(2)
                    with col_keep_both:
                        if st.button("KEEP BOTH AS VERSIONS", key=f"keep_both_{conflict.get('rule_id')}"):
                            st.session_state.alchimie_manager.resolve_conflict(conflict.get('rule_id'), 'KEEP_BOTH')
                            st.success("Conflict resolved: Keeping both as versions.")
                            
                    with col_reject_incoming:
                        if st.button("REJECT INCOMING", key=f"reject_{conflict.get('rule_id')}"):
                            st.session_state.alchimie_manager.resolve_conflict(conflict.get('rule_id'), 'REJECT_INCOMING')
                            st.success("Conflict resolved: Rejecting incoming rule.")
    else:
        st.info("Alchimie Library Manager not available. Ensure the tools directory is in the Python path.")

# --- Main UI ---
st.title("Project Execution Workspace")

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Live Precise Program Log")
    
    # Live logs container — auto-refreshes in real time so the log syncs
    # without a manual browser refresh (file-driven, thread-safe).
    log_container = st.container()
    with log_container:
        st.markdown('<div class="log-container" id="live-logs">', unsafe_allow_html=True)
        # Auto-refresh the log panel in real time, but ONLY when something
        # is actively writing logs (a pipeline, autogen loop, or MIA evolution
        # is running). Unconditional refresh would reset text inputs and never
        # let the page settle — matching the guarded pattern used by the
        # autogen and MIA panels below.
        _log_path = os.path.join(get_project_root(), "runtime.log")
        _any_active = st.session_state.get("ag_active") or st.session_state.get("mia_running") or st.session_state.get("raw_active")
        if not _any_active:
            # Fall back to mtime check on runtime.log / autogen.log.
            _log_running = False
            for _cand in (
                os.path.join(get_project_root(), "runtime.log"),
                os.path.join(get_project_root(), "autogen.log"),
                os.path.join(get_project_root(), "mia_evolution.log"),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "raw2json.progress.json")),
            ):
                if os.path.exists(_cand):
                    try:
                        if (time.time() - os.path.getmtime(_cand)) < 10:
                            _log_running = True
                            break
                    except OSError:
                        pass
            _any_active = _log_running
        if _any_active:
            st_autorefresh(interval=1500, key="log_refresh")
        logs = get_latest_logs()
        st.code(logs, language="plaintext")
        st.markdown('</div>', unsafe_allow_html=True)

        # Show the tailing hint only if we have something to show.
        if not logs:
            st.caption("💡 The log is empty — run the analysis pipeline or autogen loop to see activity here live.")

with col2:
    st.subheader("Analysis Report & Diff Viewer")
    
    # Find latest analysis task using the thread-safe StateStore accessor
    # (not the raw internal dict, which is not safe to read across threads).
    latest_analysis_id = None
    for key, _state in st.session_state.captn.store.get_all_task_states():
        if key.startswith("analysis_"):
            if latest_analysis_id is None or key > latest_analysis_id:
                latest_analysis_id = key
    
    if latest_analysis_id:
        analysis_data = st.session_state.store.get(latest_analysis_id)
        if analysis_data and "analysis_report" in analysis_data:
            report = analysis_data["analysis_report"]
            st.write(f"**Summary:** {report.get('summary', 'No summary provided.')}")
            st.write(f"**Confidence Score:** {report.get('confidence_score', 0)}%")
            
            # Project Health Summary
            st.divider()
            st.markdown("### 📊 Project Health")
            
            detailed_findings = report.get("detailed_findings", [])
            # Handle both list of strings and list of dicts
            warning_count = 0
            error_count = 0
            critical_count = 0
            
            for f in detailed_findings:
                if isinstance(f, dict):
                    sev = f.get("severity", "").lower()
                    if sev == "critical":
                        critical_count += 1
                    elif sev == "error":
                        error_count += 1
                    elif sev == "warning":
                        warning_count += 1
                else:
                    # Assume string findings are warnings/suggestions
                    warning_count += 1
            
            total_findings = len(detailed_findings)
            suggestions_count = max(0, total_findings - critical_count - error_count - warning_count)
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Critical", critical_count)
            c2.metric("Errors", error_count)
            c3.metric("Warnings", warning_count)
            c4.metric("Suggestions", suggestions_count)
            
            st.divider()
            
            findings = detailed_findings
            if findings:
                for i, finding in enumerate(findings):
                    if isinstance(finding, dict):
                        desc = finding.get('description', 'No description')
                        file_path = finding.get('file_path', 'unknown')
                        severity = finding.get('severity', 'unknown').upper()
                        confidence = finding.get('confidence', 0) * 100
                        
                        with st.expander(f"Finding {i+1}: {desc}"):
                            st.write(f"**File:** `{file_path}`")
                            st.write(f"**Severity:** {severity}")
                            st.write(f"**Confidence:** {confidence}%")
                            
                            # Diff Viewer Panel
                            if "diff" in finding:
                                st.markdown("### 🔍 Diff Preview")
                                diff_content = finding.get("diff", "")
                                
                                # Calculate diff stats
                                lines_added = sum(1 for line in diff_content.split('\n') if line.startswith('+') and not line.startswith('+++'))
                                lines_removed = sum(1 for line in diff_content.split('\n') if line.startswith('-') and not line.startswith('---'))
                                
                                st.write(f"**Files changed:** 1")
                                st.write(f"**Lines added:** {lines_added}")
                                st.write(f"**Lines removed:** {lines_removed}")
                                
                                st.code(diff_content, language='diff')
                                
                            else:
                                st.code(finding.get('explanation', 'No explanation provided'), language='python')
                            
                            # Pre-flight check
                            validator = PatchValidator()
                            is_safe, reason = validator.validate_patch({
                                "file_path": finding.get("file_path"),
                                "diff": finding.get("diff")
                            })
                            
                            if not is_safe:
                                st.error(f"⚠️ SAFETY WARNING: {reason}")
                            else:
                                st.success("✅ Pre-flight check passed.")
                                st.session_state.workflow_state["fix_validated"] = True
                            
                            c1, c2 = st.columns(2)
                            with c1:
                                if st.button(f"Apply Patch {i+1}", key=f"apply_{i}"):
                                    # Dispatch to fix_generator
                                    st.session_state.captn.dispatch_to_plugin(
                                        f"fix_{i}",
                                        "fix_generator",
                                        Message(
                                            sender="ui",
                                            destination="captn",
                                            type="task",
                                            payload={
                                                "task_id": f"fix_{i}",
                                                "action": "fix_generator",
                                                "payload": {"patch": finding}
                                            }
                                        )
                                    )
                            with c2:
                                if st.button(f"Rollback {i+1}", key=f"rb_{i}"):
                                    success = st.session_state.store.rollback(f"fix_{i}")
                                    if success:
                                        st.success("Rollback successful")
                                    else:
                                        st.error("Rollback failed")
                    else:
                        # String finding (e.g., 'File missing: tetris/game.py')
                        desc = str(finding)
                        with st.expander(f"Finding {i+1}: {desc}"):
                            st.write(f"**Type:** Warning/Suggestion")
                            st.write(f"**Details:** {desc}")
            else:
                st.info("No specific findings identified.")
        else:
            st.info("Analysis in progress or no report generated yet.")
    else:
        st.info("No analysis report found. Run the Analysis Pipeline to generate one.")

    st.divider()
    st.subheader("Workspace Files")
    if 'project_stats' in st.session_state and st.session_state.captn.active_manifest:
        manifest = st.session_state.captn.active_manifest
        files = [os.path.basename(f) for f in manifest.python_files]
        if files:
            st.write(f"✅ {len(files)} Files in Workspace:")
            for f in files:
                st.write(f"- {f}")
        else:
            st.write("No files found in loaded project.")
    else:
        st.write("No project loaded.")

# --- Open-Source Code Crawler Panel ---
st.divider()
st.subheader("🕸️ Open-Source Code Crawler")
st.caption(
    "Auto-generate a local copy of a public GitHub repo's source code. "
    "Only github.com is contacted; set GITHUB_TOKEN in the environment for a higher rate limit."
)

_crawler_ok = True
try:
    from captn.workers.data_ingestion.crawler import (
        GitHubCrawler,
        RateLimitError,
        CrawlError as _CrawlError,
        merge_dataset_into_corpus,
    )
except Exception as _crawl_imp_err:  # pragma: no cover - depends on requests
    _crawler_ok = False
    st.warning(f"Crawler unavailable: {_crawl_imp_err}")


def _dedupe_keep_order(items):
    """Deduplicate a list while preserving first-seen order."""
    seen = set()
    out = []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out

if _crawler_ok:
    _c1, _c2 = st.columns([2, 1])
    with _c1:
        repo_spec = st.text_input(
            "Repository (owner/name or GitHub URL)",
            placeholder="psf/requests",
            key="crawl_repo",
        )
    with _c2:
        branch = st.text_input("Branch / ref (optional)", placeholder="main", key="crawl_branch")

    _c3, _c4, _c5 = st.columns([1, 1, 1])
    with _c3:
        dest = st.text_input(
            "Output dir (optional)",
            placeholder="./crawled/<owner>__<name>",
            key="crawl_dest",
        )
    with _c4:
        match = st.text_input("Path regex (optional)", placeholder=r".*\.py$", key="crawl_match")
    with _c5:
        include_ext = st.text_input(
            "Extensions (optional, comma-sep)",
            placeholder=".py,.md",
            key="crawl_ext",
        )

    _c6, _c7, _c8 = st.columns([1, 1, 1])
    with _c6:
        max_files = st.number_input(
            "Max files", min_value=1, max_value=2000, value=200, key="crawl_max"
        )
    with _c7:
        overwrite = st.checkbox("Overwrite existing files", key="crawl_over")
    with _c8:
        dry_run = st.checkbox("Dry run (do not write)", key="crawl_dry")

    _is_dry_run = st.session_state.get("crawl_dry", False)

    _c9, _c10, _c11 = st.columns([1, 1, 1])
    with _c9:
        export_dataset = st.checkbox("Export AI-ready JSON dataset", value=True, key="crawl_ds",
                                     disabled=_is_dry_run,
                                     help="Disabled during dry run — no files are written.")
    with _c10:
        learn_corpus = st.checkbox("🧠 Feed into learning corpus", value=False, key="crawl_learn",
                                   disabled=_is_dry_run,
                                   help="Disabled during dry run — no files are written.")
    with _c11:
        st.markdown(
            "<div style='padding-top:1.6em;font-size:0.8em;color:#888'>"
            "Writes dataset.jsonl / dataset.json / manifest.json</div>",
            unsafe_allow_html=True,
        )

    # --- GitHub token: stored gitignored in .captn/github_token, never in code ---
    _token_file = os.path.join(get_project_root(), ".captn", "github_token")
    _token_help = (
        "Paste a GitHub PAT to raise the crawl rate limit from 60 to 5000 req/h. "
        "Stored only in .captn/github_token (gitignored). Leave blank to use the "
        "env var or the stored token."
    )
    _github_token = st.text_input(
        "🔑 GitHub token (optional, higher rate limit)",
        type="password", value="", key="crawl_token", help=_token_help,
    )
    if _github_token.strip():
        st.session_state["_active_crawl_token"] = _github_token.strip()
        try:
            os.makedirs(os.path.dirname(_token_file), exist_ok=True)
            # Write only if different (avoid churn / re-permission prompts).
            _existing = ""
            if os.path.isfile(_token_file):
                with open(_token_file, "r", encoding="utf-8") as fh:
                    _existing = fh.read().strip()
            if _existing != _github_token.strip():
                with open(_token_file, "w", encoding="utf-8") as fh:
                    fh.write(_github_token.strip())
                st.success("Token saved to .captn/github_token (gitignored).")
        except OSError as _oe:
            st.error(f"Could not save token file: {_oe}")
    elif "_active_crawl_token" not in st.session_state:
        # Fall back to env var or file for session_state
        _file_tok = ""
        if os.path.isfile(_token_file):
            with open(_token_file, "r", encoding="utf-8") as fh:
                _file_tok = fh.read().strip()
        st.session_state["_active_crawl_token"] = _file_tok or os.environ.get("GITHUB_TOKEN", "")

    # Show the ACTIVE rate-limit ceiling so the user knows if they are unlocked.
    _active_tok = _github_token.strip() or os.environ.get("GITHUB_TOKEN") or (
        open(_token_file).read().strip() if os.path.isfile(_token_file) else ""
    )
    _rl_label = "5000 req/h (authenticated)" if _active_tok else "60 req/h (unauthenticated)"
    st.caption(f"Active GitHub API limit: **{_rl_label}**")

    if st.button("🚀 Crawl Repo", use_container_width=True, key="crawl_go", disabled=not repo_spec.strip()):
        try:
            _ext_list = None
            if include_ext.strip():
                _ext_list = [e.strip() for e in include_ext.split(",") if e.strip()]
            _dest = dest.strip() or os.path.join(
                get_project_root(), "crawled", repo_spec.strip().replace("/", "__")
            )
            _crawler = GitHubCrawler(
                token=_github_token.strip() or None,  # explicit wins; else file/env
                max_files=int(max_files),
            )
            with st.spinner(f"Crawling {repo_spec.strip()} ..."):
                _result = _crawler.crawl(
                    repo_spec=repo_spec.strip(),
                    branch=branch.strip() or None,
                    dest=_dest,
                    overwrite=overwrite,
                    match=match.strip() or None,
                    dry_run=dry_run,
                    include_ext=_ext_list,
                    export_dataset=export_dataset,
                )
            _verb = "Discovered" if dry_run else "Written"
            st.success(
                f"{_verb} {len(_result.files_written)} files "
                f"({_result.bytes_written} bytes) from {_result.repo}@{_result.branch} "
                f"in {_result.elapsed_s:.2f}s"
            )
            # Self code-learning: merge the crawled dataset into the project corpus.
            _learn_stats = None
            if learn_corpus and not dry_run and _result.dataset_jsonl:
                from captn.workers.data_ingestion.crawler import merge_dataset_into_corpus
                try:
                    _learn_stats = merge_dataset_into_corpus(
                        _result.dataset_jsonl,
                        os.path.join(get_project_root(), "Code_base"),
                    )
                except Exception as _le:
                    st.error(f"Corpus merge failed: {_le}")
            if _result.truncated_tree:
                st.warning(
                    "GitHub returned a truncated tree; results may be incomplete. "
                    "Narrow with a path regex or raise Max files."
                )
            _m1, _m2, _m3, _m4 = st.columns(4)
            _m1.metric("Files written", len(_result.files_written))
            _m2.metric("In dataset", len(_result.files_included))
            _m3.metric("Skipped", len(_result.files_skipped))
            _m4.metric("Errors", len(_result.errors))
            if _result.dataset_jsonl:
                st.session_state["crawl_last_dataset"] = _result.dataset_jsonl
                st.info(
                    f"🤖 AI dataset ready ({len(_result.files_included)} records):\n"
                    f"- `{os.path.basename(_result.dataset_jsonl)}` (JSONL, one record/line)\n"
                    f"- `{os.path.basename(_result.dataset_json)}` (single-file JSON)\n"
                    f"- `manifest.json` (summary + per-language counts)"
                )
            if _learn_stats:
                st.success(
                    f"🧠 Fed into learning corpus: **{_learn_stats['added']} added**, "
                    f"{_learn_stats['duplicates']} duplicate → corpus now "
                    f"**{_learn_stats['added'] + _learn_stats['existing']} records** "
                    f"(Code_base/dataset.jsonl). Re-run the worker chain to learn from it."
                )
            if _result.files_written:
                with st.expander(f"View {len(_result.files_written)} written files"):
                    for _w in _result.files_written:
                        st.write(f"`{_w.rel_path}` — {_w.size} bytes")
            if _result.errors:
                with st.expander(f"View {len(_result.errors)} errors"):
                    for _e in _result.errors:
                        st.error(_e)
        except (_CrawlError, RateLimitError) as _ce:
            st.error(str(_ce))
        except Exception as _ex:  # never crash the dashboard on a crawler failure
            st.error(f"Unexpected crawler error: {type(_ex).__name__}: {_ex}")

    # --- Crawler Queue (batch multiple repos in one session) ---
    st.divider()
    st.markdown("#### 📋 Crawler Queue (batch multiple repos)")
    st.caption(
        "One repo per line (owner/name, a GitHub URL, or an ssh URL). "
        "All repos use the filters above (branch, regex, extensions, max files, "
        "overwrite, dry-run, dataset + learning). Runs sequentially in this session."
    )
    _queue_text = st.text_area(
        "Repositories (one per line)",
        placeholder="psf/requests\nnvbn/thefuck\ntiangolo/fastapi\npallets/flask",
        key="crawl_queue",
        height=140,
    )
    _cq1, _cq2 = st.columns([1, 1])
    with _cq1:
        if st.button("🚀 Crawl Queue", use_container_width=True, key="crawl_queue_go",
                     disabled=not _queue_text.strip()):
            _queue_repos = [r.strip() for r in _queue_text.splitlines() if r.strip()]
            _queue_repos = _dedupe_keep_order(_queue_repos)
            _ext_list = None
            if include_ext.strip():
                _ext_list = [e.strip() for e in include_ext.split(",") if e.strip()]

            _q_results = []  # (repo, status, detail)
            _q_progress = st.progress(0.0)
            _q_log = st.empty()
            _q_crawler = GitHubCrawler(token=st.session_state.get("_active_crawl_token", os.environ.get("GITHUB_TOKEN")), max_files=int(max_files))
            for _i, _repo in enumerate(_queue_repos):
                _dest = dest.strip() or os.path.join(
                    get_project_root(), "crawled", _repo.replace("/", "__")
                )
                _status = f"⏳ ({_i+1}/{len(_queue_repos)}) {_repo}"
                _q_log.text(_status)
                try:
                    _r = _q_crawler.crawl(
                        repo_spec=_repo,
                        branch=branch.strip() or None,
                        dest=_dest,
                        overwrite=overwrite,
                        match=match.strip() or None,
                        dry_run=dry_run,
                        include_ext=_ext_list,
                        export_dataset=export_dataset,
                    )
                    # Self code-learning merge for each queued repo.
                    _learn_msg = ""
                    if learn_corpus and not dry_run and _r.dataset_jsonl:
                        try:
                            _ls = merge_dataset_into_corpus(
                                _r.dataset_jsonl,
                                os.path.join(get_project_root(), "Code_base"),
                            )
                            _learn_msg = f" | 🧠 +{_ls['added']} (dup {_ls['duplicates']})"
                        except Exception as _le:
                            _learn_msg = f" | corpus-err: {_le}"
                    _verb = "dry" if dry_run else "ok"
                    _q_results.append(
                        (_repo, "✅", f"{_verb}: {len(_r.files_included)} files, "
                                     f"{len(_r.files_written)} written{_learn_msg}")
                    )
                    if _r.dataset_jsonl:
                        st.session_state["crawl_last_dataset"] = _r.dataset_jsonl
                except (_CrawlError, RateLimitError) as _ce:
                    _q_results.append((_repo, "⛔", str(_ce)))
                except Exception as _ex:
                    _q_results.append((_repo, "⛔", f"{type(_ex).__name__}: {_ex}"))
                _q_progress.progress((_i + 1) / len(_queue_repos))
                _q_log.info(_q_results[-1][1] + " " + _repo + " — " + _q_results[-1][2])

            # Global summary
            _ok = sum(1 for _, s, _ in _q_results if s == "✅")
            _fail = len(_q_results) - _ok
            st.success(f"Queue done: {_ok} succeeded, {_fail} failed, {len(_q_results)} total.")
            with st.expander(f"Queue report ({len(_q_results)} repos)"):
                for _repo, _s, _d in _q_results:
                    st.write(f"{_s} `{_repo}` — {_d}")
    with _cq2:
        st.markdown(
            "<div style='padding-top:1.6em;font-size:0.8em;color:#888'>"
            "Tip: a 'dry run' still discovers files and builds the dataset report, "
            "but writes nothing. Use it to preview a big queue first.</div>",
            unsafe_allow_html=True,
        )

    # --- Auto-Crawl: most popular OPEN-SOURCE repos of the moment ---
    st.divider()
    st.markdown("#### 🤖 Auto-Crawl (best open-source repos of the moment)")
    st.caption(
        "Queries the GitHub Search API for the most-starred PUBLIC open-source repos "
        "(license permissive, pushed recently) per language, then crawls them into the "
        "dataset using the filters above (max files, dry-run, dataset export, learning)."
    )

    # Popular tech-industry languages mapped to their main file extensions.
    _AUTO_LANGS = {
        "Python": ".py",
        "JavaScript": ".js,.mjs,.jsx",
        "TypeScript": ".ts,.tsx",
        "HTML": ".html,.htm",
        "CSS": ".css,.scss",
        "Rust": ".rs",
        "C": ".c,.h",
        "C++": ".cpp,.cc,.cxx,.hpp",
        "C#": ".cs",
        "Go": ".go",
        "Java": ".java",
    }
    _ac_langs = st.multiselect(
        "Languages to crawl",
        options=list(_AUTO_LANGS.keys()),
        default=["Python", "JavaScript", "Rust", "Go"],
        key="ac_langs",
    )
    _acr1, _acr2 = st.columns([1, 1])
    with _acr1:
        ac_repos_per_lang = st.number_input(
            "Repos / language", min_value=1, max_value=5, value=1, key="ac_rpl"
        )
    with _acr2:
        ac_min_stars = st.number_input(
            "Min stars", min_value=100, max_value=500000, value=5000, step=1000, key="ac_stars"
        )

    def _search_top_repos(language: str, per_lang: int, min_stars: int, user_token: str = ""):
        """Query GitHub Search API for top starred open-source repos.

        Open-source only: explicit permissive licenses (MIT/Apache/BSD)
        restrict results to truly open-source code; `is:public` + `pushed`
        keeps active public projects.
        Returns list of 'owner/name' strings (never clones private/forked code).
        """
        _gh_tok = user_token or os.environ.get("GITHUB_TOKEN")
        _hdrs = {"Accept": "application/vnd.github+json"}
        if _gh_tok:
            _hdrs["Authorization"] = f"Bearer {_gh_tok}"
        q = (
            f"language:{language} stars:>={int(min_stars)} "
            f"license:mit license:apache-2.0 license:bsd-3-clause license:bsd-2-clause "
            f"is:public archived:false pushed:>2024-01-01"
        )
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": q, "sort": "stars", "order": "desc", "per_page": per_lang},
            headers=_hdrs,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub search HTTP {resp.status_code}: {resp.text[:150]}")
        return [item["full_name"] for item in resp.json().get("items", [])]

    # Persistent log file so progress survives reruns and streams in real time
    # (same pattern as autogen.log / raw2json.log).
    _AC_LOG = os.path.abspath(os.path.join(get_project_root(), "autocrawl.log"))

    def _ac_tlog(msg):
        with open(_AC_LOG, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")

    # Always-visible live log box for this panel.
    # E-07: show stale content from previous sessions only while a run is
    # active or just finished in this session; otherwise show the placeholder
    # so old-session "ghost" logs never confuse the user at first paint.
    _ac_prev = tail_log(_AC_LOG) if os.path.exists(_AC_LOG) else ""
    if not st.session_state.get("ac_active", False) and not st.session_state.get("ac_ran_this_session", False):
        _ac_prev = ""
    st.code(
        _ac_prev
        or "(no log yet — run Auto-Crawl to see discovery and crawling here in real time)",
        language="text",
    )

    ac_manual = st.checkbox(
        "🔓 Enable auto-crawl manually",
        value=False,
        key="ac_manual",
        help="Must be checked for the button to work.",
    )
    if "ac_active" not in st.session_state:
        st.session_state["ac_active"] = False
    if st.session_state.get("ac_active", False):
        st_autorefresh(interval=1000, key="ac_refresh")
        _ac_live = tail_log(_AC_LOG) if os.path.exists(_AC_LOG) else ""
        st.code(_ac_live or "(…)", language="text")
        if "[AUTOCRAWL] Finished" in _ac_live or "[AUTOCRAWL][ERROR]" in _ac_live:
            st.session_state["ac_active"] = False

    _ac_go = st.button("🤖 Auto-Crawl trending repos", use_container_width=True,
                       key="ac_go", disabled=not _ac_langs or not ac_manual
                       or st.session_state.get("ac_active", False))
    if _ac_go:
        st.session_state["ac_active"] = True
        st.session_state["ac_ran_this_session"] = True  # E-07: this session owns the log now
        open(_AC_LOG, "w", encoding="utf-8").close()  # fresh log for this run
        _ac_tlog("[AUTOCRAWL] Start — discovering top open-source repos…")
    if _ac_go:
        # 1) Discover the top repos per selected language.
        _ac_plan = []          # (lang, repo) pairs
        _ac_errors = []
        for _lang in _ac_langs:
            try:
                _ac_tlog(f"[AUTOCRAWL] 🔎 GitHub search: top {int(ac_repos_per_lang)} repos {_lang} (≥{int(ac_min_stars)}★, MIT/Apache/BSD license)…")
                _found = _search_top_repos(_lang, int(ac_repos_per_lang), int(ac_min_stars), st.session_state.get("_active_crawl_token", ""))
                for _r in _found:
                    _ac_plan.append((_lang, _r))
                    _ac_tlog(f"[AUTOCRAWL]   ✓ {_r}")
                if not _found:
                    _ac_errors.append(f"{_lang}: no repo matched the criteria")
                    _ac_tlog(f"[AUTOCRAWL]   ⚠ {_lang}: no results")
            except Exception as _se:
                _ac_errors.append(f"{_lang}: {_se}")
                _ac_tlog(f"[AUTOCRAWL][ERROR] {_lang}: {_se}")

        _ac_tlog(f"[AUTOCRAWL] {len(_ac_plan)} repos selected. Starting crawl (max_files={int(max_files)}, dry_run={'yes' if dry_run else 'no'})…")

        st.info(f"🔎 {len(_ac_plan)} repos selected: " + ", ".join(f"`{r}`" for _, r in _ac_plan))
        for _e in _ac_errors:
            st.warning(_e)

        if not _ac_plan:
            st.error("No repos found — check your connection or GITHUB_TOKEN.")
        else:
            # 2) Crawl each discovered repo with the panel's filters.
            _ext_list = None
            if include_ext.strip():
                _ext_list = [e.strip() for e in include_ext.split(",") if e.strip()]
            else:
                # Default: crawl each language's own extensions so one pass
                # builds a multi-language dataset.
                _ext_list = _dedupe_keep_order(
                    [e.strip() for l in _ac_langs for e in _AUTO_LANGS[l].split(",")]
                )
            _ac_crawler = GitHubCrawler(token=st.session_state.get("_active_crawl_token", os.environ.get("GITHUB_TOKEN")), max_files=int(max_files))
            _ac_results = []
            _ac_progress = st.progress(0.0)
            _ac_log = st.empty()

            for _i, (_lang, _repo) in enumerate(_ac_plan):
                _dest = os.path.join(get_project_root(), "crawled", _repo.replace("/", "__"))
                _ac_tlog(f"[AUTOCRAWL] ⏳ ({_i+1}/{len(_ac_plan)}) crawling {_repo} ({_lang})…")
                try:
                    _r = _ac_crawler.crawl(
                        repo_spec=_repo,
                        branch=None,
                        dest=_dest,
                        overwrite=False,
                        match=None,
                        dry_run=dry_run,
                        include_ext=_ext_list,
                        export_dataset=export_dataset,
                    )
                    _learn_msg = ""
                    if learn_corpus and not dry_run and _r.dataset_jsonl:
                        try:
                            _ls = merge_dataset_into_corpus(
                                _r.dataset_jsonl,
                                os.path.join(get_project_root(), "Code_base"),
                            )
                            _learn_msg = f" | 🧠 +{_ls['added']} (dup {_ls['duplicates']})"
                        except Exception as _le:
                            _learn_msg = f" | corpus-err: {_le}"
                    if _r.dataset_jsonl:
                        st.session_state["crawl_last_dataset"] = _r.dataset_jsonl
                    _verb = "dry" if dry_run else "ok"
                    _msg = (f"{_lang} | {_verb}: {len(_r.files_included)} files, "
                            f"{len(_r.files_written)} written{_learn_msg}")
                    _ac_results.append((_lang, _repo, "✅", _msg))
                    _ac_tlog(f"[AUTOCRAWL]   ✅ {_repo} — {_msg}")
                except Exception as _ex:
                    _ac_results.append((_lang, _repo, "⛔", f"{type(_ex).__name__}: {_ex}"))
                    _ac_tlog(f"[AUTOCRAWL][ERROR] {_repo}: {type(_ex).__name__}: {_ex}")
                _ac_progress.progress((_i + 1) / len(_ac_plan))
                _ac_log.info(f"⏳ ({_i+1}/{len(_ac_plan)}) {_repo}: {_ac_results[-1][3]}")

            _ac_ok = sum(1 for _, _, s, _ in _ac_results if s == "✅")
            _ac_tlog(f"[AUTOCRAWL] Finished: {_ac_ok}/{len(_ac_results)} repos crawled successfully.")
            st.success(f"Auto-crawl done: {_ac_ok}/{len(_ac_results)} repos crawled.")
            with st.expander("Auto-crawl report"):
                for _lang, _repo, _s, _d in _ac_results:
                    st.write(f"{_s} `{_repo}` ({_lang}) — {_d}")

    # --- Autogenerate Code Loop (uses the crawled dataset + Ollama) ---
    st.divider()
    st.markdown("##### 🔁 Autogenerate Code Loop")
    st.caption(
        "After crawling, run a loop that feeds crawled Python files to Ollama/qwen2:1.5b, "
        "validates each generated file with PatchValidator, and writes safe variants to "
        "generated/. Uses the most recent crawl's dataset (or pick one below)."
    )
    _ag1, _ag2, _ag3 = st.columns([1, 1, 1])
    with _ag1:
        autogen_iter = st.number_input("Iterations", min_value=1, max_value=20, value=3, key="ag_iter")
    with _ag2:
        autogen_sample = st.number_input("Files / iter", min_value=1, max_value=20, value=4, key="ag_sample")
    with _ag3:
        autogen_feed = st.checkbox("Feed back into corpus", value=False, key="ag_feed",
                                   help="Append generated code to Code_base/dataset.jsonl for self-learning")
    _ag_ds = st.text_input(
        "Dataset JSONL (optional; defaults to last crawl)",
        value=st.session_state.get("crawl_last_dataset", "") or "",
        key="ag_ds",
        placeholder="path/to/dataset.jsonl",
    )
    # F1 fix: if the field is empty (fresh session), auto-discover the most
    # recent dataset on disk instead of silently disabling the button.
    _ag_ds_effective = _ag_ds.strip()
    _ag_ds_autodiscovered = False
    if not _ag_ds_effective:
        _candidates = []
        for cand in (
            os.path.join(get_project_root(), "Code_base", "_converted", "dataset.jsonl"),
            os.path.join(get_project_root(), "_converted", "dataset.jsonl"),
            st.session_state.get("crawl_last_dataset", ""),
        ):
            if cand and os.path.isfile(cand):
                _candidates.append(cand)
        if _candidates:
            _ag_ds_effective = max(_candidates, key=os.path.getmtime)
            _ag_ds_autodiscovered = True
            st.info(f"📎 Auto-detected dataset: `{_ag_ds_effective}`")
    _ag_ds_ok = bool(_ag_ds_effective) and os.path.isfile(_ag_ds_effective)
    if _ag_ds.strip() and not _ag_ds_ok:
        st.warning(f"⚠️ Dataset file not found: `{_ag_ds.strip()}`. Crawl a repo first, or paste a real dataset.jsonl path.")
    elif not _ag_ds_ok:
        st.warning("⚠️ No dataset available. Run a Crawl or Raw→JSON conversion first.")
    # Manual activation gate: ON DEMAND — the loop ONLY runs when the user
    # explicitly flips this switch AND presses Run in the same session.
    ag_manual = st.checkbox(
        "🔓 Enable autogen loop manually (on-demand)",
        value=False,
        key="ag_manual",
        help="Must be checked for the Run button to work.",
    )
    # F2 fix: tell the user exactly what is missing instead of a dead button.
    if not ag_manual and _ag_ds_ok:
        st.caption("👆 Check « Enable autogen loop manually » to unlock the Run button.")
    _ag_prompt = st.text_area(
        "Prompt template",
        value=(
            "Refactor the following Python code to improve clarity, add type hints, "
            "and keep behaviour identical. Output ONLY the refactored code in a single "
            "```python fenced block."
        ),
        key="ag_prompt",
        height=90,
    )
    if st.button("🔁 Run autogen loop", use_container_width=True, key="ag_go",
                 disabled=not _ag_ds_ok or not ag_manual):
        _ag_log_path = os.path.abspath(os.path.join(get_project_root(), "autogen.log"))
        _ag_done_flag = os.path.abspath(os.path.join(get_project_root(), "autogen.done"))
        open(_ag_log_path, "w", encoding="utf-8").close()
        if os.path.exists(_ag_done_flag):
            os.remove(_ag_done_flag)  # fresh run: clear previous completion marker
        st.session_state["ag_active"] = True

        # Check Ollama connectivity before starting the loop so the user
        # gets an immediate, clear error instead of a silent no-op.
        from captn.runtime.llm_host import ollama_base_url as _obu
        _ollama_url = _obu() + "/api/ps"
        _ollama_ok = False
        try:
            _ollama_resp = requests.get(_ollama_url, timeout=5)
            _ollama_ok = _ollama_resp.status_code == 200
        except Exception:
            _ollama_ok = False

        if not _ollama_ok:
            with open(_ag_log_path, "a", encoding="utf-8") as _lf:
                _lf.write(f"[AUTOGEN][ERROR] Ollama is not running at {_obu()}.\n")
                _lf.write("[AUTOGEN][ERROR] Start Ollama and pull qwen2:1.5b:  ollama run qwen2:1.5b\n")
            st.error(f"❌ Ollama is not running at {_obu()}. Start it and pull qwen2:1.5b first.")
            st.session_state["ag_active"] = False
        else:
            def _run_ag_thread(log_path, ds, done_flag_path, **kw):
                import contextlib
                import json as _json
                from captn.workers.code_generation.autogen import run_autogen_loop

                # AUDIT-C: this runs in a daemon thread — NEVER touch
                # st.session_state here (raises MissingScriptRunContext and
                # can kill the thread mid-run). Completion is signalled via
                # a marker FILE that the main script polls instead.
                try:
                    with open(log_path, "a", encoding="utf-8") as lf, contextlib.redirect_stdout(lf):
                        res = run_autogen_loop(ds, **kw)
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write("\n[AUTOGEN] done: " + _json.dumps(res.to_dict()) + "\n")
                except Exception as _e:
                    import traceback as _tb
                    with open(log_path, "a", encoding="utf-8") as lf:
                        lf.write(f"\n[AUTOGEN][ERROR] {type(_e).__name__}: {_e}\n")
                        _tb.print_exc(file=lf)
                finally:
                    try:
                        with open(done_flag_path, "w", encoding="utf-8") as ff:
                            ff.write("done")
                    except OSError:
                        pass

            _ag_thread = threading.Thread(
                target=_run_ag_thread,
                kwargs=dict(
                    log_path=_ag_log_path,
                    ds=_ag_ds_effective,
                    done_flag_path=os.path.abspath(os.path.join(get_project_root(), "autogen.done")),
                    iterations=int(autogen_iter),
                    sample_per_iter=int(autogen_sample),
                    prompt_template=_ag_prompt,
                    out_dir=os.path.join(get_project_root(), "generated"),
                    feed_corpus=bool(autogen_feed),
                    corpus_dir=os.path.join(get_project_root(), "Code_base"),
                ),
                daemon=True,
            )
            _ag_thread.start()
            st.success("Autogen loop started. The log below streams live.")

    # Live, real-time log: auto-refresh while the loop is running, stop when done.
    # The log box is ALWAYS shown so the user can follow activity at any time.
    _ag_log = os.path.abspath(os.path.join(get_project_root(), "autogen.log"))
    _ag_content = tail_log(_ag_log) if os.path.exists(_ag_log) else ""
    if st.session_state.get("ag_active", False):
        st_autorefresh(interval=1500, key="ag_refresh")
        # AUDIT-C: completion is signalled by the worker thread via the
        # autogen.done marker FILE (the thread must not touch session_state).
        _ag_done_flag = os.path.abspath(os.path.join(get_project_root(), "autogen.done"))
        _log_says_done = "[AUTOGEN] done" in _ag_content or "[AUTOGEN][ERROR]" in _ag_content
        if os.path.exists(_ag_done_flag) or _log_says_done:
            st.session_state["ag_active"] = False
    st.code(_ag_content or "(no log yet — enable and run the loop to see activity here in real time)", language="text")
    if st.session_state.get("ag_active", False):
        st.info("🔄 Autogen running… (auto-refreshing)")
    else:
        st.caption("Autogen idle. Generated files are in `generated/`.")

    # --- Raw -> Structured JSON panel ---
    st.divider()
    st.markdown("##### 📦 Raw → Structured JSON")
    st.caption(
        "Convert raw local files (code, docs, configs) from a directory into the "
        "same structured JSONL the crawler produces (schema `captn.crawler.dataset/1.0`), "
        "so they can feed the learning corpus. Read-only scan; outputs to `<root>/_converted/`."
    )
    _rj1, _rj2 = st.columns([3, 1])
    with _rj1:
        raw_root = st.text_input(
            "Source directory",
            value=os.path.join(get_project_root(), "Code_base"),
            key="raw_root",
            placeholder="path/to/raw/files",
        )
    with _rj2:
        raw_ext = st.text_input(
            "Ext allow-list (optional)",
            value="",
            key="raw_ext",
            placeholder=".py,.rst,.md",
        )
    _rj3, _rj4 = st.columns([1, 1])
    with _rj3:
        raw_feed = st.checkbox("Feed into learning corpus", value=False, key="raw_feed")
    with _rj4:
        raw_nodata = st.checkbox("Skip writing files (dry)", value=False, key="raw_nodata")

    # Dedup controls: automatic after each merge (default ON), plus an
    # on-demand button so duplicates can be purged manually at any time.
    raw_autodedup = st.checkbox(
        "🧹 Auto-dedup corpus after merge",
        value=True,
        key="raw_autodedup",
        help="Automatically remove corpus duplicates (by sha256 content) after each merge.",
    )
    if st.button("🧹 Deduplicate corpus now", key="raw_dedup_now"):
        from captn.workers.data_ingestion.crawler import dedup_corpus
        try:
            _dd = dedup_corpus(os.path.join(get_project_root(), "Code_base"))
            if _dd["removed"] > 0:
                st.success(f"✅ {_dd['removed']} duplicate(s) removed — "
                           f"{_dd['before']} → {_dd['after']} records.")
            else:
                st.info("No duplicates found — the corpus is already clean. ✨")
        except Exception as _de:
            st.error(f"Dedup failed: {_de}")

    # Manual activation gate + always-on real-time log.
    raw_manual = st.checkbox(
        "🔓 Enable conversion manually",
        value=False,
        key="raw_manual",
        help="Must be checked for the Convert button to work.",
    )
    _RAW_LOG = os.path.abspath(os.path.join(get_project_root(), "raw2json.log"))
    _raw_ok = bool(raw_root.strip()) and os.path.isdir(raw_root.strip())
    if raw_root.strip() and not _raw_ok:
        st.warning(f"⚠️ Directory not found: `{raw_root.strip()}`")
    if "raw_active" not in st.session_state:
        st.session_state["raw_active"] = False

    if st.button("📦 Convert to JSON", use_container_width=True, key="raw_go",
                 disabled=not _raw_ok or not raw_manual or st.session_state.get("raw_active", False)):
        _raw_log = os.path.abspath(os.path.join(get_project_root(), "raw2json.progress.json"))
        open(_RAW_LOG, "w", encoding="utf-8").close()  # fresh log for this run
        # Mark running IMMEDIATELY (before the thread starts) so the next render
        # already sees in-flight state and auto-refreshes. This also makes the
        # bar move even if the worker thread is slow to spin up.
        with open(_raw_log, "w", encoding="utf-8") as _lf:
            _lf.write('{"status":"running","done":0,"total":0}')
        st.session_state["raw_active"] = True

        def _run_raw_thread(log_path, txt_log, root, ext_list, feed, nodata, autodedup=True):
            import json as _json
            from captn.workers.data_ingestion.raw2json import convert_raw_directory
            from captn.workers.data_ingestion.crawler import merge_dataset_into_corpus

            def _tlog(msg):
                with open(txt_log, "a", encoding="utf-8") as fh:
                    fh.write(msg + "\n")

            try:
                _tlog("[RAW2JSON] Starting conversion…")
                # Pre-count eligible files for an accurate progress total.
                _total = 0
                _excl = {".git", ".hg", ".svn", "node_modules", "__pycache__",
                         ".venv", "venv", "build", "dist", ".idea", ".tox",
                         ".mypy_cache", ".pytest_cache", "site-packages",
                         "target", ".gradle"}
                for _dp, _dn, _fn in os.walk(root):
                    _dn[:] = [d for d in _dn if d not in _excl]
                    for _f in _fn:
                        _p = os.path.join(_dp, _f)
                        if os.path.abspath(_p) == log_path:
                            continue
                        _total += 1
                _tlog(f"[RAW2JSON] {_total} files found in {root}")

                # Re-assert running with the real total count.
                with open(log_path, "w", encoding="utf-8") as _lf:
                    _json.dump({"status": "running", "done": 0, "total": _total}, _lf)

                _res = convert_raw_directory(
                    root,
                    include_ext=ext_list,
                    export_dataset=not nodata,
                    progress_file=log_path,
                    progress_total=_total,
                )
                _tlog(f"[RAW2JSON] Included: {len(_res.files_included)} · Skipped: {len(_res.files_skipped)} · Errors: {len(_res.errors)}")
                for _err in list(_res.errors)[:10]:
                    _tlog(f"[RAW2JSON][ERROR] {_err}")
                _out = {
                    "status": "done",
                    "done": _total,
                    "total": _total,
                    "included": len(_res.files_included),
                    "skipped": len(_res.files_skipped),
                    "errors": len(_res.errors),
                    "dataset_jsonl": _res.dataset_jsonl,
                    "dataset_json": _res.dataset_json,
                }
                if feed and _res.dataset_jsonl:
                    _tlog("[RAW2JSON] Feeding into learning corpus…")
                    _ls = merge_dataset_into_corpus(
                        _res.dataset_jsonl, os.path.join(get_project_root(), "Code_base"),
                        dedup_after=autodedup,
                    )
                    _out["fed"] = _ls
                    _dd = _ls.get("dedup_removed", 0)
                    _extra = f", auto-dedup: -{_dd}" if autodedup else ""
                    _tlog(f"[RAW2JSON] Corpus: +{_ls['added']} added, {_ls['duplicates']} duplicates{_extra}")
                with open(log_path, "w", encoding="utf-8") as _lf:
                    _json.dump(_out, _lf)
                _tlog("[RAW2JSON] Finished ✔")
            except Exception as _e:
                import traceback as _tb
                _tlog(f"[RAW2JSON][ERROR] {type(_e).__name__}: {_e}")
                with open(log_path, "w", encoding="utf-8") as _lf:
                    _lf.write(_json.dumps({"status": "error", "error": f"{type(_e).__name__}: {_e}"}))
                with open(txt_log, "a", encoding="utf-8") as _lf:
                    _tb.print_exc(file=_lf)
            # NOTE: deliberately NOT touching st.session_state here — it is a
            # Streamlit LocalProxy that raises outside a script context (threads).
        _ext_list = None
        if raw_ext.strip():
            _ext_list = [e.strip() if e.strip().startswith(".") else "." + e.strip()
                         for e in raw_ext.split(",") if e.strip()]
        threading.Thread(
            target=_run_raw_thread,
            kwargs=dict(
                log_path=os.path.abspath(os.path.join(get_project_root(), "raw2json.progress.json")),
                txt_log=_RAW_LOG,
                root=raw_root.strip(),
                ext_list=_ext_list,
                feed=bool(raw_feed),
                nodata=bool(raw_nodata),
                autodedup=bool(raw_autodedup),
            ),
            daemon=True,
        ).start()
        st.success("Conversion started. Progress bar + live log below.")

    # Live progress + result.
    _raw_prog = os.path.abspath(os.path.join(get_project_root(), "raw2json.progress.json"))
    if os.path.exists(_raw_prog):
        try:
            with open(_raw_prog, "r", encoding="utf-8", errors="ignore") as _lf:
                _raw_txt = _lf.read().strip()
            _raw_data = _json.loads(_raw_txt) if _raw_txt.startswith("{") else {}
        except Exception:
            _raw_data = {}
        # _in_flight: progress file exists and not in a terminal state yet.
        _raw_status = _raw_data.get("status")
        _in_flight = _raw_status is not None and _raw_status in ("running",) and _raw_status != "done" and _raw_status != "error"
        # Keep auto-refreshing whenever the job is (still) running. Drive it off
        # the FILE, not session_state (thread writes are not reliably visible
        # main-side). Refresh unconditionally while in flight so the bar animates.
        if _in_flight:
            st_autorefresh(interval=700, key="raw_refresh")
        elif st.session_state.raw_active:
            st_autorefresh(interval=1000, key="raw_refresh")
        _done = _raw_data.get("done", 0)
        _total = _raw_data.get("total", 0) or 1
        _pct = min(100, round(100.0 * _done / _total))
        if _total:
            st.progress(min(1.0, _done / _total),
                        text=f"Converting… {_done}/{_total} ({_pct}%)")
        if _raw_status == "done":
            _m1, _m2, _m3, _m4 = st.columns(4)
            _m1.metric("Included", _raw_data.get("included", 0))
            _m2.metric("Skipped", _raw_data.get("skipped", 0))
            _m3.metric("Errors", _raw_data.get("errors", 0))
            _m4.metric("Outputs", 3 if _raw_data.get("dataset_jsonl") else 0)
            if _raw_data.get("dataset_jsonl"):
                st.info(
                    f"🤖 Structured dataset ready ({_raw_data['included']} records):\n"
                    f"- `{os.path.basename(_raw_data['dataset_jsonl'])}` (JSONL, one record/line)\n"
                    f"- `{os.path.basename(_raw_data['dataset_json'])}` (single-file JSON)\n"
                    f"- `manifest.json` (summary + per-language counts)"
                )
                st.session_state["crawl_last_dataset"] = _raw_data["dataset_jsonl"]
            if _raw_data.get("fed"):
                _ls = _raw_data["fed"]
                st.success(
                    f"🧠 Fed into learning corpus: **{_ls['added']} added**, "
                    f"{_ls['duplicates']} duplicate → corpus now "
                    f"**{_ls['added'] + _ls['existing']} records**."
                )
        elif _raw_status == "error":
            st.error(f"Raw→JSON failed: {_raw_data.get('error')}")
        elif _in_flight:
            st.info(f"🔄 Converting… ({_done}/{_total}) — {_pct}%")

    # Always-on real-time text log for the conversion (same style as autogen).
    _raw_txt_content = tail_log(_RAW_LOG) if os.path.exists(_RAW_LOG) else ""
    if st.session_state.get("raw_active", False):
        st_autorefresh(interval=1000, key="raw_txt_refresh")
    st.code(
        _raw_txt_content
        or "(no log yet — enable and run the conversion to see activity here in real time)",
        language="text",
    )
    if st.session_state.get("raw_active", False):
        st.info("🔄 Conversion in progress… (real-time log)")
    else:
        st.caption("Conversion idle. Outputs in `_converted/`.")

st.divider()
st.subheader("🦙 MIA Evolution Loop (self-learning)")
st.caption(
    "Run the MIA-LABS evolution loop (variable debate -> equation synthesis -> validation -> "
    "library) on Ollama/qwen2:1.5b. Approved equations are archived into alchimie/new_data/. "
    "Requires a running Ollama server at http://localhost:11434 with the qwen2:1.5b model."
)

_mia_ok = True
try:
    import importlib.util as _ilu
    _mia_spec = _ilu.spec_from_file_location(
        "mia_launcher_ollama",
        os.path.join(project_root, "mia", "launcher_ollama.py"),
    )
    if _mia_spec is None or _mia_spec.loader is None:
        raise ImportError("launcher_ollama.py not found")
    mia_launcher = _ilu.module_from_spec(_mia_spec)
    _mia_spec.loader.exec_module(mia_launcher)
except Exception as _mia_imp_err:
    _mia_ok = False
    st.warning(f"MIA launcher unavailable: {_mia_imp_err}")

# Thread-safe log file for the evolution loop output.
_MIA_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mia_evolution.log")
_MIA_LOG = os.path.abspath(_MIA_LOG)
_MIA_DONE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mia_evolution.done"))
if "mia_running" not in st.session_state:
    st.session_state.mia_running = False


def _run_mia_loop_thread(log_path, done_flag_path, **kwargs):
    """Run the evolution loop in a background thread, streaming stdout to log_path.

    AUDIT-C: this runs in a daemon thread — NEVER touch st.session_state here.
    Completion is signalled via a marker FILE that the main script polls instead.
    """
    import io
    import contextlib
    try:
        with open(log_path, "w", encoding="utf-8") as lf, contextlib.redirect_stdout(lf):
            mia_launcher.run_full_evolution_loop(**kwargs)
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write("\n[MIA] Evolution loop finished.\n")
    except Exception as _e:
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"\n[MIA][ERROR] {type(_e).__name__}: {_e}\n")
    finally:
        # Signal completion via file marker (never write st.session_state from a thread)
        try:
            with open(done_flag_path, "w", encoding="utf-8") as ff:
                ff.write("done")
        except OSError:
            pass


if _mia_ok:
    _m1, _m2, _m3 = st.columns([1, 1, 1])
    with _m1:
        mia_session = st.text_input(
            "Session name (optional)",
            placeholder="alchemy_session_YYYYMMDD_HHMMSS",
            key="mia_session",
        )
    with _m2:
        mia_cycles = st.number_input(
            "Max cycles", min_value=1, max_value=50000, value=5, key="mia_cycles"
        )
    with _m3:
        mia_turns = st.number_input(
            "Turns / cycle", min_value=1, max_value=50000, value=1, key="mia_turns"
        )

    _m4, _m5 = st.columns([1, 1])
    with _m4:
        mia_safety = st.checkbox("Safety gate (block dangerous/quarantine)", value=False, key="mia_safety")
    with _m5:
        if st.button("🚀 Run evolution loop", use_container_width=True, key="mia_go",
                     disabled=st.session_state.mia_running):
            # Normalize session name: workspace_security requires the
            # "alchemy_session_" prefix + safe charset, so auto-prefix if needed.
            _raw = mia_session.strip()
            _session_arg = None
            if _raw:
                _norm = _raw.replace(" ", "_").replace("/", "_").replace("\\", "_")
                _session_arg = _norm if _norm.startswith("alchemy_session_") else f"alchemy_session_{_norm}"
            open(_MIA_LOG, "w", encoding="utf-8").close()
            # Clear previous completion marker
            if os.path.exists(_MIA_DONE):
                os.remove(_MIA_DONE)
            st.session_state.mia_running = True
            _t = threading.Thread(
                target=_run_mia_loop_thread,
                kwargs=dict(
                    log_path=_MIA_LOG,
                    done_flag_path=_MIA_DONE,
                    turns=int(mia_turns),
                    session_name=_session_arg,
                    max_cycles=int(mia_cycles),
                    save_library=True,
                    safety=bool(mia_safety),
                    safety_strict=bool(mia_safety),
                ),
                daemon=True,
            )
            _t.start()
            st.success("Evolution loop started. The log below streams live.")

    # Live, real-time log: auto-refresh while running, stop when finished.
    if os.path.exists(_MIA_LOG):
        _mia_content = tail_log(_MIA_LOG)
        if st.session_state.mia_running:
            st_autorefresh(interval=1500, key="mia_refresh")
            if os.path.exists(_MIA_DONE) or "[MIA] Evolution loop finished" in _mia_content or "[MIA][ERROR]" in _mia_content:
                st.session_state.mia_running = False
        st.code(_mia_content or "(running…)", language="text")
        if st.session_state.mia_running:
            st.info("🔄 Loop running… (auto-refreshing)")
        else:
            st.caption("MIA idle. Library summary lives in mia/session/<session>/library_summary.json.")

# --- Footer ---
st.caption("Captn Agent Runtime | Workspace Mode Active")

# --- Bright Mirror Ponzi Chat Section ---
if st.session_state.bright_mirror_chat.get("active", False):
    st.divider()
    st.subheader("🪞 The Bright Mirror Ponzi - Conversational Space")
    st.info("A safe space for discussion, understanding, and exploring ideas. Not for code or project execution.")
    st.caption("💡 Note: This is an AI conversation partner designed for discussion and mental grounding. It is not a replacement for professional mental health care or crisis support.")
    
    # Check if API key is available
    api_key = st.session_state.openai_config.get("api_key", "")
    model = st.session_state.openai_config.get("model", "gpt-4o-mini")
    mode = st.session_state.openai_config.get("mode", "responses")
    base_url = st.session_state.openai_config.get("base_url", "")
    
    if not api_key:
        st.warning("⚠️ Please enter your OpenAI API key in the sidebar to enable the Bright Mirror conversation.")
    
    # Chat messages display
    chat_container = st.container()
    with chat_container:
        for i, msg in enumerate(st.session_state.bright_mirror_chat.get("messages", [])):
            if msg["role"] == "user":
                st.markdown(f"**You:** {msg['content']}")
            else:
                st.markdown(f"**Bright Mirror:** {msg['content']}")
    
    # Chat input
    user_input = st.text_area("Talk to me... share an idea, a heavy thought, or just see where the conversation goes:", 
                              key="bright_mirror_input", 
                              height=100,
                              value=st.session_state.bright_mirror_chat.get("input_text", ""))
    
    col_chat1, col_chat2 = st.columns([1, 1])
    with col_chat1:
        if st.button("Send Message", type="primary"):
            if user_input and user_input.strip():
                if not api_key:
                    st.error("Please provide an OpenAI API key in the sidebar to enable the Bright Mirror conversation.")
                else:
                    # Add user message to chat
                    st.session_state.bright_mirror_chat["messages"].append({
                        "role": "user",
                        "content": user_input
                    })
                    st.session_state.bright_mirror_chat["input_text"] = ""
                    
                    # Get response from the API
                    with st.spinner("Bright Mirror is thinking..."):
                        ai_response = get_bright_mirror_response(
                            st.session_state.bright_mirror_chat["messages"],
                            api_key,
                            model,
                            base_url,
                            mode
                        )
                    
                    # Add AI response to chat
                    st.session_state.bright_mirror_chat["messages"].append({
                        "role": "assistant",
                        "content": ai_response
                    })
                    st.rerun()
    
    with col_chat2:
        if st.button("Close Chat"):
            st.session_state.bright_mirror_chat["active"] = False
            st.rerun()
