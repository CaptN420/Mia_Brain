#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import subprocess
from datetime import datetime
from pathlib import Path


OUTPUT_FILE = Path("system_scan_report.txt")


def run_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            return result.stdout.strip()
        if result.stderr.strip():
            return f"[stderr]\n{result.stderr.strip()}"
        return "[no output]"
    except FileNotFoundError:
        return f"[error] Command not found: {' '.join(command)}"
    except Exception as exc:
        return f"[error] {exc}"


def section(title: str, content: str) -> str:
    line = "=" * 70
    return f"{line}\n{title}\n{line}\n{content}\n\n"


def main() -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    report_parts = []
    report_parts.append(section("SYSTEM SCAN REPORT", f"Generated at: {now}"))

    ports_output = run_command(["ss", "-ltnp"])
    report_parts.append(section("LISTENING TCP PORTS", ports_output))

    udp_output = run_command(["ss", "-lunp"])
    report_parts.append(section("LISTENING UDP PORTS", udp_output))

    process_output = run_command(["ps", "aux", "--sort=-%mem"])
    process_lines = process_output.splitlines()
    trimmed_process_output = "\n".join(process_lines[:25]) if process_lines else "[no output]"
    report_parts.append(section("TOP PROCESSES BY MEMORY", trimmed_process_output))

    auth_log_path = Path("/var/log/auth.log")
    if auth_log_path.exists():
        auth_tail = run_command(["tail", "-n", "40", str(auth_log_path)])
        report_parts.append(section("AUTH LOG TAIL", auth_tail))
    else:
        report_parts.append(section("AUTH LOG TAIL", "[info] /var/log/auth.log not found on this system."))

    failed_units = run_command(["systemctl", "--failed", "--no-pager"])
    report_parts.append(section("FAILED SYSTEMD UNITS", failed_units))

    full_report = "".join(report_parts)
    OUTPUT_FILE.write_text(full_report, encoding="utf-8")

    print("Scan complete.")
    print(f"Report saved to: {OUTPUT_FILE.resolve()}")
    print()
    print("Quick summary:")
    print("- Checked listening TCP ports")
    print("- Checked listening UDP ports")
    print("- Captured top processes")
    print("- Read auth log tail if available")
    print("- Checked failed systemd units")


if __name__ == "__main__":
    main()
