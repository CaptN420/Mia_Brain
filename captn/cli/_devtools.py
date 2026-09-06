#!/usr/bin/env python3
"""CLI command: devtools — consolidated developer tools (token-economy tools).

Exposes 5 AST-based developer tools under a single ``devtools`` subcommand
instead of polluting the top-level command namespace:

  - ``devtools codechunk``   — Découper un fichier en chunks fonction/classe
  - ``devtools imports``     — Analyse et optimisation des imports Python
  - ``devtools context``     — Extraire le contexte local d'un fichier Python
  - ``devtools lint``        — Linter Python structuré
  - ``devtools diff-ast``    — Diff structurel AST entre deux fichiers

Usage:
    python summon_agents.py devtools codechunk myfile.py
    python summon_agents.py devtools imports myfile.py --fix
    python summon_agents.py devtools context myfile.py --line 42
    python summon_agents.py devtools lint myfile.py --json
    python summon_agents.py devtools diff-ast old.py new.py
"""
from __future__ import annotations

import argparse
import logging

logger = logging.getLogger("cli._devtools")


def _cmd_devtools(args):
    """Called only when 'devtools' is used without a subcommand."""
    # argparse handles required subparser enforcement automatically
    pass


def register_cli(subparsers) -> None:
    """Register the 'devtools' umbrella + delegate to individual tool registrations."""
    p = subparsers.add_parser(
        "devtools",
        help="Developer tools (code analysis, linting, AST diff, etc.)",
        description="""Developer tools — AST-based code analysis, linting, import optimization,
context extraction, and structural diffs. All deterministic, no LLM.

Subcommands:
  codechunk        Découper un fichier en chunks fonction/classe
  imports          Analyse et optimisation des imports Python
  context          Extraire le contexte local d'un fichier Python
  lint             Linter Python structuré
  diff-ast         Diff structurel AST entre deux fichiers
  token_profile    CaptN-BRAIN token profiler (observation only)
  hermes_profile   Hermes production token profiler (pre_api_request hook)
  gap              Investigate token gap (64k heuristic vs 150k actual)
  reconcile        Reconcile local tokenizer vs OpenRouter actual usage
  budget           Context Budget — relevance-based selection within token limits
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dev_sub = p.add_subparsers(dest="devtool", required=True)

    # Lazy imports — each tool is loaded only when its subcommand is used
    try:
        from tools.codechunk import register_cli as _reg_codechunk
        _reg_codechunk(dev_sub)
        logger.debug("Loaded devtools subcommand: codechunk")
    except Exception as exc:
        logger.warning("devtools codechunk not available: %s", exc)

    try:
        from tools.imports_tool import register_cli as _reg_imports
        _reg_imports(dev_sub)
        logger.debug("Loaded devtools subcommand: imports")
    except Exception as exc:
        logger.warning("devtools imports not available: %s", exc)

    try:
        from tools.context_ai import register_cli as _reg_context
        _reg_context(dev_sub)
        logger.debug("Loaded devtools subcommand: context")
    except Exception as exc:
        logger.warning("devtools context not available: %s", exc)

    try:
        from tools.lint_tool import register_cli as _reg_lint
        _reg_lint(dev_sub)
        logger.debug("Loaded devtools subcommand: lint")
    except Exception as exc:
        logger.warning("devtools lint not available: %s", exc)

    try:
        from tools.diff_ast import register_cli as _reg_diffast
        _reg_diffast(dev_sub)
        logger.debug("Loaded devtools subcommand: diff-ast")
    except Exception as exc:
        logger.warning("devtools diff-ast not available: %s", exc)

    # Token Profiler — observation-only; captures per-component token attribution
    try:
        from captn.runtime.token_profiler import register_cli as _reg_tokprof
        _reg_tokprof(dev_sub)
        logger.debug("Loaded devtools subcommand: token_profile")
    except Exception as exc:
        logger.warning("devtools token_profile not available: %s", exc)

    # Hermes Production Profiler — hooks into pre_api_request lifecycle
    try:
        from captn.runtime.hermes_profiler import register_cli as _reg_hermesprof
        _reg_hermesprof(dev_sub)
        logger.debug("Loaded devtools subcommand: hermes_profile")
    except Exception as exc:
        logger.warning("devtools hermes_profile not available: %s", exc)

    # Gap Investigation — diagnostic for 64k vs 150k discrepancy
    try:
        from captn.runtime.token_gap import register_cli as _reg_gap
        _reg_gap(dev_sub)
        logger.debug("Loaded devtools subcommand: gap")
    except Exception as exc:
        logger.warning("devtools gap not available: %s", exc)

    # OpenRouter Token Reconciliation — reconcile local vs provider token counts
    try:
        from captn.runtime.reconcile import register_cli as _reg_reconcile
        _reg_reconcile(dev_sub)
        logger.debug("Loaded devtools subcommand: reconcile")
    except Exception as exc:
        logger.warning("devtools reconcile not available: %s", exc)

    # Context Budget — relevance-based selection within token limits
    try:
        from captn.runtime.context_budget import register_cli as _reg_budget
        _reg_budget(dev_sub)
        logger.debug("Loaded devtools subcommand: budget")
    except Exception as exc:
        logger.warning("devtools budget not available: %s", exc)

    # Adaptive Context Manager — dynamic budget allocation
    try:
        from captn.runtime.adaptive_manager import register_cli as _reg_adaptive
        _reg_adaptive(dev_sub)
        logger.debug("Loaded devtools subcommand: adaptive")
    except Exception as exc:
        logger.warning("devtools adaptive not available: %s", exc)

    p.set_defaults(func=_cmd_devtools)