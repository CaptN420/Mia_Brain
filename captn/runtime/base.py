from typing import Any, Dict, List, Optional, Callable
from dataclasses import dataclass, field
import uuid
import time
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger("Runtime")

@dataclass
class Message:
    """The universal communication unit for all components."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sender: str = "system"
    destination: str = "orchestrator"
    type: str = "task"  # task, state_update, error, response
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

@dataclass
class Task:
    """The core unit of work defined in the schema."""
    task_id: str
    source: str
    action: str  # The primary action requested
    pipeline_id: str  # Added to support pipeline logic
    priority: int = 1
    payload: Dict[str, Any] = field(default_factory=dict)
    retries: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending" # pending, running, completed, failed
    file_path: Optional[str] = None # Added to support direct file pathing

    def to_json(self) -> Dict[str, Any]:
        return self.__dict__

@dataclass
class Rule:
    """A strict rule enforced by the Orchestrator."""
    name: str
    description: str
    condition: Callable[[Task], bool]  # A function that takes a Task and returns True/False
    error_message: str

@dataclass
class Pipeline:
    """A sequence of actions that a task must go through."""
    id: str
    steps: List[str]  # e.g., ["extractor", "normalizer", "translator", "validator"]

class Plugin:
    """The base class for all workers and capabilities."""
    name: str = "base"

    def __init__(self, bus):
        self.bus = bus
        self.is_active = False

    def initialize(self) -> bool:
        """Setup resources, connections, etc."""
        logger.info(f"[{self.name}] Initializing...")
        self.is_active = True
        return True

    def execute(self, message: Message) -> None:
        """Process a message and publish a response back to the bus."""
        # To be implemented by subclasses
        pass

    def shutdown(self) -> bool:
        """Cleanup resources."""
        logger.info(f"[{self.name}] Shutting down...")
        self.is_active = False
        return True
