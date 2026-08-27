from captn.runtime.base import Plugin
from captn.runtime.base import Message
import logging
import time

logger = logging.getLogger("Runtime")

class Translator(Plugin):
    name = "translator"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing translation logic...")
        return True

    def execute(self, message: Message) -> None:
        # Extract the data produced by the Normalizer/Extractor
        data_dict = message.payload.get("data", {})
        task_id = message.payload.get("task_id")
        
        # The raw_text comes from the 'content' key in UniversalData
        raw_text = data_dict.get("content", {}).get("raw_text", "")
        
        logger.info(f"[{self.name}] Translating {len(raw_text)} characters for task {task_id}...")
        
        # Simulate translation: 
        # In a real system, this is where we might do regex cleaning, 
        # markdown formatting, or key-value extraction.
        translated_data = {
            "title": raw_text.split('\n')[0][:50] if raw_text else "Untitled",
            "body": raw_text,
            "metadata": {
                "source": data_dict.get("source_type"),
                "processed_at": time.time()
            }
        }
        
        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "data": translated_data
            }
        )
        self.bus.publish(response)

    def shutdown(self) -> bool:
        return True
