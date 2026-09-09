#!/usr/bin/env python3
"""
summon_agents.py — Instantiate and run CaptN-BRAIN agents/workers from the command line.

Usage:
    python summon_agents.py --help

All commands are auto-discovered from captn/cli/ (CaptN runtime) and tools/
(standalone tools). Each command's help is available via:
    python summon_agents.py <command> --help

Global optimization flags:
    --cache-size N       Tool result cache size (default 1024, 0 = disabled)
    --no-adaptive-routing  Disable adaptive routing (use fixed top_k=2)
    --llm-fallback       Enable LLM fallback recovery (default: off / opt-in)
"""
from __future__ import annotations

import argparse
import logging
import os
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


def apply_optimization_flags(args: argparse.Namespace) -> None:
    """Apply global optimization flags to the runtime environment.

    These flags control:
      - Tool cache size (LRU)
      - Adaptive routing enable/disable
      - LLM fallback enable/disable

    CaptN context optimization is enabled by default for maximum token economy.
    """
    # Cache size
    cache_size = getattr(args, "cache_size", None)
    if cache_size is not None and cache_size >= 0:
        os.environ["CAPTN_CACHE_SIZE"] = str(cache_size)

    # Adaptive routing
    if getattr(args, "no_adaptive_routing", False):
        os.environ["CAPTN_ADAPTIVE_ROUTING"] = "0"
    else:
        os.environ.setdefault("CAPTN_ADAPTIVE_ROUTING", "1")

    # ── CaptN context optimization (ON by default) ──────────────────
    # Context Budget: relevance-based selection within token limits
    # - System prompt capped at 5K tokens (core never removed)
    # - History capped at 8K tokens (recent + compressed older)
    # - Memory capped at 7K tokens (relevance-scored)
    # - Tools capped at 5K tokens (utility/token-cost selection)
    # - Retrieved context capped at 5K tokens
    # Total soft budget: 30K tokens
    os.environ.setdefault("CAPTN_CONTEXT_BUDGET", "on")

    # Context Necessity: per-category necessity analysis (T0-T3 tool segmentation)
    #   conservative = drop only T3 (irrelevant) tools
    #   ab_test     = drop T2+T3 tools
    # Conservative is safe by default — no critical quality regressions confirmed
    os.environ.setdefault("CAPTN_CONTEXT_NECESSITY", "conservative")

    # ── End CaptN context optimization ──────────────────────────────

    # LLM fallback
    if getattr(args, "llm_fallback", False):
        os.environ["CAPTN_LLM_FALLBACK"] = "true"

    # Token profiling
    token_profiling = getattr(args, "token_profiling", None)
    if token_profiling is not None:
        os.environ["CAPTN_TOKEN_PROFILING"] = token_profiling

    # Shadow A/B mode
    shadow_ab = getattr(args, "shadow_ab", None)
    if shadow_ab is not None:
        os.environ["CAPTN_ADAPTIVE_AB"] = shadow_ab


def main() -> None:
    from captn.cli import auto_discover, register_tool_modules  # type: ignore[import-not-found]

    parser = argparse.ArgumentParser(
        description="Summon CaptN-BRAIN agents/workers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Global optimization flags (before subcommand) ──────────────────
    parser.add_argument(
        "--cache-size",
        type=int,
        default=None,
        help="Tool result cache size (default 1024, 0 = disable)",
    )
    parser.add_argument(
        "--no-adaptive-routing",
        action="store_true",
        help="Disable adaptive routing (use fixed top_k=2)",
    )
    parser.add_argument(
        "--llm-fallback",
        action="store_true",
        help="Enable LLM fallback recovery (default: off)",
    )
    parser.add_argument(
        "--token-profiling",
        type=str,
        default=None,
        choices=["off", "summary", "verbose"],
        help="Enable token profiling (off/summary/verbose, default: off)",
    )
    parser.add_argument(
        "--shadow-ab",
        type=str,
        default=None,
        choices=["off", "shadow"],
        help="Shadow A/B mode for adaptive weight validation (default: off)",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Phase 1: domains requiring CaptN runtime (thinker, mirror, pipeline, etc.)
    auto_discover(subparsers)

    # Phase 2: standalone tools (codechunk, imports, depgraph, eqsolve, etc.)
    register_tool_modules(subparsers)

    args = parser.parse_args()

    # Apply optimization flags before executing command
    apply_optimization_flags(args)

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