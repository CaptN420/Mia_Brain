#!/usr/bin/env python3
"""CLI commands: equation (20+ tools), workbench (25+ tools)."""
from __future__ import annotations

import json
import sys
from typing import Any


# ── Equation tools ──────────────────────────────────────────────────
def _cmd_equation(args):
    try:
        from equation_tools import run_tool, list_tools  # type: ignore[import-not-found]
    except ImportError:
        from equation_tools import run_tool, list_tools  # type: ignore[import-not-found]

    if args.action == "list":
        domain = args.domain
        tools = list_tools(domain=domain)
        print(f"📐 OUTILS DISPONIBLES ({len(tools)} total)")
        if domain:
            print(f"   Filtre: domaine={domain}")
        print()
        for t in tools:
            print(f"  [{t['domain']:10s}] {t['name']:30s} — {t['description']}")
        return

    if args.action == "run":
        if not args.tool_name:
            print("Usage: summon_agents.py equation run <tool_name> --kwargs '{\"key\": \"val\"}'")
            sys.exit(1)

        kwargs: dict[str, Any] = {}
        if args.kwargs:
            try:
                kwargs = json.loads(args.kwargs)
            except json.JSONDecodeError as e:
                print(f"❌ Erreur de parsing JSON: {e}")
                sys.exit(1)

        result = run_tool(args.tool_name, **kwargs)
        print(json.dumps(result, indent=2, ensure_ascii=False))


# ── Workbench tools ─────────────────────────────────────────────────
def _cmd_workbench(args):
    try:
        from equation_workbench import run_workbench_tool, list_workbench_tools  # type: ignore[import-not-found]
    except ImportError:
        from equation_workbench import run_workbench_tool, list_workbench_tools  # type: ignore[import-not-found]

    if args.action == "list":
        tools = list_workbench_tools()
        print(f"🔧 WORKBENCH — {len(tools)} outils disponibles\n")
        for t in tools:
            print(f"  {t['name']:20s} — {t['description']}")
        return

    if args.action == "run":
        if not args.tool_name:
            print("Usage: summon_agents.py workbench run <tool_name> --kwargs '{\"key\": \"val\"}'")
            sys.exit(1)

        kwargs: dict[str, Any] = {}
        if args.kwargs:
            try:
                kwargs = json.loads(args.kwargs)
            except json.JSONDecodeError as e:
                print(f"❌ Erreur de parsing JSON: {e}")
                sys.exit(1)

        result = run_workbench_tool(args.tool_name, **kwargs)
        print(json.dumps(result, indent=2, ensure_ascii=False))


# ── Registration ───────────────────────────────────────────────────
def register_cli(subparsers) -> None:
    # ── equation ──
    p_eq = subparsers.add_parser("equation", help="Run equation tools (math, physics, chemistry)")
    p_eq_sub = p_eq.add_subparsers(dest="action", required=True)

    p_list = p_eq_sub.add_parser("list", help="List available tools")
    p_list.add_argument("--domain", "-d", choices=["math", "physics", "chemistry", "general"],
                        help="Filter by domain")
    p_list.set_defaults(func=_cmd_equation)

    p_run = p_eq_sub.add_parser("run", help="Run a specific tool")
    p_run.add_argument("tool_name", help="Tool name (e.g. solve_quadratic, molecular_weight)")
    p_run.add_argument("--kwargs", "-k", help='JSON arguments: {"a":1,"b":-3,"c":2}')
    p_run.set_defaults(func=_cmd_equation)

    # ── workbench ──
    p_wb = subparsers.add_parser("workbench", help="Equation workbench (validate, score, compare, simulate)")
    p_wb_sub = p_wb.add_subparsers(dest="action", required=True)

    p_wb_list = p_wb_sub.add_parser("list", help="List available workbench tools")
    p_wb_list.set_defaults(func=_cmd_workbench)

    p_wb_run = p_wb_sub.add_parser("run", help="Run a workbench tool")
    p_wb_run.add_argument("tool_name", help="Tool name (e.g. validate, score, compare)")
    p_wb_run.add_argument("--kwargs", "-k", help='JSON arguments: {"equation":"Ndot=k*A*B"}')
    p_wb_run.set_defaults(func=_cmd_workbench)