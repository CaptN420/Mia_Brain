from captn.runtime.base import Plugin
from captn.runtime.base import Message
import logging
import os
from captn.runtime.validator import PatchValidator

logger = logging.getLogger("Runtime")

class FixGenerator(Plugin):
    name = "fix_generator"
    
    def __init__(self, bus=None):
        self.bus = bus
        self.validator = PatchValidator()

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Initializing fix generation logic with safety validation...")
        return True

    def execute(self, message: Message) -> None:
        """
        Takes a 'pgm' from the pgm_worker and generates a runnable fix.py script.
        """
        task_id = message.payload.get("task_id")
        pgm = message.payload.get("pgm", "No pgm provided.")
        
        logger.info(f"[{self.name}] Generating fix.py for task {task_id} based on pgm: {pgm}")

        # H-05: honesty about capability. This generator produces a TEMPLATE
        # scaffold - not an actual fix. The message payload says so explicitly
        # so the orchestrator and UI never treat this as a completed repair.
        fix_script_content = f'''# Auto-generated TEMPLATE for task {task_id} (NOT a verified fix)
# PGM (problem graph model): {pgm}
#
# H-05 NOTE: content is a scaffold. A human or a validated code-generation
# step must complete apply_fix() before running this script.

def apply_fix():
    raise NotImplementedError(
        "Template fix generated for task {task_id}. "
        "No automated repair logic was produced - implement apply_fix() first."
    )

if __name__ == "__main__":
    apply_fix()'''
        
        # Save the fix script to the FIX directory at project root
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        fix_dir = os.path.abspath(os.path.join(project_root, "FIX"))
        os.makedirs(fix_dir, exist_ok=True)
        fix_filename = f"fix_{task_id}.py"
        fix_path = os.path.abspath(os.path.join(fix_dir, fix_filename))
        
        # --- SAFETY CHECK ---
        # Wrap the content in a patch object for the validator
        patch_data = {
            "file_path": fix_path,
            "diff": fix_script_content
        }
        
        validation = self.validator.validate_patch(patch_data)
        if not validation["is_safe"]:
            logger.error(f"[{self.name}] SAFETY VIOLATION for task {task_id}: {validation['reason']}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="error",
                payload={"task_id": task_id, "error_message": f"Safety Violation: {validation['reason']}"}
            )
            self.bus.publish(response)
            return

        try:
            with open(fix_path, 'w', encoding='utf-8') as f:
                f.write(fix_script_content)
            logger.info(f"[{self.name}] Fix script generated safely at: {fix_path}")

            response = Message(
                sender=self.name,
                destination="captn",
                type="response",
                payload={
                    "task_id": task_id,
                    "current_step_plugin": self.name,
                    "fix_path": fix_path,
                    # H-05: explicit status so nothing downstream mistakes a
                    # scaffold for an applied repair.
                    "status": "fix_template_generated",
                    "is_verified_fix": False,
                }
            )
            self.bus.publish(response)
        except Exception as e:
            logger.error(f"[{self.name}] Failed to write fix script: {e}")
            response = Message(
                sender=self.name,
                destination="captn",
                type="error",
                payload={"task_id": task_id, "error_message": f"Failed to write fix script: {e}"}
            )
            self.bus.publish(response)

    def shutdown(self) -> bool:
        return True