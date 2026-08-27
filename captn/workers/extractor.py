from captn.runtime.base import Plugin
from captn.runtime.base import Message
from captn.runtime.schemas import UniversalData
import logging
import os

logger = logging.getLogger("Runtime")

class Extractor(Plugin):
    name = "extractor"

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Connecting to local filesystem...")
        return True

    def execute(self, message: Message) -> None:
        # The payload now contains a "file_path"
        file_path = message.payload.get("file_path")
        task_id = message.payload.get("task_id")

        if not file_path:
            logger.error(f"[{self.name}] No file_path provided in payload for task {task_id}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="error",
                payload={"task_id": task_id, "error_message": "Missing file_path in payload"}
            )
            self.bus.publish(response)
            return

        if not os.path.exists(file_path):
            logger.error(f"[{self.name}] File not found: {file_path}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="error",
                payload={"task_id": task_id, "error_message": f"File not found: {file_path}"}
            )
            self.bus.publish(response)
            return

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_content = f.read()
            
            logger.info(f"[{self.name}] Successfully read {len(raw_content)} bytes from {file_path}")

            # Wrap in the UniversalData schema
            universal_data = UniversalData(
                source_type="local_file",
                content={"raw_text": raw_content},
                metadata={"file_name": os.path.basename(file_path)}
            )
            
            response = Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "data": universal_data.__dict__
                }
            )
            self.bus.publish(response)
        except Exception as e:
            logger.error(f"[{self.name}] Error reading file: {e}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="error",
                payload={"task_id": task_id, "error_message": str(e)}
            )
            self.bus.publish(response)

    def shutdown(self) -> bool:
        return True
