#!/usr/bin/env python3
"""CLI commands: pipeline, raw2json (need CaptN runtime)."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger("cli._orchestrator")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── Command: pipeline ──────────────────────────────────────────────
def _cmd_pipeline(args):
    from captn.runtime.runtime import Captn, MessageBus, StateStore, PluginManager, Pipeline  # type: ignore[import-not-found]

    logger.info("Starting Captn orchestrator...")

    bus = MessageBus()
    store = StateStore()
    plugins_path = PROJECT_ROOT / "captn" / "plugins"
    manager = PluginManager(str(plugins_path), bus)

    captn = Captn(bus, manager, store)

    if "analysis" not in captn.pipelines:
        analysis_pipeline = Pipeline(id="analysis", steps=["syntax_worker", "bug_worker"])
        captn.register_pipeline(analysis_pipeline)

    if not captn.load_project_context(args.project, args.name or "cli-project"):
        logger.error("Failed to load project: %s", args.project)
        return

    bus_thread = threading.Thread(target=bus.run, daemon=True)
    bus_thread.start()

    captn.run_analysis_pipeline()

    logger.info("Pipeline running — polling for completion (max %ds)...", args.wait)
    deadline = time.monotonic() + args.wait
    all_states = None
    while time.monotonic() < deadline:
        time.sleep(0.5)
        all_states = store.get_all_analysis_statuses()
        if all_states:
            _, state = max(all_states, key=lambda x: x[0])
            status = state.get("status", "running")
            if status in ("completed", "failed", "error", "cancelled"):
                logger.info("Pipeline finished with status: %s", status)
                break

    all_states = store.get_all_analysis_statuses()
    if all_states:
        latest_task_id, state = max(all_states, key=lambda x: x[0])
        logger.info("Latest analysis task: %s", latest_task_id)
        if "analysis_report" in state:
            print(json.dumps(state["analysis_report"], indent=2))
        else:
            print(json.dumps(state, indent=2))
    else:
        logger.warning("No analysis tasks found in state store")


# ── Command: raw2json ──────────────────────────────────────────────
def _cmd_raw2json(args):
    from captn.runtime.base import Message  # type: ignore[import-not-found]
    from captn.workers.data_ingestion.raw2json_worker import Raw2JsonWorker  # type: ignore[import-not-found]

    worker = Raw2JsonWorker(bus=None)
    worker.initialize()

    message = Message(
        sender="cli",
        destination="raw2json_worker",
        type="task",
        payload={
            "task_id": args.task_id or "cli-raw2json",
            "project_path": args.project,
            "action": "raw2json",
            "priority": 1,
        },
    )

    worker.execute(message)
    logger.info("Raw2Json conversion complete (check output)")


# ── Registration ───────────────────────────────────────────────────
def register_cli(subparsers) -> None:
    # pipeline
    p = subparsers.add_parser("pipeline", help="Run full Captn pipeline")
    p.add_argument("--project", "-p", required=True, help="Project directory path")
    p.add_argument("--name", "-n", help="Project name")
    p.add_argument("--pipeline", default="analysis", help="Pipeline ID to run")
    p.add_argument("--wait", "-w", type=int, default=10, help="Seconds to wait for completion")
    p.set_defaults(func=_cmd_pipeline)

    # raw2json
    p = subparsers.add_parser("raw2json", help="Convert directory to JSONL dataset")
    p.add_argument("--project", "-p", required=True, help="Project directory path")
    p.add_argument("--task-id", help="Task ID (default: auto)")
    p.set_defaults(func=_cmd_raw2json)