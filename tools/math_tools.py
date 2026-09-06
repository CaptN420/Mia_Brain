#!/usr/bin/env python3
"""
math_tools.py — Deterministic math toolkit (22 tools).

Pure Python, zero LLM.

Usage:
    python -m tools.math_tools gcd a=12 b=8
    python -m tools.math_tools quadratic a=1 b=-3 c=2
    summon_agents.py math-tools list
"""
from __future__ import annotations
import json, math
from typing import Any, Dict, List, Optional

def gcd(a: int, b: int) -> Dict[str, Any]:
    """PGCD (Euclide)."""
    if a == 0 and b == 0: return {"valid": False, "error": "a=b=0"}
    x, y = abs(a), abs(b)
    while y: x, y = y, x % y
    return {"valid": True, "a": a, "b": b, "gcd": x, "lcm": abs(a*b)//x if x else 0}

def lcm(a: int, b: int) -> Dict[str, Any]:
    g = gcd(a, b)
    if not g["valid"]: return g
    return {"valid": True, "a": a, "b": b, "lcm": abs(a*b)//g["gcd"]}

def is_prime(n: int) -> Dict[str, Any]:
    if n < 2: return {"valid": True, "n": n, "is_prime": False}
    d, s = n-1, 0
    while d % 2 == 0: d //= 2; s += 1
    for a in [2,3,5,7,11,13]:
        if a >= n: continue
        x = pow(a, d, n)
        if x in (1, n-1): continue
        for _ in range(s-1):
            x = pow(x, 2, n)
            if x == n-1: break
        else: return {"valid": True, "n": n, "is_prime": False}
    return {"valid": True, "n": n, "is_prime": True}

def fibonacci(n: int) -> Dict[str, Any]:
    if n < 0: return {"valid": False, "error": "n>=0"}
    a, b = 0, 1
    for _ in range(2, n+1): a, b = b, a+b
    return {"valid": True, "n": n, "fibonacci": b if n > 0 else 0}

def factorial(n: int) -> Dict[str, Any]:
    if n < 0: return {"valid": False, "error": "n>=0"}
    if n > 170: return {"valid": False, "error": "n>170"}
    r = 1
    for i in range(2, n+1): r *= i
    return {"valid": True, "n": n, "factorial": r}

def permutation(n: int, k: int) -> Dict[str, Any]:
    if k < 0 or k > n: return {"valid": False, "error": "0<=k<=n"}
    r = 1
    for i in range(n, n-k, -1): r *= i
    return {"valid": True, "n": n, "k": k, "permutations": r}

def binomial(n: int, k: int) -> Dict[str, Any]:
    if k < 0 or k > n: return {"valid": False, "error": "0<=k<=n"}
    k = min(k, n-k); c = 1
    for i in range(1, k+1): c = c * (n - k + i) // i
    return {"valid": True, "n": n, "k": k, "binomial": c}

def distance(x1, y1, x2, y2) -> Dict[str, Any]:
    d = math.sqrt((x2-x1)**2 + (y2-y1)**2)
    return {"valid": True, "distance": round(d, 6)}

def midpoint(x1, y1, x2, y2) -> Dict[str, Any]:
    return {"valid": True, "midpoint": [round((x1+x2)/2, 6), round((y1+y2)/2, 6)]}

def quadratic(a, b, c) -> Dict[str, Any]:
    if a == 0: return {"valid": False, "error": "a!=0"}
    d = b*b - 4*a*c
    if d >= 0:
        x1 = (-b + math.sqrt(d))/(2*a); x2 = (-b - math.sqrt(d))/(2*a)
        return {"valid": True, "real": True, "x1": round(x1,6), "x2": round(x2,6), "discriminant": round(d,6)}
    rp = -b/(2*a); ip = math.sqrt(-d)/(2*a)
    return {"valid": True, "real": False, "x1": f"{rp:.4f}+{ip:.4f}i", "x2": f"{rp:.4f}-{ip:.4f}i", "discriminant": round(d,6)}

def trig(sine=None, cosine=None, angle_deg=None) -> Dict[str, Any]:
    if angle_deg is not None:
        r = math.radians(angle_deg)
        t = "inf" if abs(math.cos(angle_deg)) < 1e-12 else round(math.tan(r), 6)
        return {"valid": True, "sin": round(math.sin(r),6), "cos": round(math.cos(r),6), "tan": t}
    if sine is not None:
        a = math.degrees(math.asin(min(1, max(-1, sine))))
        return {"valid": True, "sin": sine, "angle_deg": round(a,4)}
    if cosine is not None:
        a = math.degrees(math.acos(min(1, max(-1, cosine))))
        return {"valid": True, "cos": cosine, "angle_deg": round(a,4)}
    return {"valid": False, "error": "give sin/cos/angle"}

def log_(value=1, base=10) -> Dict[str, Any]:
    if value <= 0: return {"valid": False, "error": "value>0"}
    if base <= 0 or base == 1: return {"valid": False, "error": "bad base"}
    return {"valid": True, "value": value, "base": base, "log": round(math.log(value, base), 6)}

def exp_(x=0) -> Dict[str, Any]:
    return {"valid": True, "x": x, "exp": round(math.exp(x), 6)}

def pow_(base=2, exponent=3) -> Dict[str, Any]:
    try: return {"valid": True, "result": round(base**exponent, 6)}
    except Exception as e: return {"valid": False, "error": str(e)}

def abs_(x=0) -> Dict[str, Any]:
    return {"valid": True, "x": x, "abs": abs(x)}

def round_(value=0, decimals=2) -> Dict[str, Any]:
    return {"valid": True, "value": value, "rounded": round(value, decimals)}

def sum_(values=None) -> Dict[str, Any]:
    if not values: values = [1,2,3,4,5]
    return {"valid": True, "n": len(values), "sum": round(sum(values), 6)}

def mean(values=None) -> Dict[str, Any]:
    if not values: values = [1,2,3,4,5]
    return {"valid": True, "n": len(values), "mean": round(sum(values)/len(values), 6)}

def median(values=None) -> Dict[str, Any]:
    if not values: values = [1,3,3,6,7]
    s = sorted(values); n = len(s)
    if n % 2: return {"valid": True, "median": s[n//2]}
    return {"valid": True, "median": (s[n//2-1] + s[n//2])/2}

def variance(values=None, ddof=1) -> Dict[str, Any]:
    if not values: values = [1,2,3,4,5]
    if len(values) < 2: return {"valid": False, "error": ">=2"}
    m = sum(values)/len(values)
    v = sum((x-m)**2 for x in values)/(len(values)-ddof)
    return {"valid": True, "variance": round(v,6), "stddev": round(math.sqrt(v),6), "n": len(values), "mean": round(m,6)}

def stddev(values=None, ddof=1) -> Dict[str, Any]:
    return variance(values, ddof)

def correlation(xs=None, ys=None) -> Dict[str, Any]:
    if not xs: xs = [1,2,3,4,5]
    if not ys: ys = [2,4,5,4,5]
    if len(xs) < 3: return {"valid": False, "error": ">=3"}
    n = len(xs); mx = sum(xs)/n; my = sum(ys)/n
    num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
    dx = math.sqrt(sum((x-mx)**2 for x in xs))
    dy = math.sqrt(sum((y-my)**2 for y in ys))
    if dx*dy == 0: return {"valid": False, "error": "zero variance"}
    r = num/(dx*dy)
    return {"valid": True, "r": round(r,6), "n": n, "r_squared": round(r*r,6)}

MATH_TOOLS = {
    "gcd": {"fn": gcd, "desc": "PGCD of two ints"},
    "lcm": {"fn": lcm, "desc": "PPCM"},
    "is_prime": {"fn": is_prime, "desc": "Primality test"},
    "fibonacci": {"fn": fibonacci, "desc": "nth Fibonacci"},
    "factorial": {"fn": factorial, "desc": "n!"},
    "permutation": {"fn": permutation, "desc": "P(n,k)"},
    "binomial": {"fn": binomial, "desc": "C(n,k)"},
    "distance": {"fn": distance, "desc": "Euclidean 2D"},
    "midpoint": {"fn": midpoint, "desc": "Midpoint"},
    "quadratic": {"fn": quadratic, "desc": "ax^2+bx+c=0 roots"},
    "trig": {"fn": trig, "desc": "sin/cos/tan table"},
    "log": {"fn": log_, "desc": "Logarithm"},
    "exp": {"fn": exp_, "desc": "Exponential"},
    "pow": {"fn": pow_, "desc": "Power"},
    "abs": {"fn": abs_, "desc": "Absolute value"},
    "round": {"fn": round_, "desc": "Round to decimals"},
    "sum": {"fn": sum_, "desc": "Sum of list"},
    "mean": {"fn": mean, "desc": "Arithmetic mean"},
    "median": {"fn": median, "desc": "Median"},
    "variance": {"fn": variance, "desc": "Variance + stddev"},
    "correlation": {"fn": correlation, "desc": "Pearson correlation"},
}

def list_tools() -> List[Dict[str, str]]:
    return [{"name": k, "description": v["desc"]} for k, v in MATH_TOOLS.items()]

def _cmd_list(args) -> None:
    for t in list_tools():
        print(f"  {t['name']:25s} — {t['description']}")

def _parse_kwargs(args_list):
    kwargs = {}
    for kv in args_list:
        if "=" in kv:
            k, v = kv.split("=", 1)
            try:
                parsed = json.loads(v)
                kwargs[k] = parsed
            except (json.JSONDecodeError, ValueError):
                kwargs[k] = v
    return kwargs

def _cmd_run(args, tools_dict) -> None:
    fn = tools_dict[args.tool_name]["fn"]
    kwargs = _parse_kwargs(args.args)
    result = fn(**kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False))

def register_cli(subparsers) -> None:
    p = subparsers.add_parser("math", help="Math tools: gcd, lcm, factorial, quadratic, stats, etc.")
    sub = p.add_subparsers(dest="math_cmd")
    sp = sub.add_parser("list", help="List tools")
    sp.set_defaults(func=_cmd_list)
    for name in MATH_TOOLS:
        rp = sub.add_parser(name, help=MATH_TOOLS[name]["desc"])
        rp.add_argument("args", nargs="*")
        rp.set_defaults(func=lambda a, n=name: _cmd_run(a, MATH_TOOLS, n))

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Math tools")
    parser.add_argument("command", choices=["list"] + list(MATH_TOOLS.keys()))
    parser.add_argument("args", nargs="*")
    a = parser.parse_args()
    if a.command == "list":
        for t in list_tools(): print(f"  {t['name']:25s} — {t['description']}")
    elif a.command in MATH_TOOLS:
        kwargs = _parse_kwargs(a.args)
        result = MATH_TOOLS[a.command]["fn"](**kwargs)
        print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()