#!/usr/bin/env python3
"""pipeline_trace — Parse les logs CaptN pour produire une timeline structurée.

Analyse runtime.log, mia_evolution.log, et bus trace pour reconstruire
la séquence d'événements d'un pipeline : workers invoqués, timing,
erreurs, messages bus, états finals.

Usage:
    python tools/pipeline_trace.py --log runtime.log
    python tools/pipeline_trace.py --log runtime.log --output trace.json
    python tools/pipeline_trace.py --log runtime.log --timeline
    python tools/pipeline_trace.py --log runtime.log --errors-only
    python tools/pipeline_trace.py --log runtime.log --bus

Déterministe, zéro LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════
# 1. DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════

@dataclass
class LogEvent:
    timestamp: str
    level: str
    logger: str
    message: str
    raw: str
    line_no: int = 0

    @property
    def parsed_time(self) -> Optional[datetime]:
        try:
            return datetime.strptime(self.timestamp[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            return None


@dataclass
class BusMessage:
    timestamp: str
    msg_type: str
    sender: str
    destination: str
    details: str = ""


@dataclass
class WorkerInvocation:
    name: str
    start: str
    end: str = ""
    status: str = "running"
    task_id: str = ""
    error: str = ""


@dataclass
class PipelineRun:
    pipeline_id: str = "unknown"
    start: str = ""
    end: str = ""
    workers: List[WorkerInvocation] = field(default_factory=list)
    bus_messages: List[BusMessage] = field(default_factory=list)
    status: str = "unknown"
    task_id: str = ""


# ═══════════════════════════════════════════════════════════════
# 2. PARSERS
# ═══════════════════════════════════════════════════════════════

# Pattern: 2025-08-26 22:15:33,123 [INFO] LoggerName: message
LOG_PATTERN = re.compile(
    r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?)\s+"
    r"\[(\w+)\]\s+"
    r"([^:]+):\s+(.*)"
)

# Bus routing pattern
BUS_ROUTE_PATTERN = re.compile(
    r"Bus:\s+(Routing|No subscribers|Published)\s+"
    r"(.*?)(?:from\s+(\S+))?(?:to\s+(\S+))?"
)

# Worker start/end patterns
WORKER_START = re.compile(r"(?:Initializing|Starting|Running)\s+(\w+)(?:\s+worker)?", re.I)
WORKER_END = re.compile(r"(?:Completed|Finished|Done|Success)\s+(\w+)", re.I)
WORKER_ERROR = re.compile(r"(?:Error|Failed|Exception)\s+(?:in\s+)?(\w+)", re.I)

# Pipeline patterns
PIPELINE_START = re.compile(r"Running\s+(?:analysis\s+)?pipeline", re.I)
PIPELINE_END = re.compile(r"Pipeline\s+(?:completed|finished|done)", re.I)

# Task ID patterns
TASK_PATTERN = re.compile(r"task[_\s]?id[:\s=]*([\w-]+)", re.I)


def parse_log_file(path: str) -> List[LogEvent]:
    events = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f, 1):
            line = line.rstrip("\n\r")
            if not line:
                continue
            m = LOG_PATTERN.match(line)
            if m:
                events.append(LogEvent(
                    timestamp=m.group(1),
                    level=m.group(2),
                    logger=m.group(3).strip(),
                    message=m.group(4).strip(),
                    raw=line[:300],
                    line_no=i,
                ))
    return events


def extract_trace(events: List[LogEvent]) -> PipelineRun:
    trace = PipelineRun()
    workers_map: Dict[str, WorkerInvocation] = {}

    # Heuristic: pipeline name from first pipeline event
    for ev in events:
        if PIPELINE_START.search(ev.message):
            trace.pipeline_id = "analysis"
            trace.start = ev.timestamp
            # Try to extract custom pipeline id
            id_m = TASK_PATTERN.search(ev.message)
            if id_m:
                trace.task_id = id_m.group(1)
            break

    for ev in events:
        # Bus messages
        bus_m = BUS_ROUTE_PATTERN.search(ev.message)
        if bus_m:
            action = bus_m.group(1)
            rest = bus_m.group(2) if bus_m.group(2) else ""
            sender = bus_m.group(3) if bus_m.group(3) else ""
            dest = bus_m.group(4) if bus_m.group(4) else ""
            trace.bus_messages.append(BusMessage(
                timestamp=ev.timestamp,
                msg_type=action,
                sender=sender,
                destination=dest,
                details=rest[:200],
            ))

        # Worker start
        ws = WORKER_START.search(ev.message)
        if ws:
            wname = ws.group(1).lower()
            if wname not in workers_map:
                w = WorkerInvocation(name=wname, start=ev.timestamp)
                tid = TASK_PATTERN.search(ev.message)
                if tid:
                    w.task_id = tid.group(1)
                workers_map[wname] = w

        # Worker error
        we = WORKER_ERROR.search(ev.message)
        if we:
            wname = we.group(1).lower()
            if wname in workers_map:
                workers_map[wname].status = "error"
                workers_map[wname].error = ev.message[:150]

        # Pipeline end
        if PIPELINE_END.search(ev.message):
            trace.end = ev.timestamp
            trace.status = "completed" if "error" not in ev.message.lower() else "failed"

    # Assign remaining workers (no explicit end = running)
    trace.workers = list(workers_map.values())
    for w in trace.workers:
        if w.status == "running" and trace.end:
            w.end = trace.end
            w.status = "completed"

    return trace


# ═══════════════════════════════════════════════════════════════
# 3. OUTPUT
# ═══════════════════════════════════════════════════════════════

def format_timeline(trace: PipelineRun) -> str:
    lines = []
    lines.append(f"╔══════════════════════════════════════════╗")
    lines.append(f"║ PIPELINE: {trace.pipeline_id:30s} ║")
    lines.append(f"║ Status:   {trace.status:30s} ║")
    if trace.task_id:
        lines.append(f"║ Task:     {trace.task_id:30s} ║")
    lines.append(f"╚══════════════════════════════════════════╝")
    lines.append("")

    if trace.start:
        lines.append(f"🟢 DÉBUT  {trace.start}")
    if trace.end:
        elapsed = ""
        if trace.start and trace.end:
            try:
                st = datetime.strptime(trace.start[:19], "%Y-%m-%d %H:%M:%S")
                en = datetime.strptime(trace.end[:19], "%Y-%m-%d %H:%M:%S")
                delta = en - st
                elapsed = f" ({delta.total_seconds():.0f}s)"
            except ValueError:
                pass
        lines.append(f"🔚 FIN    {trace.end}{elapsed}")

    lines.append("")
    lines.append(f"📋 WORKERS ({len(trace.workers)}):")
    for w in trace.workers:
        status_icon = "✅" if w.status == "completed" else "❌" if w.status == "error" else "🔄"
        err = f" — {w.error}" if w.error else ""
        lines.append(f"  {status_icon} {w.name:20s} {w.start}{err}")

    if trace.bus_messages:
        lines.append(f"\n📨 BUS ({len(trace.bus_messages)} messages):")
        for m in trace.bus_messages[:20]:
            lines.append(f"  {m.timestamp[:19]} [{m.msg_type:12s}] {m.sender} → {m.destination}")
        if len(trace.bus_messages) > 20:
            lines.append(f"  ... et {len(trace.bus_messages) - 20} messages supplémentaires")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# 4. CLI
# ═══════════════════════════════════════════════════════════════

def register_cli(subparsers):
    """Register the trace command as a CLI subcommand."""
    p = subparsers.add_parser("trace", help="Analyse des logs pipeline (timeline, erreurs, bus)")
    p.add_argument("--log", "-l", required=True, help="Fichier de log (runtime.log)")
    p.add_argument("--output", "-o", help="Fichier JSON de sortie")
    p.add_argument("--timeline", "-t", action="store_true", help="Afficher la timeline textuelle")
    p.add_argument("--errors-only", "-e", action="store_true", help="Afficher seulement les erreurs")
    p.add_argument("--bus", "-b", action="store_true", help="Afficher les messages bus")
    p.set_defaults(func=cmd_trace)


def cmd_trace(args):
    if not Path(args.log).exists():
        print(f"Erreur: fichier {args.log} introuvable", file=sys.stderr)
        sys.exit(1)

    events = parse_log_file(args.log)
    print(f"📄 {args.log}: {len(events)} événements parsés", file=sys.stderr)

    if args.errors_only:
        errors = [e for e in events if e.level in ("ERROR", "CRITICAL", "WARNING")]
        if not errors:
            print("Aucune erreur trouvée.")
        for e in errors:
            print(f"[{e.timestamp}] {e.level} {e.logger}: {e.message}")
        return

    trace = extract_trace(events)

    if args.bus:
        for m in trace.bus_messages:
            print(f"{m.timestamp[:19]} [{m.msg_type:12s}] {m.sender:20s} → {m.destination}")
        return

    if args.timeline or not args.output:
        print(format_timeline(trace))

    if args.output:
        data = {
            "pipeline_id": trace.pipeline_id,
            "task_id": trace.task_id,
            "start": trace.start,
            "end": trace.end,
            "status": trace.status,
            "worker_count": len(trace.workers),
            "bus_message_count": len(trace.bus_messages),
            "workers": [
                {"name": w.name, "start": w.start, "end": w.end,
                 "status": w.status, "error": w.error}
                for w in trace.workers
            ],
            "bus_messages": [
                {"timestamp": m.timestamp, "type": m.msg_type,
                 "sender": m.sender, "destination": m.destination}
                for m in trace.bus_messages
            ],
        }
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✅ Trace écrite dans {args.output}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Trace parser pour logs CaptN")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()