from captn.runtime.base import Plugin
from captn.runtime.base import Message
import logging

logger = logging.getLogger("Runtime")

class PGMWorker(Plugin):
    name = "pgm_worker"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing PGM generation logic...")
        return True

    def execute(self, message: Message) -> None:
        """
        Analyzes an error message and provides a helpful PGM/fix suggestion.
        """
        task_id = message.payload.get("task_id")
        error_msg = message.payload.get("error_message", "Unknown error occurred.")
        
        logger.info(f"[{self.name}] Analyzing error for task {task_id}: {error_msg}")
        
        # In V1, we use a rule-based PGM system. 
        # In V2, this will be an LLM call.
        pgm_map = {
            "Missing required fields in translated data": "Check the Translator plugin. Ensure the source data contains 'title' and 'body' keys.",
            "Priority must be greater than 0": "The Orchestrator rejected this task because the priority was set to 0 or less.",
            "File not found": "The Extractor couldn't find the path provided. Check if the file exists and the path is absolute."
        }
        
        suggestion = pgm_map.get(error_msg, "Check the logs for more details on this failure.")
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "pgm": suggestion,
                "suggested_action": "generate_fix"
            }
        )
        self.bus.publish(response)

    def shutdown(self) -> bool:
        return True
