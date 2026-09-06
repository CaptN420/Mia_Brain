#!/usr/bin/env python3
"""
_cli_tools.py — Unified CLI for all CaptN-BRAIN tools.

Provides:
    summon_agents.py run <tool_name> key=value ...
    summon_agents.py tools list
    summon_agents.py tools count

Auto-routes to the correct module via _tool_router.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List

from captn.cli._tool_router import (
    run_tool,
    list_all_tools,
    list_tools_by_domain,
    print_tool_summary,
)


def _parse_kwargs(args_list: List[str]) -> Dict[str, Any]:
    kwargs = {}
    for kv in args_list:
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                kwargs[k] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                kwargs[k] = v
    return kwargs


def _cmd_run(args) -> None:
    """Run a tool by name."""
    kwargs = _parse_kwargs(args.args)
    result = run_tool(args.tool_name, **kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result.get("valid", False):
        sys.exit(1)


def _cmd_list(args) -> None:
    """List all tools."""
    by_domain = list_tools_by_domain()
    total = sum(len(tools) for tools in by_domain.values())
    print(f"CaptN-BRAIN Tools — {total} tools across {len(by_domain)} domains")
    print()
    for domain, tools in sorted(by_domain.items()):
        print(f"  [{domain}] — {len(tools)} tools:")
        for t in tools:
            print(f"    {t['full_name']:35s}  {t['description']}")
        print()


def _cmd_count(args) -> None:
    """Count tools by domain."""
    by_domain = list_tools_by_domain()
    total = sum(len(tools) for tools in by_domain.values())
    for domain, tools in sorted(by_domain.items()):
        print(f"  {domain:15s}: {len(tools):>3d} tools")
    print(f"  {'TOTAL':15s}: {total:>3d} tools")


def _cmd_search(args) -> None:
    """Search tools by keyword."""
    query = args.query.lower()
    by_domain = list_tools_by_domain()
    found = []
    for domain, tools in by_domain.items():
        for t in tools:
            if query in t["name"].lower() or query in t["description"].lower():
                found.append((domain, t))
    if found:
        print(f"Found {len(found)} tools matching '{args.query}':")
        for domain, t in found:
            print(f"  [{domain}] {t['full_name']:35s}  {t['description']}")
    else:
        print(f"No tools matching '{args.query}'")


def register_cli(subparsers) -> None:
    """Register the unified tools CLI."""
    # 'run' command at top level
    run_p = subparsers.add_parser(
        "run",
        help="Run any tool by name (e.g. 'run math_gcd a=12 b=8')",
    )
    run_p.add_argument("tool_name", help="Tool name (e.g. 'math_gcd', 'gcd', 'ohms_law')")
    run_p.add_argument("args", nargs="*", help="Arguments as key=value pairs")
    run_p.set_defaults(func=_cmd_run)

    # 'tools' command group
    tools_p = subparsers.add_parser(
        "tools",
        help="List, search, and count available tools",
    )
    tools_sub = tools_p.add_subparsers(dest="tools_cmd")

    list_p = tools_sub.add_parser("list", help="List all tools by domain")
    list_p.set_defaults(func=_cmd_list)

    count_p = tools_sub.add_parser("count", help="Count tools by domain")
    count_p.set_defaults(func=_cmd_count)

    search_p = tools_sub.add_parser("search", help="Search tools by keyword")
    search_p.add_argument("query", help="Search keyword")
    search_p.set_defaults(func=_cmd_search)