import os
import sys
import time
import threading

# Add the project root to the path
project_root = "/home/captn/hermes/CaptN_BRAIN"
sys.path.insert(0, project_root)

from captn.runtime.runtime import MessageBus, StateStore, Captn
from captn.runtime.manager import PluginManager
from captn.runtime.base import Message

# Initialize components
bus = MessageBus()
store = StateStore()
plugins_dir = os.path.join(project_root, "captn", "workers")
plugin_manager = PluginManager(plugins_dir, bus)

captn = Captn(bus, plugin_manager, store)

# Start the bus in a background thread
threading.Thread(target=bus.run, daemon=True).start()

# Test dispatching a message
print("Testing message routing...")
test_message = Message(
    sender="test",
    destination="captn",
    type="task",
    payload={"task_id": "test_task_123", "action": "test_action"}
)

bus.publish(test_message)
time.sleep(2)  # Give the bus time to process

print("Test completed. Check runtime.log for entries.")