#!/usr/bin/env python3
"""
captn/cli/ — Domain-cli modules for summon_agents.py.

Each module exposes ``register_cli(subparsers)`` which adds its subparsers
and sets ``defaults(func=<handler>)`` on the last subparser.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse

logger = logging.getLogger("captn.cli")


def auto_discover(subparsers: argparse._SubParsersAction) -> None:
    """Load every *registered* cli module from this package."""
    for _, mod_name, _ in pkgutil.iter_modules(__path__):  # type: ignore[arg-type]
        full_name = f"{__package__}.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
            if hasattr(mod, "register_cli"):
                mod.register_cli(subparsers)
                logger.debug("Loaded CLI module: %s", full_name)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", full_name, exc)


def register_tool_modules(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Discover and register standalone tools that expose ``register_cli``."""
    from pathlib import Path
    import sys

    root = Path(__file__).resolve().parent.parent.parent  # → CaptN-BRAIN-main
    tools_dir = root / "tools"
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))

    for py_file in sorted(tools_dir.glob("*.py")):
        mod_name = py_file.stem
        if mod_name.startswith("_") or mod_name in (
            "alchimie_library_manager",
            "alchimie_dashboard",
            "alchimie_dashboard_cli",
            "crawler_cli",
            "raw2json_cli",
            "scanner",
            "scan_secrets",
            "update_alchimie",
            "math_validator",
            "equation_tools",
            "equation_workbench",
            # DevTools — accessible via `devtools <subcommand>` instead
            "codechunk",
            "imports_tool",
            "context_ai",
            "lint_tool",
            "diff_ast",
        ):
            continue
        try:
            mod = importlib.import_module(f"tools.{mod_name}")
            if hasattr(mod, "register_cli"):
                mod.register_cli(subparsers)
                logger.debug("Registered tool: %s", mod_name)
        except Exception as exc:
            logger.debug("Skipping %s: %s", mod_name, exc)