from captn.runtime.base import Plugin
from captn.runtime.base import Message
from captn.runtime.schemas import UniversalData
import logging
import time

logger = logging.getLogger("Runtime")

class Normalizer(Plugin):
    name = "normalizer"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing parser/normalizer...")
        return True

    def execute(self, message: Message) -> None:
        # In a real system, this would parse raw HTML/JSON/XML
        # and convert it into our universal JSON format.
        data_dict = message.payload.get("data", {})
        task_id = message.payload.get("task_id")

        logger.info(f"[{self.name}] Normalizing data for task {task_id}...")
        
        # Simulate normalization
        normalized_data = UniversalData(
            source_type="website",
            content={"title": "Example Site", "body": "Normalized content here..."},
            metadata={"encoding": "utf-8", "clean": True}
        )
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "data": normalized_data.__dict__
            }
        )
        self.bus.publish(response)

    def shutdown(self) -> bool:
        return True
