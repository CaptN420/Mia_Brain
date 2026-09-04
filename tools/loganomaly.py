#!/usr/bin/env python3
"""
loganomaly.py — Détection d'anomalies dans les logs CaptN.

Au-delà de pipeline_trace (timeline) : statistiques temporelles,
outliers, motifs d'erreur récurrents, workers lents, tendances.

Usage:
    python -m tools.loganomaly runtime.log
    python -m tools.loganomaly runtime.log --outliers
    python -m tools.loganomaly runtime.log --errors
    python -m tools.loganomaly runtime.log --trends
    python -m tools.loganomaly runtime.log --json
"""

import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ─── Log parsers ───

# Typical format: 2025-08-24 18:04:00,123 [INFO] module: message
_LOG_LINE = re.compile(
    r'(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?)\s+'
    r'\[(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\s+'
    r'(?P<logger>[^:]+):\s*(?P<message>.*)'
)

# Alternative: just [LEVEL] message
_LOG_LINE_ALT = re.compile(
    r'\[(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL)\]\s+'
    r'(?P<message>.*)'
)


@dataclass
class LogEvent:
    timestamp: Optional[datetime]
    level: str
    logger: str
    message: str
    raw_line: str
    line_number: int


@dataclass
class Anomaly:
    kind: str
    severity: str  # info, warning, critical
    message: str
    count: int = 1
    details: dict = field(default_factory=dict)


@dataclass
class AnomalyReport:
    file: str
    total_lines: int = 0
    parsed_events: int = 0
    anomaly_count: int = 0
    anomalies: List[Anomaly] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "total_lines": self.total_lines,
            "parsed_events": self.parsed_events,
            "anomaly_count": self.anomaly_count,
            "anomalies": [
                {"kind": a.kind, "severity": a.severity,
                 "message": a.message, "count": a.count,
                 "details": a.details}
                for a in self.anomalies
            ],
            "stats": self.stats,
        }


class LogAnomalyDetector:

    def __init__(self, log_path: str):
        self.path = Path(log_path)
        self.events: List[LogEvent] = []

    def parse(self) -> None:
        """Parse le fichier de log."""
        text = self.path.read_text(encoding="utf-8", errors="replace")
        lines = text.split("\n")
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue

            match = _LOG_LINE.match(line)
            if match:
                ts_str = match.group("timestamp")
                ts = self._parse_timestamp(ts_str)
                event = LogEvent(
                    timestamp=ts,
                    level=match.group("level"),
                    logger=match.group("logger"),
                    message=match.group("message"),
                    raw_line=line,
                    line_number=i,
                )
                self.events.append(event)
            else:
                # Try alternative format
                alt = _LOG_LINE_ALT.match(line)
                if alt:
                    event = LogEvent(
                        timestamp=None,
                        level=alt.group("level"),
                        logger="?",
                        message=alt.group("message"),
                        raw_line=line,
                        line_number=i,
                    )
                    self.events.append(event)

    def _parse_timestamp(self, ts: str) -> Optional[datetime]:
        """Parse différents formats de timestamp."""
        ts = ts.replace(",", ".")
        formats = [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue
        return None

    def detect(self) -> AnomalyReport:
        report = AnomalyReport(file=str(self.path))
        report.total_lines = sum(1 for _ in self.path.read_text().split("\n"))
        report.parsed_events = len(self.events)

        if not self.events:
            return report

        anomalies: List[Anomaly] = []

        # 1. Spike d'erreurs
        self._detect_error_spikes(anomalies)

        # 2. Workers lents (durées entre timestamps)
        self._detect_slow_workers(anomalies)

        # 3. Patterns d'erreur récurrents
        self._detect_error_patterns(anomalies)

        # 4. Lacunes temporelles (trous dans les logs)
        self._detect_gaps(anomalies)

        # 5. Ratio erreur / info
        self._detect_error_ratio(anomalies)

        # 6. Messages répétés excessivement
        self._detect_repeated_messages(anomalies)

        report.anomalies = anomalies
        report.anomaly_count = len(anomalies)

        # Stats de base
        levels = Counter(e.level for e in self.events)
        loggers = Counter(e.logger for e in self.events)
        top_errors = Counter(
            e.message for e in self.events if e.level in ("ERROR", "CRITICAL")
        ).most_common(10)

        report.stats = {
            "levels": dict(levels),
            "loggers": dict(loggers.most_common(10)),
            "top_errors": [{"msg": m, "count": c} for m, c in top_errors],
            "total_duration_sec": self._duration(),
        }

        return report

    def _duration(self) -> Optional[float]:
        """Durée totale entre premier et dernier événement."""
        timestamps = [e.timestamp for e in self.events if e.timestamp]
        if len(timestamps) >= 2:
            delta = max(timestamps) - min(timestamps)
            return delta.total_seconds()
        return None

    def _detect_error_spikes(self, anomalies: List[Anomaly]) -> None:
        """Détecte les pics d'erreurs dans le temps."""
        error_events = [
            e for e in self.events
            if e.timestamp and e.level in ("ERROR", "CRITICAL")
        ]
        if len(error_events) < 10:
            return

        # Grouper par minute
        by_minute: Dict[str, int] = defaultdict(int)
        for e in error_events:
            key = e.timestamp.strftime("%Y-%m-%d %H:%M")
            by_minute[key] += 1

        avg = sum(by_minute.values()) / max(len(by_minute), 1)
        std = (sum((c - avg) ** 2 for c in by_minute.values()) / max(len(by_minute), 1)) ** 0.5

        for minute, count in sorted(by_minute.items()):
            if count > avg + 3 * std and count >= 3:
                anomalies.append(Anomaly(
                    kind="error_spike",
                    severity="critical",
                    message=f"Pic d'erreurs à {minute}: {count} erreurs "
                            f"(moyenne={avg:.1f}, écart-type={std:.1f})",
                    count=count,
                    details={"minute": minute, "count": count, "avg": round(avg, 1)},
                ))

    def _detect_slow_workers(self, anomalies: List[Anomaly]) -> None:
        """Détecte les workers anormalement longs."""
        if len(self.events) < 5:
            return

        # Grouper par logger pour estimer les durées
        by_logger: Dict[str, List[datetime]] = defaultdict(list)
        for e in self.events:
            if e.timestamp:
                by_logger[e.logger].append(e.timestamp)

        for logger, timestamps in by_logger.items():
            if len(timestamps) < 5:
                continue
            # Inter-arrival times
            gaps = [(timestamps[i+1] - timestamps[i]).total_seconds()
                    for i in range(len(timestamps) - 1)]
            if not gaps:
                continue
            avg_gap = sum(gaps) / len(gaps)
            max_gap = max(gaps)

            if max_gap > avg_gap * 5 and max_gap > 5:
                anomalies.append(Anomaly(
                    kind="slow_worker",
                    severity="warning",
                    message=f"Worker '{logger}' anormalement lent : "
                            f"pic à {max_gap:.1f}s (moyenne={avg_gap:.1f}s)",
                    count=1,
                    details={
                        "logger": logger,
                        "max_gap_sec": round(max_gap, 1),
                        "avg_gap_sec": round(avg_gap, 1),
                    },
                ))

    def _detect_error_patterns(self, anomalies: List[Anomaly]) -> None:
        """Détecte les motifs d'erreur récurrents (mêmes messages)."""
        errors_by_msg = Counter(
            e.message for e in self.events if e.level in ("ERROR", "CRITICAL")
        )
        for msg, count in errors_by_msg.most_common(5):
            if count >= 3:
                anomalies.append(Anomaly(
                    kind="recurring_error",
                    severity="warning" if count < 10 else "critical",
                    message=f"Erreur récurrente ({count}x) : {msg[:100]}",
                    count=count,
                    details={"message": msg[:200], "count": count},
                ))

    def _detect_gaps(self, anomalies: List[Anomaly]) -> None:
        """Détecte les trous dans les logs (périodes sans événement)."""
        timestamps = sorted([
            e.timestamp for e in self.events
            if e.timestamp
        ])
        if len(timestamps) < 10:
            return

        gaps: List[Tuple[datetime, datetime, float]] = []
        for i in range(len(timestamps) - 1):
            gap = (timestamps[i+1] - timestamps[i]).total_seconds()
            if gap > 60:  # > 1 minute
                gaps.append((timestamps[i], timestamps[i+1], gap))

        if gaps:
            # Top 3 plus longs gaps
            gaps.sort(key=lambda x: -x[2])
            for start, end, duration in gaps[:3]:
                if duration > 300:  # > 5 minutes
                    anomalies.append(Anomaly(
                        kind="log_gap",
                        severity="info",
                        message=f"Trou dans les logs : "
                                f"{duration/60:.1f} min entre "
                                f"{start.strftime('%H:%M:%S')} et "
                                f"{end.strftime('%H:%M:%S')}",
                        count=1,
                        details={
                            "from": start.isoformat(),
                            "to": end.isoformat(),
                            "duration_sec": duration,
                        },
                    ))

    def _detect_error_ratio(self, anomalies: List[Anomaly]) -> None:
        """Détecte un ratio anormal d'erreurs."""
        total = len(self.events)
        errors = sum(1 for e in self.events if e.level in ("ERROR", "CRITICAL"))
        if total > 0 and errors / total > 0.2:
            anomalies.append(Anomaly(
                kind="high_error_ratio",
                severity="critical",
                message=f"Ratio d'erreurs élevé : {errors}/{total} "
                        f"({errors/total*100:.0f}%)",
                count=errors,
                details={"errors": errors, "total": total, "ratio": round(errors/total, 3)},
            ))

    def _detect_repeated_messages(self, anomalies: List[Anomaly]) -> None:
        """Messages identiques répétés en boucle."""
        msg_counts = Counter(e.message for e in self.events)
        for msg, count in msg_counts.most_common(5):
            if count > 10 and len(self.events) > 100:
                anomalies.append(Anomaly(
                    kind="repeated_message",
                    severity="info",
                    message=f"Message répété {count}x : {msg[:80]}",
                    count=count,
                    details={"message": msg[:200], "count": count},
                ))


def format_report(report: AnomalyReport, show_all: bool = False) -> str:
    """Formatte le rapport en texte lisible."""
    lines = []
    lines.append(f"\n{'='*50}")
    lines.append(f"  📊 LOGANOMALY — {report.file}")
    lines.append(f"{'='*50}")
    lines.append(f"  Lignes: {report.total_lines}  "
                 f"Événements parsés: {report.parsed_events}")
    lines.append(f"  Anomalies: {report.anomaly_count}")
    s = report.stats
    lines.append(f"\n  📈 Stats:")
    for level, count in sorted(s.get("levels", {}).items()):
        bar = "█" * min(count // 10 + 1, 30)
        lines.append(f"    {level:10s} : {count:5d} {bar}")
    dur = s.get("total_duration_sec")
    if dur:
        lines.append(f"    Durée      : {dur:.1f}s")

    if s.get("top_errors"):
        lines.append(f"\n  ❌ Top erreurs:")
        for e in s["top_errors"][:5]:
            lines.append(f"    {e['count']:4d}x — {e['msg'][:80]}")

    if report.anomalies:
        lines.append(f"\n  ⚠  Anomalies détectées:")
        for a in sorted(report.anomalies,
                        key=lambda x: {"critical": 0, "warning": 1, "info": 2}[x.severity]):
            icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}[a.severity]
            lines.append(f"    {icon} [{a.severity:8s}] {a.kind:20s} "
                         f"(x{a.count})")
            lines.append(f"         {a.message[:120]}")

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Détection d'anomalies dans les logs"
    )
    parser.add_argument("log", help="Fichier de log à analyser")
    parser.add_argument("--outliers", "-o", action="store_true",
                        help="Afficher seulement les outliers")
    parser.add_argument("--errors", "-e", action="store_true",
                        help="Afficher seulement les erreurs")
    parser.add_argument("--trends", "-t", action="store_true",
                        help="Afficher les tendances")
    parser.add_argument("--json", "-j", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--threshold", type=float, default=2.0,
                        help="Seuil d'écart-type pour outliers")
    args = parser.parse_args()

    if not os.path.isfile(args.log):
        print(f"Erreur: fichier {args.log} introuvable", file=sys.stderr)
        sys.exit(1)

    detector = LogAnomalyDetector(args.log)
    detector.parse()
    report = detector.detect()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    elif args.errors:
        for e in detector.events:
            if e.level in ("ERROR", "CRITICAL"):
                ts = e.timestamp.strftime("%H:%M:%S") if e.timestamp else "??"
                print(f"[{ts}] {e.logger}: {e.message}")
    elif args.trends:
        if report.stats.get("total_duration_sec"):
            dur = report.stats["total_duration_sec"]
            levels = report.stats.get("levels", {})
            total_events = sum(levels.values())
            rate = total_events / max(dur / 60, 0.001)
            error_rate = sum(levels.get(l, 0) for l in ("ERROR", "CRITICAL")) / max(dur / 60, 0.001)
            print(f"📈 TENDANCES — {report.file}")
            print(f"  Événements/min : {rate:.1f}")
            print(f"  Erreurs/min     : {error_rate:.1f}")
            print(f"  Durée           : {dur/60:.1f} min")
            print(f"  Total           : {total_events} événements")
    elif args.outliers:
        for a in report.anomalies:
            if a.severity in ("critical", "warning"):
                print(f"  [{a.severity}] {a.kind:20s} x{a.count} — {a.message[:100]}")
    else:
        print(format_report(report))


def register_cli(subparsers):
    """Register loganomaly as a CLI subcommand."""
    p = subparsers.add_parser("loganomaly", help="Détection d'anomalies dans les logs")
    p.add_argument("log", help="Fichier de log")
    p.add_argument("--outliers", "-o", action="store_true")
    p.add_argument("--errors", "-e", action="store_true")
    p.add_argument("--trends", "-t", action="store_true")
    p.add_argument("--json", "-j", action="store_true")
    p.add_argument("--threshold", type=float, default=2.0, help="Seuil d'écart-type")
    p.set_defaults(func=main)


if __name__ == "__main__":
    main()