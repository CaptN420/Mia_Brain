import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from captn.runtime.base import Message, Task, Rule, Pipeline
from captn.runtime.manager import PluginManager
from captn.runtime.runtime import MessageBus, StateStore, Captn

def main():
    print(f"--- Starting Agent Runtime at {project_root} ---")

    # Setup components
    bus = MessageBus()
    store = StateStore()
    plugins_path = os.path.abspath(os.path.join(project_root, "plugins"))
    manager = PluginManager(plugins_path, bus)
    
    captn = Captn(bus, manager, store)

    # 1. Register Rules
    captn.rule_engine.add_rule(Rule(
        name="positive_priority",
        description="Tasks must have a positive priority.",
        condition=lambda t: t.priority > 0,
        error_message="Priority must be greater than 0."
    ))

    # 2. Register Pipelines
    # Pipeline: extractor -> normalizer -> translator -> validator
    data_pipeline = Pipeline(
        id="data_processing_v1",
        steps=["extractor", "normalizer", "translator", "validator"]
    )
    captn.register_pipeline(data_pipeline)

    # Optional pipeline: crawl an open-source GitHub repo, then process it
    # local-file-style through the existing extractor -> ... chain.
    github_pipeline = Pipeline(
        id="github_crawl_v1",
        steps=["crawler", "extractor", "normalizer", "translator", "validator"]
    )
    captn.register_pipeline(github_pipeline)

    # Pipeline: transform a raw local directory into structured JSON, then feed
    # it into the agent's analysis workflow (syntax + bug workers -> Thinker
    # synthesis). `raw2json` emits a dataset JSONL which downstream workers can
    # consume as their `file_path` (structured handoff).
    #
    # NOTE: the legacy extractor/normalizer/translator/validator plugins are not
    # loaded by this runtime (their plugin files live under captn/workers/, not a
    # scanned plugins/ dir), so the chain uses the analysis workers that are.
    raw2json_pipeline = Pipeline(
        id="raw2json_v1",
        steps=["raw2json", "syntax_worker", "bug_worker"]
    )
    captn.register_pipeline(raw2json_pipeline)

    # Start Bus thread
    bus_thread = threading.Thread(target=bus.run, daemon=True)
    bus_thread.start()

    # Load Plugins
    manager.discover_plugins()
    manager.load_plugin("extractor.py")
    manager.load_plugin("normalizer.py")
    manager.load_plugin("translator.py")
    manager.load_plugin("validator.py")
    manager.load_plugin("pgm_worker.py")

    # Optional crawler worker (networked). Non-fatal if requests is missing.
    try:
        from captn.workers.crawler import CrawlerWorker  # safe import
        crawler = CrawlerWorker(bus)
        if crawler.initialize():
            manager.register_plugin(crawler)
    except Exception as e:  # never break the core pipeline over the crawler
        print(f"[warn] Crawler worker not loaded: {e}")

    # Raw -> structured JSON worker (local files). Non-fatal if module missing.
    try:
        from captn.workers.raw2json_worker import Raw2JsonWorker  # safe import
        raw2json_w = Raw2JsonWorker(bus)
        if raw2json_w.initialize():
            manager.register_plugin(raw2json_w)
    except Exception as e:  # never break the core pipeline over this worker
        print(f"[warn] Raw2Json worker not loaded: {e}")

    # 3. Simulate a Real File Task
    # FIX: Ensure 'file_path' is directly in the payload for the extractor
    test_file = os.path.abspath(os.path.join(project_root, "test_input.txt"))
    
    mock_task = Message(
        sender="user",
        destination="captn",
        type="task",
        payload={
            "task_id": "1001",
            "pipeline_id": "data_processing_v1",
            "action": "extractor",
            "source": "local_file",
            "priority": 10,
            "file_path": test_file  # Moved to top level of payload
        }
    )
    
    print("\n--- Injecting REAL File Pipeline Task ---\n")
    bus.publish(mock_task)

    # Wait for completion (4 steps + pgm_worker step)
    time.sleep(7)
    print("\n--- Final State Check ---")
    final_state = store.get("1001")
    print(f"Final State for Task 1001: {final_state}")
    print("--- Shutdown ---")

if __name__ == "__main__":
    main()
