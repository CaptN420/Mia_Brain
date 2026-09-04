from captn.runtime.base import Plugin
from captn.runtime.base import Message
import logging

logger = logging.getLogger("Runtime")

class Validator(Plugin):
    name = "validator"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing final validation rules...")
        return True

    def execute(self, message: Message) -> None:
        # The data here is the result of the Translator
        data = message.payload.get("data", {})
        task_id = message.payload.get("task_id")
        
        logger.info(f"[{self.name}] Final validation for task {task_id}...")
        
        # Example Validation: Ensure we have a title and body
        is_valid = "title" in data and "body" in data
        
        if is_valid:
            logger.info(f"Validation PASSED for task {task_id}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "valid": True,
                    "data": data
                }
            )
        else:
            logger.warning(f"Validation FAILED for task {task_id}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "valid": False,
                    "error_message": "Missing required fields in translated data."
                }
            )
            
        self.bus.publish(response)

    def shutdown(self) -> bool:
        return True
