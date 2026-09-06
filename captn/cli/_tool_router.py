#!/usr/bin/env python3
"""
_tool_router.py — Unified tool router for CaptN-BRAIN.

Provides a single entry point to discover and run any tool across all modules.
Auto-routes tool names to their correct module.

Usage:
    from captn.cli._tool_router import run_tool, list_tools, list_all_tools

    result = run_tool("math_gcd", a=12, b=8)
    result = run_tool("physics_ohms_law", voltage=12, resistance=6)
    tools = list_all_tools()  # all registered tools
"""
from __future__ import annotations

import importlib
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("tool_router")

# Module → tool dict name mapping
TOOL_MODULES: Dict[str, str] = {
    "math_tools": "MATH_TOOLS",
    "physics_tools": "PHYSICS_TOOLS",
    "chemistry_tools": "CHEMISTRY_TOOLS",
    "biology_tools": "BIO_TOOLS",
    "finance_tools": "FINANCE_TOOLS",
    "datasci_tools": "DATASCI_TOOLS",
    "engineering_tools": "ENG_TOOLS",
    "coding_tools": "CODING_TOOLS",
    "utility_tools": "UTILITY_TOOLS",
    "logic_tools": "TOOL_REGISTRY",
    "equation_tools": "TOOL_REGISTRY",
    "eqsolve": None,  # commands via cmd_*, not TOOLS dict
    "chemsym": None,
    "scratch_algebra": "SCRATCH_TOOLS",  # 6 algebra tools
    "code_review_tools": "CODE_REVIEW_TOOLS",  # 4 code review tools
    "mass": "TOOLS",  # mass-generated tools
}

# Cache for loaded modules
_loaded: Dict[str, Any] = {}

# ── Tool result cache (LRU, deterministic tools → infinite TTL) ─────
from collections import OrderedDict
_TOOL_CACHE: OrderedDict[str, Dict[str, Any]] = OrderedDict()
_TOOL_CACHE_MAX = 1024


def _get_cache_max() -> int:
    """Get max cache size from env var, fallback to default."""
    val = os.environ.get("CAPTN_CACHE_SIZE", "")
    if val.isdigit():
        return int(val)
    return _TOOL_CACHE_MAX


def _cache_key(name: str, kwargs: Dict[str, Any]) -> str:
    """Deterministic cache key: tool_name + sorted kwargs."""
    items = sorted(kwargs.items(), key=lambda x: x[0])
    return f"{name}|{'&'.join(f'{k}={v}' for k, v in items)}"


def _cache_get(key: str) -> Optional[Dict[str, Any]]:
    """Get from LRU cache; move to end on hit."""
    if key not in _TOOL_CACHE:
        return None
    _TOOL_CACHE.move_to_end(key)
    return _TOOL_CACHE[key]


def _cache_set(key: str, value: Dict[str, Any]) -> None:
    """Set in LRU cache; evict oldest if at capacity."""
    max_size = _get_cache_max()
    if max_size == 0:
        return  # cache disabled
    _TOOL_CACHE[key] = value
    while len(_TOOL_CACHE) > max_size:
        _TOOL_CACHE.popitem(last=False)


def _load_module(mod_name: str) -> Any:
    """Lazy-load a tool module."""
    if mod_name not in _loaded:
        _loaded[mod_name] = importlib.import_module(f"tools.{mod_name}")
    return _loaded[mod_name]


def _get_tool_dict(mod_name: str) -> Dict[str, Any]:
    """Get the TOOLS dict from a module."""
    mod = _load_module(mod_name)
    dict_name = TOOL_MODULES[mod_name]
    if dict_name is None:
        return {}
    return getattr(mod, dict_name, {})


def _find_tool(tool_name: str) -> Tuple[Optional[str], Optional[str], Optional[Any]]:
    """Find a tool by name across all modules. Returns (mod_name, tool_key, function)."""
    # Friendly prefix aliases: algebra_solve → scratch_algebra
    _PREFIX_ALIASES = {"algebra": "scratch_algebra", "review": "code_review_tools", "cr": "code_review_tools"}
    # Try prefixed name first (e.g. math_gcd)
    if "_" in tool_name:
        prefix, rest = tool_name.split("_", 1)
        # Check alias first
        if prefix in _PREFIX_ALIASES:
            mod_name = _PREFIX_ALIASES[prefix]
            tools_dict = _get_tool_dict(mod_name)
            if rest in tools_dict:
                return mod_name, rest, tools_dict[rest]["fn"]
        for mod_name in TOOL_MODULES:
            if mod_name.startswith(prefix):
                tools_dict = _get_tool_dict(mod_name)
                if rest in tools_dict:
                    return mod_name, rest, tools_dict[rest]["fn"]

    # Try all modules
    for mod_name in TOOL_MODULES:
        tools_dict = _get_tool_dict(mod_name)
        if tool_name in tools_dict:
            return mod_name, tool_name, tools_dict[tool_name]["fn"]
        # Also check prefixed keys in the module's dict
        for key in tools_dict:
            if key == tool_name or key.endswith(f"_{tool_name}"):
                return mod_name, key, tools_dict[key]["fn"]

    return None, None, None


def run_tool(name: str, **kwargs) -> Dict[str, Any]:
    """Run any tool by name with keyword arguments.

    Auto-routes to the correct module. Supports both prefixed
    (math_gcd, physics_ohms_law) and bare (gcd, ohms_law) names.

    OPTIMIZATION: Results from deterministic tools are cached (LRU, 1024 entries).
    Identical calls return cached result instantly — zero recomputation.

    Args:
        name: Tool name (e.g. 'gcd', 'math_gcd', 'ohms_law')
        **kwargs: Arguments to pass to the tool function

    Returns:
        Dict with tool result (always has 'valid' key)
    """
    # Check cache first (deterministic tools → same input = same output)
    cache_key = _cache_key(name, kwargs)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Cache HIT for tool: %s", cache_key[:80])
        return cached

    mod_name, tool_key, fn = _find_tool(name)
    if fn is not None:
        try:
            result = fn(**kwargs)
            result["_cached"] = True
            result["_cache_key"] = cache_key
            _cache_set(cache_key, result)
            return result
        except Exception as e:
            return {"valid": False, "error": f"{type(e).__name__}: {e}", "tool": name}

    # Try equation_tools.run_tool as fallback
    if "equation_tools" not in _loaded:
        _load_module("equation_tools")
    try:
        return _loaded["equation_tools"].run_tool(name, **kwargs)
    except Exception:
        pass

    # Try eqsolve command functions
    if "eqsolve" not in _loaded:
        _load_module("eqsolve")
    eq = _loaded["eqsolve"]
    cmds = {k.replace("cmd_", ""): getattr(eq, k) for k in dir(eq) if k.startswith("cmd_")}
    if name in cmds:
        try:
            result = cmds[name](**kwargs)
            if isinstance(result, str):
                import json
                try:
                    return json.loads(result)
                except json.JSONDecodeError:
                    return {"valid": True, "result": result}
            return result
        except Exception as e:
            return {"valid": False, "error": f"{type(e).__name__}: {e}", "tool": name}

    return {"valid": False, "error": f"Tool not found: {name}"}


def list_all_tools() -> List[Dict[str, str]]:
    """List ALL available tools across all modules."""
    all_tools = []
    for mod_name in sorted(TOOL_MODULES.keys()):
        tools_dict = _get_tool_dict(mod_name)
        prefix = mod_name.split("_")[0]
        for key, info in tools_dict.items():
            desc = info.get("desc", info.get("description", ""))
            all_tools.append({
                "name": f"{prefix}_{key}",
                "module": mod_name,
                "description": desc,
            })
        # Also add bare names
        for key, info in tools_dict.items():
            desc = info.get("desc", info.get("description", ""))
            all_tools.append({
                "name": key,
                "module": mod_name,
                "description": desc,
                "alias": True,
            })

    # Deduplicate: keep the prefixed version, flag aliases
    seen = set()
    deduped = []
    for t in all_tools:
        if t["name"] not in seen:
            seen.add(t["name"])
            deduped.append(t)
    return deduped


def list_tools_by_domain() -> Dict[str, List[Dict[str, str]]]:
    """Group tools by domain/module."""
    domains = {}
    for mod_name in sorted(TOOL_MODULES.keys()):
        tools_dict = _get_tool_dict(mod_name)
        prefix = mod_name.split("_")[0]
        domain_name = prefix.capitalize()
        domain_tools = []
        for key, info in tools_dict.items():
            domain_tools.append({
                "name": key,
                "full_name": f"{prefix}_{key}",
                "description": info.get("desc", info.get("description", "")),
            })
        if domain_tools:
            domains[domain_name] = domain_tools
    return domains


def print_tool_summary() -> None:
    """Print a human-readable summary of all available tools."""
    domains = list_tools_by_domain()
    total = sum(len(tools) for tools in domains.values())
    print(f"\n{'='*60}")
    print(f"  CaptN-BRAIN Tool Registry — {total} tools across {len(domains)} domains")
    print(f"{'='*60}")
    for domain, tools in sorted(domains.items()):
        print(f"\n  [{domain}] — {len(tools)} tools:")
        for t in tools:
            print(f"    {t['full_name']:35s}  {t['description']}")
    print(f"\n  Total: {total} tools")
    print(f"{'='*60}\\n")