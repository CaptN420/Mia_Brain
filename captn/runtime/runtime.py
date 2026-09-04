import queue
import threading
import time
import os
import logging
from typing import Any, Dict, List, Optional, Callable, Tuple
from captn.runtime.base import Message, Task, Rule, Pipeline
from captn.runtime.manager import PluginManager
from tools.scanner import ProjectScanner, ProjectManifest
from captn.runtime.workers import SyntaxWorker, BugWorker
from captn.workers.code_generation.fix_generator import FixGenerator
from captn.workers.data_ingestion.raw2json_worker import Raw2JsonWorker
from captn.workers.code_generation.deterministic_coder import DeterministicCoder
from captn.runtime.thinker import Thinker
from captn.runtime.patch_generator import PatchGenerator
from captn.runtime.llm_provider import OpenAIProvider, FallbackLLM
from captn.runtime.mirror_agent import MirrorAgent
from tools.math_validator import MathValidator
from captn.workers.orchestration.smart_router import SmartRouter

# Import Alchimie Library Manager
import sys
tools_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'tools')
if tools_dir not in sys.path:
    sys.path.insert(0, tools_dir)
from alchimie_library_manager import AlchimieLibraryManager

# Setup logging - do not remove existing log file to preserve history
# E-04: pin the log file to the project root (absolute) so the dashboard and
# every entry point watch the SAME file regardless of the current working dir.
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
log_file = os.path.join(_PROJECT_ROOT, "runtime.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(log_file, mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("Runtime")

class MessageBus:
    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.subscribers: Dict[str, List[Callable[[Message], None]]] = {}
        self.running = True
        # H-01: bounded worker pool replaces unbounded thread-per-message.
        # Gives backpressure, caps concurrency, keeps handler ordering sane.
        from concurrent.futures import ThreadPoolExecutor
        self._executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="bus")

    def subscribe(self, destination: str, callback: Callable[[Message], None]):
        if destination not in self.subscribers:
            self.subscribers[destination] = []
        self.subscribers[destination].append(callback)

    def publish(self, message: Message):
        logger.info(f"Bus: Routing {message.type} from {message.sender} to {message.destination}")
        if message.destination in self.subscribers:
            for callback in self.subscribers[message.destination]:
                self._executor.submit(callback, message)
        else:
            logger.warning(f"Bus: No subscribers for {message.destination}")

    def run(self):
        logger.info("Bus: Started.")
        while self.running:
            try:
                message = self.queue.get(timeout=1)
                self.publish(message)
                self.queue.task_done()
            except queue.Empty:
                continue

class StateStore:
    def __init__(self):
        self.store: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self.snapshots: Dict[str, Dict[str, str]] = {} # task_id -> {path: content}

    def update(self, task_id: str, data: Dict[str, Any]):
        with self._lock:
            if task_id not in self.store:
                self.store[task_id] = {}
            self.store[task_id].update(data)
            logger.info(f"StateStore: Updated {task_id} -> {self.store[task_id]}")

    def append(self, task_id: str, key: str, item: Any):
        """N-03/H-06: thread-safe read-modify-write for list fields.
        Replaces the racy get->mutate->update sequence in process_response."""
        with self._lock:
            if task_id not in self.store:
                self.store[task_id] = {}
            self.store[task_id].setdefault(key, []).append(item)
            return self.store[task_id][key]

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            # H-06: return a shallow copy so callers can't mutate shared
            # state outside the lock.
            v = self.store.get(task_id)
            return dict(v) if isinstance(v, dict) else v

    def get_all_analysis_statuses(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Thread-safe method to retrieve all analysis task IDs and their states."""
        with self._lock:
            result = []
            for k, v in self.store.items():
                if k.startswith("analysis_"):
                    result.append((k, v))
            return result

    def get_all_task_states(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Thread-safe method to retrieve all task IDs and their states."""
        with self._lock:
            result = []
            for k, v in self.store.items():
                result.append((k, v))
            return result

    def create_snapshot(self, task_id: str, file_paths: List[str]):
        """Captures the current content of files before a fix is applied."""
        with self._lock:
            snapshot = {}
            for path in file_paths:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        snapshot[path] = f.read()
            self.snapshots[task_id] = snapshot
            logger.info(f"StateStore: Created snapshot for task {task_id}")

    def rollback(self, task_id: str):
        """Restores files from the last snapshot.

        N-06: backs up the CURRENT content first (so a rollback is itself
        reversible), and collects failures instead of dying mid-loop.
        """
        with self._lock:
            snapshot = self.snapshots.get(task_id)
            if not snapshot:
                logger.error(f"StateStore: No snapshot found for task {task_id}")
                return False

            # 1. Safety net: preserve current state before overwriting.
            pre_rollback = {}
            for path in snapshot:
                if os.path.exists(path):
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            pre_rollback[path] = f.read()
                    except OSError as e:
                        logger.warning(f"StateStore: Could not read current {path}: {e}")
            if pre_rollback:
                self.snapshots[f"{task_id}_pre_rollback"] = pre_rollback

            # 2. Restore; report any file that fails rather than crashing halfway silently.
            failures = []
            for path, content in snapshot.items():
                try:
                    tmp = path + ".captn_tmp"
                    with open(tmp, 'w', encoding='utf-8') as f:
                        f.write(content)
                    os.replace(tmp, path)  # atomic on same filesystem
                    logger.info(f"StateStore: Rolled back {path}")
                except OSError as e:
                    failures.append(f"{path}: {e}")
                    logger.error(f"StateStore: Rollback failed for {path}: {e}")
            if failures:
                logger.error(f"StateStore: Rollback for {task_id} had {len(failures)} failure(s)")
            return True

class RuleEngine:
    def __init__(self):
        self.rules: List[Rule] = []

    def add_rule(self, rule: Rule):
        self.rules.append(rule)

    def validate_task(self, task: Task) -> (bool, Optional[str]):
        for rule in self.rules:
            if not rule.condition(task):
                return False, rule.error_message
        return True, None

class Captn:
    """The Orchestrator - The Brain of the system."""
    def __init__(self, bus, manager: PluginManager, store: StateStore):
        self.bus = bus
        self.manager = manager
        self.store = store
        self.rule_engine = RuleEngine()
        self.pipelines: Dict[str, Pipeline] = {}
        
        # Context-aware properties
        self.active_project_path: Optional[str] = None
        self.active_project_name: Optional[str] = None
        self.active_manifest: Optional[ProjectManifest] = None
        
        # Initialize Alchimie Library Manager
        alchimie_library_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'alchimie', 'library')
        self.alchimie_manager = AlchimieLibraryManager(alchimie_library_dir)
        
        # Register workers with the bus
        self.manager.register_plugin(SyntaxWorker(bus))
        self.manager.register_plugin(BugWorker(bus))
        self.manager.register_plugin(FixGenerator(bus))
        self.manager.register_plugin(Raw2JsonWorker(bus))
        # Deterministic-first code generation (no LLM) for autogen pipelines.
        self.manager.register_plugin(DeterministicCoder(bus))
        
        # Initialize MirrorAgent with Alchimie manager
        self.mirror_agent = MirrorAgent(bus, self.alchimie_manager)
        self.mirror_agent.initialize()
        
        # Initialize components
        self.thinker = Thinker(self.alchimie_manager)
        self.patch_generator = PatchGenerator()
        self.math_validator = MathValidator()

        # SmartRouter — routage déterministe des workers par description de tâche
        self.smart_router = SmartRouter()

        # Seed the Fragment Registry with all available deterministic fragments
        try:
            from captn.runtime.seed_registry import seed_all as _seed_all
            _seed_all()
            logger.info("Fragment registry seeded with deterministic fragments")
        except Exception as e:
            logger.warning("Fragment registry seeding failed: %s", e)

        # Initialize LLM Provider and Fallback Component for Last-Resort Recovery
        self.llm_provider = OpenAIProvider(model="gpt-4o-mini", mode="responses")
        self.fallback_llm = FallbackLLM(llm_provider=self.llm_provider)

        self.bus.subscribe("captn", self.handle_message)
        self.bus.subscribe("pgm_worker", self.handle_message)
        self.bus.subscribe("fix_generator", self.handle_message)
        self.bus.subscribe("mirror_agent", self.handle_mirror_agent_message)
        self.bus.subscribe("ui_control", self.handle_ui_command)

    def register_pipeline(self, pipeline: Pipeline):
        self.pipelines[pipeline.id] = pipeline
        logger.info(f"Captn: Registered pipeline {pipeline.id} with steps: {pipeline.steps}")

    def load_project_context(self, path: str, name: str) -> bool:
        """Updates the active workspace for the runtime using the ProjectScanner."""
        abs_path = os.path.abspath(path)
        if os.path.isdir(abs_path):
            try:
                scanner = ProjectScanner(abs_path)
                self.active_manifest = scanner.scan()
                self.active_project_path = abs_path
                self.active_project_name = name
                logger.info(f"Captn: CONTEXT LOADED - Project: {name} at {abs_path}")
                return True
            except Exception as e:
                logger.error(f"Captn: Scanner error - {str(e)}")
                return False
        logger.error(f"Captn: FAILED to load project. Path {abs_path} is not a directory.")
        return False

    def run_analysis_pipeline(self):
        """Executes the full Analysis Pipeline: Workers -> Thinker -> UI."""
        if not self.active_manifest:
            logger.warning("Captn: Cannot run analysis - no project context loaded.")
            return

        task_id = f"analysis_{int(time.time())}"
        logger.info(f"Captn: Starting Analysis Pipeline for {self.active_project_name} (Task: {task_id})")
        
        # Register the analysis pipeline if not already registered
        if "analysis" not in self.pipelines:
            analysis_pipeline = Pipeline(
                id="analysis",
                steps=["syntax_worker", "bug_worker"]
            )
            self.register_pipeline(analysis_pipeline)
        
        # 1. Prepare payload for workers
        payload = {
            "task_id": task_id,
            "project_path": self.active_project_path,
            "files": self.active_manifest.python_files,
            "action": "analysis_pipeline",
            "pending_count": len(self.pipelines["analysis"].steps)
        }

        # 2. Dispatch to all workers in parallel
        # We'll track results in the store
        self.store.update(task_id, {
            "status": "analyzing",
            "pipeline": "analysis",
            "worker_results": [],
            "pending_count": len(self.pipelines["analysis"].steps)
        })

        # For this implementation, we'll manually trigger the workers 
        # and then wait for them to report back to 'captn' via the bus.
        # In a production system, we'd use a proper coordinator/waiter.
        
        # For simplicity in Phase 2, we'll simulate the parallel dispatch:
        for worker_name in ["syntax_worker", "bug_worker"]:
            self.dispatch_to_plugin(task_id, worker_name, Message(
                sender="captn",
                destination="captn",
                type="task",
                payload=payload
            ))

        # Note: The results will come back via process_response.
        # Once the last worker finishes, we'll trigger the Thinker.
        # To simplify, we'll trigger Thinker when the UI signals 'analysis_complete'.
        # Or, we can add a 'coordinator' that counts responses.

    def resolve_pipeline_for_task(self, description: str, top_k: int = 2) -> List[str]:
        """Utilise SmartRouter pour générer dynamiquement les steps d'un pipeline.

        Args:
            description: Description de la tâche (ex. "generate code", "analyse project")
            top_k: Nombre de workers à inclure

        Retourne:
            Liste ordonnée de noms de workers (steps de pipeline)
        """
        steps = self.smart_router.resolve_pipeline_steps(description, top_k=top_k)
        logger.info(f"SmartRouter: '{description[:60]}' -> pipeline steps: {steps}")
        return steps

    def handle_message(self, message: Message):
        logger.info(f"Captn: Received {message.type} from {message.sender}")
        
        if message.type == "task":
            self.process_task(message)
        elif message.type == "error":
            self.handle_error(message)
        elif message.type == "response":
            self.process_response(message)
        elif message.type == "ui_command":
            self.handle_ui_command(message)

    def process_task(self, message: Message):
        payload = message.payload
        task_id = payload.get("task_id")
        pipeline_id = payload.get("pipeline_id", "default")
        
        pipeline = self.pipelines.get(pipeline_id)

        if not pipeline:
            # SmartRouter fallback: générer un pipeline dynamique via la description
            description = payload.get("description") or payload.get("topic") or ""
            if description:
                logger.info(f"Captn: No pipeline '{pipeline_id}' found. Using SmartRouter for: '{description[:60]}'")
                steps = self.resolve_pipeline_for_task(description, top_k=2)
                pipeline = Pipeline(id=pipeline_id, steps=steps)
                self.register_pipeline(pipeline)
            else:
                logger.warning(f"Captn: No pipeline found for {pipeline_id}. Using default logic.")
                self.dispatch_to_plugin(task_id, payload.get("action"), message)
                return
        
        # 1. Validate against Rules
        task = Task(**payload)
        is_valid, error = self.rule_engine.validate_task(task)
        if not is_valid:
            logger.error(f"Captn: RULE VIOLATION - {error}")
            self.bus.publish(Message(sender="captn", destination="user", 
                                      type="error", payload={"error": error, "task_id": task_id}))
            return
        
        # 2. Start Pipeline Execution
        logger.info(f"Captn: Starting Pipeline {pipeline_id}")
        self.store.update(task_id, {
            "status": "started",
            "current_step": 0,
            "pipeline": pipeline_id,
            # Preserve the original task args + priority so downstream steps
            # receive valid Task fields (the pipeline progression forwards a
            # Task-shaped payload, not the rich worker response payload).
            "task_args": payload.get("payload", {}) if isinstance(payload.get("payload"), dict) else {},
            "priority": task.priority,
        })
        self.dispatch_to_plugin(task_id, pipeline.steps[0], message)

    def dispatch_to_plugin(self, task_id: str, plugin_name: str, message: Message):
        plugin = self.manager.get_plugin(plugin_name)
        if plugin:
            logger.info(f"Captn: Dispatching {task_id} to {plugin_name}")
            message.payload["current_step_plugin"] = plugin_name
            plugin.execute(message)
        else:
            logger.error(f"Captn: ERROR - Plugin {plugin_name} not loaded.")

    def process_response(self, message: Message):
        task_id = message.payload.get("task_id") or "unknown_task"
        plugin_name = message.payload.get("current_step_plugin")
        
        # Collect worker results for analysis/synthesis
        # N-03: use the atomic store.append instead of get->mutate->update,
        # which lost results when workers finished concurrently.
        if plugin_name and plugin_name != "captn":
            worker_data = {
                "plugin": plugin_name,
                "valid": message.payload.get("valid", True),
                "payload": message.payload
            }
            self.store.append(task_id, "worker_results", worker_data)

        state = self.store.get(task_id)
        if not state:
            logger.warning(f"Captn: No state found for {task_id}")
            return

        pipeline_id = state.get("pipeline")
        pipeline = self.pipelines.get(pipeline_id)
        
        if not pipeline:
            logger.warning(f"Captn: Pipeline {pipeline_id} no longer exists.")
            return

        # --- NEW: Analysis Pipeline Coordinator Logic ---
        if pipeline_id == "analysis":
            pending_count = state.get("pending_count", 0)
            
            # Decrement pending count
            if pending_count > 0:
                pending_count -= 1
                self.store.update(task_id, {"pending_count": pending_count})
                
            if pending_count == 0:
                logger.info(f"Captn: All workers finished for analysis pipeline {pipeline_id} (task {task_id}).")
                self.store.update(task_id, {"status": "completed"})
                
                # Trigger Thinker Synthesis
                worker_results = state.get("worker_results", [])
                synthesis = self.thinker.synthesize(worker_results)
                self.store.update(task_id, {"analysis_report": synthesis})
                logger.info(f"Captn: Analysis Synthesis Complete for {task_id}")
            return

        # Standard Pipeline progression (for non-analysis pipelines)
        current_step_idx = state.get("current_step", 0)
        next_step_idx = current_step_idx + 1

        # Check if the plugin reported a success or failure
        is_success = message.payload.get("valid", True)
        
        if not is_success:
            # --- NEW: Self-Healing Logic ---
            error_msg = message.payload.get("error_message", "Unknown error.")
            logger.warning(f"Captn: Step {current_step_idx} ({plugin_name}) FAILED. Initiating self-healing...")
            
            # Trigger the last-resort LLM fallback directly
            failure_context = {
                "task_id": task_id,
                "plugin": plugin_name,
                "error": error_msg,
                "state": state
            }
            try:
                self.trigger_fallback_recovery(failure_context)
            except Exception as e:
                logger.error(f"Captn: Fallback recovery failed with unhandled exception: {e}")
                self.bus.publish(Message(
                    sender="captn",
                    destination="user",
                    type="error",
                    payload={"error": f"Self-healing failed: {e}", "task_id": task_id}
                ))
            return

        # Standard Pipeline progression
        if next_step_idx < len(pipeline.steps):
            next_plugin_name = pipeline.steps[next_step_idx]
            logger.info(f"Captn: Step {current_step_idx} ({plugin_name}) complete. Moving to {next_step_idx} ({next_plugin_name})")

            self.store.update(task_id, {"current_step": next_step_idx})

            # Build a Task-shaped payload for the next step. The previous
            # worker's response carried rich fields (data, dataset_jsonl, ...)
            # that are NOT valid Task kwargs, so we forward only:
            #   - the original task args (task_args stored at task start)
            #   - the structured handoff file (file_path) from this response
            #   - task bookkeeping fields.
            task_args = state.get("task_args", {})
            if not isinstance(task_args, dict):
                task_args = {}
            next_payload = {
                "task_id": task_id,
                "pipeline_id": pipeline_id,
                "action": next_plugin_name,
                "source": "internal_pipeline",
                "priority": state.get("priority", 1),
                "file_path": message.payload.get("file_path"),
                "payload": task_args,
            }
            new_message = Message(
                sender="captn",
                destination="captn",
                type="task",
                payload=next_payload,
            )
            self.dispatch_to_plugin(task_id, next_plugin_name, new_message)
        else:
            logger.info(f"Captn: Pipeline {pipeline_id} complete for task {task_id}.")
            self.store.update(task_id, {"status": "completed"})
            # N-07: synthesis now happens ONLY in the analysis coordinator
            # branch above (pending_count == 0). The duplicate path that
            # could double-fire the Thinker was removed.

    def handle_error(self, message: Message):
        logger.error(f"Captn: Handling error from {message.sender} for task {message.payload.get('task_id')}")

    def handle_mirror_agent_message(self, message: Message):
        """Handles messages from the MirrorAgent."""
        logger.info(f"Captn: Received {message.type} from {message.sender} (MirrorAgent)")
        if message.type == "response":
            # Process the mirror agent response
            task_id = message.payload.get("task_id", "unknown_task")
            self.store.update(task_id, {"mirrored_reasoning": message.payload.get("mirrored_reasoning")})
            logger.info(f"Captn: MirrorAgent response processed for task {task_id}")

    def handle_ui_command(self, message: Message):
        """Handles commands directly from the UI Dashboard."""
        cmd = message.payload.get("command")
        task_id = message.payload.get("task_id")
        
        if cmd == "run_fix":
            self.bus.publish(Message(
                sender="ui",
                destination="fix_generator",
                type="task",
                payload={
                    "task_id": task_id,
                    "action": "fix_generator",
                    "payload": {"tip": "Apply fix manually or via generator"}
                }
            ))
        elif cmd == "rollback":
            success = self.store.rollback(task_id)
            if success:
                logger.info(f"Captn: UI-triggered rollback successful for {task_id}")
                self.bus.publish(Message(
                    sender="captn",
                    destination="user",
                    type="info",
                    payload={"message": f"Rollback successful for {task_id}"}
                ))
            else:
                logger.warning(f"Captn: UI-triggered rollback failed for {task_id}")

    def trigger_fallback_recovery(self, failure_context: Dict[str, Any]):
        """Triggers the last-resort LLM fallback to generate a recovery proposal."""
        logger.info("Captn: Triggering LAST-RESORT LLM FALLBACK for recovery...")
        
        # Generate proposal using FallbackLLM and OllamaProvider
        proposal = self.fallback_llm.generate_recovery_proposal(failure_context)
        
        if proposal.get("requires_validation", False):
            logger.info(f"Captn: Fallback generated recovery proposal. Sending to normal pipeline for validation.")
            # Publish the proposal back to the bus to re-enter the normal architecture (Workers -> Thinker -> Validators -> Orchestrator)
            recovery_message = Message(
                sender="fallback_llm",
                destination="captn",
                type="recovery_proposal",
                payload={
                    "proposal": proposal,
                    "failure_context": failure_context
                }
            )
            self.bus.publish(recovery_message)
        else:
            logger.error("Captn: Fallback proposal did not include requires_validation=True. Rejecting.")
