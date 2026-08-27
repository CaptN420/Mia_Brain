from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
import uuid

@dataclass
class UniversalData:
    """The standard format for all data extracted from any source."""
    source_type: str  # e.g., "website", "github", "pdf"
    content: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class AgentTask:
    """The structure of a request from the Orchestrator to a Worker."""
    task_id: str
    action: str
    payload: Dict[str, Any]
    priority: int
    
    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__

@dataclass
class AgentResponse:
    """The standard format for a Worker's response to the Orchestrator."""
    task_id: str
    status: str  # "success", "error", "partial_success"
    data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    suggested_fix: Optional[str] = None # For the "fix.py" generation
