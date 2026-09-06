#!/usr/bin/env python3
"""coding_tools.py — Deterministic coding/software toolkit (6 tools)."""
from __future__ import annotations
import json, re
from typing import Any, Dict, List, Optional

def loc(source="") -> Dict[str, Any]:
    if not source: source = "def foo():\n    pass"
    lines = source.splitlines()
    cl = sum(1 for l in lines if l.strip() and not l.strip().startswith("#"))
    bk = sum(1 for l in lines if not l.strip())
    return {"valid": True, "total": len(lines), "code": cl, "blank": bk}

def cyclomatic(source="") -> Dict[str, Any]:
    if not source: source = "def f():\n    if x: pass\n    for y in z: pass"
    d = len(re.findall(r"\b(if|elif|for|while|and|or|except|with|assert)\b", source))
    r = "A(simple)" if d<10 else "B(moderate)" if d<20 else "C(complex)" if d<40 else "D(dangerous)"
    return {"valid": True, "cyclomatic": d+1, "decision_points": d, "rating": r}

def identify_type(value="42") -> Dict[str, Any]:
    v = value.strip()
    if v in ("True","False"): return {"valid": True, "type": "bool"}
    if v == "None": return {"valid": True, "type": "null"}
    try: int(v); return {"valid": True, "type": "int"}
    except: pass
    try: float(v); return {"valid": True, "type": "float"}
    except: pass
    if v.startswith("[") and v.endswith("]"): return {"valid": True, "type": "list"}
    if v.startswith("{") and v.endswith("}"): return {"valid": True, "type": "dict"}
    return {"valid": True, "type": "str"}

def validate_regex(pattern="^[a-z]+$") -> Dict[str, Any]:
    try: re.compile(pattern); return {"valid": True, "is_valid": True}
    except re.error as e: return {"valid": False, "is_valid": False, "error": str(e)}

def regex_match(pattern="\\d+", text="abc123") -> Dict[str, Any]:
    try:
        m = re.search(pattern, text)
        return {"valid": True, "matches": m is not None, "matched": m.group(0)[:100] if m else ""}
    except re.error as e: return {"valid": False, "error": str(e)}

def complexity_estimate(LOC=1000) -> Dict[str, Any]:
    effort = 2.4*(LOC/1000)**1.05; time = 2.5*effort**0.38
    return {"valid": True, "effort_person_months": round(effort,2), "time_months": round(time,2)}

CODING_TOOLS = {
    "loc": {"fn": loc, "desc": "Lines of code"},
    "cyclomatic": {"fn": cyclomatic, "desc": "Cyclomatic complexity"},
    "identify_type": {"fn": identify_type, "desc": "Type identification"},
    "validate_regex": {"fn": validate_regex, "desc": "Regex validation"},
    "regex_match": {"fn": regex_match, "desc": "Regex matching"},
    "complexity_estimate": {"fn": complexity_estimate, "desc": "Effort from LOC"},
}
TOOLS = CODING_TOOLS

def list_tools() -> List[Dict[str, str]]:
    return [{"name": k, "description": v["desc"]} for k, v in TOOLS.items()]

def _parse_kwargs(args_list):
    kwargs = {}
    for kv in args_list:
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                kwargs[k] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                kwargs[k] = v
    return kwargs

def _cmd_list(args):
    for t in list_tools(): print(f"  {t['name']:25s} — {t['description']}")

def _cmd_run(args, tools_dict, tool_name=None):
    if tool_name is None:
        tool_name = args.tool_name
    print(json.dumps(tools_dict[tool_name]["fn"](**_parse_kwargs(args.args)), indent=2, ensure_ascii=False))

def register_cli(subparsers):
    import re
    mod_name = __file__.rsplit("/",1)[-1].replace("_tools.py","")
    p = subparsers.add_parser(mod_name, help=f"{mod_name.title()} tools")
    sub = p.add_subparsers(dest="subcmd")
    sub.add_parser("list", help="List tools").set_defaults(func=_cmd_list)
    for name in TOOLS:
        rp = sub.add_parser(name, help=TOOLS[name]["desc"])
        rp.add_argument("args", nargs="*")
        rp.set_defaults(func=lambda a, n=name: _cmd_run(a, TOOLS, n))

def main():
    import argparse
    import sys
    mod_name = __file__.rsplit("/",1)[-1].replace("_tools.py","")
    parser = argparse.ArgumentParser(description=f"{mod_name.title()} tools")
    parser.add_argument("command", choices=["list"] + list(TOOLS.keys()))
    parser.add_argument("args", nargs="*")
    a = parser.parse_args()
    if a.command == "list":
        for t in list_tools(): print(f"  {t['name']:25s} — {t['description']}")
    elif a.command in TOOLS:
        result = TOOLS[a.command]["fn"](**_parse_kwargs(a.args))
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
