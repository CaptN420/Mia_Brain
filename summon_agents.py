#!/usr/bin/env python3
"""
summon_agents.py — Instantiate and run CaptN-BRAIN agents/workers from the command line.

Usage:
    python summon_agents.py --help

All commands are auto-discovered from captn/cli/ (CaptN runtime) and tools/
(standalone tools). Each command's help is available via:
    python summon_agents.py <command> --help
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Add tools dir for standalone tools
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("summon_agents")

# Seed the Fragment Registry at startup
try:
    from captn.runtime.seed_registry import seed_all as _seed_all
    _seed_all()
except Exception:
    pass  # Non-critical; registry seed works on-demand too


def main() -> None:
    from captn.cli import auto_discover, register_tool_modules  # type: ignore[import-not-found]

    parser = argparse.ArgumentParser(
        description="Summon CaptN-BRAIN agents/workers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Phase 1: domains requiring CaptN runtime (thinker, mirror, pipeline, etc.)
    auto_discover(subparsers)

    # Phase 2: standalone tools (codechunk, imports, depgraph, eqsolve, etc.)
    register_tool_modules(subparsers)

    args = parser.parse_args()

    if hasattr(args, "func"):
        try:
            args.func(args)
        except KeyboardInterrupt:
            logger.info("Interrupted")
            sys.exit(130)
        except Exception as e:
            logger.exception("Command failed: %s", e)
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()