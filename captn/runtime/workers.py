from typing import Dict, List, Any
from captn.runtime.base import Message, Plugin
import logging
import os

logger = logging.getLogger("Workers")

class BaseWorker(Plugin):
    def __init__(self, name: str, bus=None):
        if bus is not None:
            super().__init__(bus)
        else:
            # Fallback for when bus isn't provided
            self.bus = None
        self.name = name
        self.is_active = True

    def initialize(self) -> bool:
        """Setup resources, connections, etc."""
        logger.info(f"[{self.name}] Initializing...")
        self.is_active = True
        return True

    def execute(self, message: Message) -> None:
        """
        Executes the logic for this specific worker.
        Publishes a response back to the bus.
        """
        payload = message.payload
        project_path = payload.get("project_path")
        files = payload.get("files", [])
        
        logger.info(f"Worker {self.name}: Processing {len(files)} files.")
        
        # Placeholder for actual logic - to be implemented by subclasses
        result = {
            "worker_name": self.name,
            "status": "success",
            "findings": []
        }
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": payload.get("task_id"),
                "current_step_plugin": self.name,
                "valid": True,
                "result": result
            }
        )
        
        if self.bus:
            self.bus.publish(response)
        else:
            logger.warning(f"Worker {self.name}: Response generated but bus not available.")


class SyntaxWorker(BaseWorker):
    def __init__(self, bus=None):
        super().__init__("syntax_worker", bus)

    def execute(self, message: Message) -> None:
        payload = message.payload
        files = payload.get("files", [])
        findings = []
        
        for file_path in files:
            if not os.path.exists(file_path):
                findings.append(f"File missing: {file_path}")
        
        result = {
            "worker_name": self.name,
            "status": "success" if not findings else "warning",
            "findings": findings
        }
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": payload.get("task_id"),
                "current_step_plugin": self.name,
                "valid": True,
                "result": result
            }
        )
        
        if self.bus:
            self.bus.publish(response)


class BugWorker(BaseWorker):
    def __init__(self, bus=None):
        super().__init__("bug_worker", bus)

    def execute(self, message: Message) -> None:
        payload = message.payload
        files = payload.get("files", [])
        findings = []
        
        for file_path in files:
            if os.path.exists(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                except UnicodeDecodeError:
                    # E-08: non-UTF8 file - record it instead of crashing the scan.
                    findings.append(f"Skipped non-UTF8 (binary/encoded) file: {file_path}")
                    continue
                except OSError as e:
                    findings.append(f"Unreadable file {file_path}: {e}")
                    continue
                if "eval(" in content or "exec(" in content:
                    findings.append(f"Potential security risk (eval/exec) in {file_path}")
        
        result = {
            "worker_name": self.name,
            "status": "success" if not findings else "warning",
            "findings": findings
        }
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": payload.get("task_id"),
                "current_step_plugin": self.name,
                "valid": True,
                "result": result
            }
        )
        
        if self.bus:
            self.bus.publish(response)
