#!/usr/bin/env python3
"""
eqsolve.py — Solveur symbolique pour équations mathématiques.

Wrapper sympy déterministe.
Dérivée, intégrale, résolution, développement limité, systèmes.

Usage:
    python -m tools.eqsolve derive "k * A * B" --variable A
    python -m tools.eqsolve integrate "x**2" --variable x
    python -m tools.eqsolve solve "x**2 - 4" --variable x
    python -m tools.eqsolve solve-system '["x + y = 5", "x - y = 1"]'
    python -m tools.eqsolve taylor "sin(x)" --variable x --order 4
    python -m tools.eqsolve simplify "(x + y)**2 - (x**2 + 2*x*y + y**2)"
    python -m tools.eqsolve limit "sin(x)/x" --variable x --to 0
"""

import json
import sys
from typing import List, Optional


# ──────────────────────────────────────────────
# Lazy import sympy (souvent absent, donc soft fail)
# ──────────────────────────────────────────────
_sympy_available = False
_sympy_error = None

try:
    import sympy
    from sympy import (
        sympify, diff, integrate, solve, series, limit,
        simplify, expand, factor, symbols, Eq, solve_linear_system,
        Matrix, latex, pretty,
    )
    _sympy_available = True
except ImportError as e:
    _sympy_error = str(e)


def _require_sympy():
    if not _sympy_available:
        msg = f"sympy est requis pour eqsolve. Installez : pip install sympy\nErreur : {_sympy_error}"
        raise ImportError(msg)


def _parse_expr(expr: str):
    """Parse une expression string en expression sympy."""
    _require_sympy()
    try:
        return sympify(expr)
    except Exception as e:
        raise ValueError(f"Expression invalide : {expr} — {e}")


def _parse_variable(v: str):
    """Parse une variable string en symbole sympy."""
    _require_sympy()
    return symbols(v)


def _result(data: dict, ok: bool = True) -> str:
    data["ok"] = ok
    return json.dumps(data, indent=2, ensure_ascii=False)


def cmd_derive(expr: str, variable: str, order: int = 1) -> str:
    """Dérivée symbolique."""
    try:
        e = _parse_expr(expr)
        v = symbols(variable)
        result = diff(e, v, order)
        return _result({
            "operation": "derive",
            "expression": expr,
            "variable": variable,
            "order": order,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "derive"}, ok=False)


def cmd_integrate(expr: str, variable: str, definite: Optional[List[float]] = None) -> str:
    """Intégrale symbolique (indéfinie ou définie)."""
    try:
        e = _parse_expr(expr)
        v = symbols(variable)
        if definite and len(definite) == 2:
            from sympy import oo
            a = definite[0]
            b = definite[1]
            result = integrate(e, (v, a, b))
        else:
            result = integrate(e, v)
        return _result({
            "operation": "integrate",
            "expression": expr,
            "variable": variable,
            "definite": definite,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "integrate"}, ok=False)


def cmd_solve(expr: str, variable: str) -> str:
    """Résolution d'équation."""
    try:
        e = _parse_expr(expr)
        v = symbols(variable)
        solutions = solve(e, v)
        result_str = str(solutions) if isinstance(solutions, list) else str([solutions])
        return _result({
            "operation": "solve",
            "expression": expr,
            "variable": variable,
            "solutions": result_str,
            "latex": latex(solutions),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "solve"}, ok=False)


def cmd_solve_system(equations: List[str], variables: Optional[List[str]] = None) -> str:
    """Résolution d'un système d'équations."""
    try:
        _require_sympy()
        parsed_eqs = []
        all_vars = set()
        for eq_str in equations:
            if "=" in eq_str:
                lhs_str, rhs_str = eq_str.split("=", 1)
                lhs = _parse_expr(lhs_str.strip())
                rhs = _parse_expr(rhs_str.strip())
                parsed_eqs.append(Eq(lhs, rhs))
            else:
                parsed_eqs.append(_parse_expr(eq_str))
        # Find free symbols
        for eq in parsed_eqs:
            all_vars |= eq.free_symbols

        if variables:
            target_vars = [symbols(v) for v in variables]
        else:
            target_vars = list(all_vars)

        solutions = solve(parsed_eqs, target_vars, dict=True)

        return _result({
            "operation": "solve_system",
            "equations": equations,
            "variables": variables or [str(v) for v in target_vars],
            "solutions": str(solutions),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "solve_system"}, ok=False)


def cmd_taylor(expr: str, variable: str, order: int = 3, point: float = 0) -> str:
    """Développement limité (Taylor)."""
    try:
        e = _parse_expr(expr)
        v = symbols(variable)
        result = series(e, v, point, order + 1).removeO()
        return _result({
            "operation": "taylor",
            "expression": expr,
            "variable": variable,
            "order": order,
            "point": point,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "taylor"}, ok=False)


def cmd_simplify(expr: str) -> str:
    """Simplification d'expression."""
    try:
        e = _parse_expr(expr)
        result = simplify(e)
        return _result({
            "operation": "simplify",
            "expression": expr,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "simplify"}, ok=False)


def cmd_expand(expr: str) -> str:
    """Développement d'expression."""
    try:
        e = _parse_expr(expr)
        result = expand(e)
        return _result({
            "operation": "expand",
            "expression": expr,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "expand"}, ok=False)


def cmd_factor(expr: str) -> str:
    """Factorisation d'expression."""
    try:
        e = _parse_expr(expr)
        result = factor(e)
        return _result({
            "operation": "factor",
            "expression": expr,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "factor"}, ok=False)


def cmd_limit(expr: str, variable: str, to) -> str:
    """Calcul de limite."""
    try:
        e = _parse_expr(expr)
        v = symbols(variable)
        if isinstance(to, str):
            to_val = "oo" if to in ("oo", "inf", "+oo") else "-oo" if to in ("-oo", "-inf") else float(to)
        else:
            to_val = to
        if to_val == "oo" or to_val == float("inf"):
            from sympy import oo
            result = limit(e, v, oo)
        elif to_val == "-oo":
            from sympy import oo
            result = limit(e, v, -oo)
        else:
            result = limit(e, v, float(to_val))
        return _result({
            "operation": "limit",
            "expression": expr,
            "variable": variable,
            "to": to,
            "result": str(result),
            "latex": latex(result),
        })
    except Exception as ex:
        return _result({"error": str(ex), "operation": "limit"}, ok=False)


def cmd_list_tools() -> str:
    """Liste des opérations disponibles."""
    tools = [
        {"name": "derive", "description": "Dérivée symbolique (ordre n)", "args": "expr, variable, [order]"},
        {"name": "integrate", "description": "Intégrale symbolique (définie ou non)", "args": "expr, variable, [definite]"},
        {"name": "solve", "description": "Résolution d'équation", "args": "expr, variable"},
        {"name": "solve-system", "description": "Système d'équations", "args": "equations, [variables]"},
        {"name": "taylor", "description": "Développement limité (Taylor)", "args": "expr, variable, [order], [point]"},
        {"name": "simplify", "description": "Simplification d'expression", "args": "expr"},
        {"name": "expand", "description": "Développement d'expression", "args": "expr"},
        {"name": "factor", "description": "Factorisation", "args": "expr"},
        {"name": "limit", "description": "Limite d'expression", "args": "expr, variable, to"},
    ]
    return json.dumps(tools, indent=2, ensure_ascii=False)


def _dispatch_eqsolve(args):
    """Dispatch eqsolve subcommand using pre-parsed args."""
    import json
    import sys
    try:
        if args.command == "list":
            print(cmd_list_tools())
        elif args.command == "derive":
            print(cmd_derive(args.expr, args.variable, args.order))
        elif args.command == "integrate":
            definite = None
            if hasattr(args, 'from_') and args.from_ is not None and hasattr(args, 'to') and args.to is not None:
                definite = [args.from_, args.to]
            print(cmd_integrate(args.expr, args.variable, definite))
        elif args.command == "solve":
            print(cmd_solve(args.expr, args.variable))
        elif args.command == "solve-system":
            print(cmd_solve_system(args.equations, args.variables))
        elif args.command == "taylor":
            print(cmd_taylor(args.expr, args.variable, args.order, args.point))
        elif args.command == "simplify":
            print(cmd_simplify(args.expr))
        elif args.command == "expand":
            print(cmd_expand(args.expr))
        elif args.command == "factor":
            print(cmd_factor(args.expr))
        elif args.command == "limit":
            print(cmd_limit(args.expr, args.variable, args.to))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, indent=2))
        sys.exit(1)


def register_cli(subparsers):
    """Register eqsolve as a CLI subcommand."""
    p = subparsers.add_parser("eqsolve", help="Solveur symbolique (sympy)")
    sub = p.add_subparsers(dest="command", required=True)

    p_d = sub.add_parser("derive", help="Dérivée symbolique")
    p_d.add_argument("expr", help="Expression (ex: k*A*B)")
    p_d.add_argument("--variable", "-v", required=True, help="Variable de dérivation")
    p_d.add_argument("--order", "-n", type=int, default=1, help="Ordre de dérivation")
    p_d.set_defaults(func=_dispatch_eqsolve)

    p_i = sub.add_parser("integrate", help="Intégrale symbolique")
    p_i.add_argument("expr", help="Expression")
    p_i.add_argument("--variable", "-v", required=True, help="Variable d'intégration")
    p_i.add_argument("--from", "-f", type=float, dest="from_", help="Borne inférieure")
    p_i.add_argument("--to", "-t", type=float, help="Borne supérieure")
    p_i.set_defaults(func=_dispatch_eqsolve)

    p_s = sub.add_parser("solve", help="Résoudre équation")
    p_s.add_argument("expr", help="Expression ou équation (x**2 - 4)")
    p_s.add_argument("--variable", "-v", required=True, help="Variable inconnue")
    p_s.set_defaults(func=_dispatch_eqsolve)

    p_ss = sub.add_parser("solve-system", help="Système d'équations")
    p_ss.add_argument("equations", nargs="+", help='Équations: "x + y = 5" "x - y = 1"')
    p_ss.add_argument("--variables", "-v", nargs="+", help="Variables à résoudre")
    p_ss.set_defaults(func=_dispatch_eqsolve)

    p_t = sub.add_parser("taylor", help="Développement limité")
    p_t.add_argument("expr", help="Expression")
    p_t.add_argument("--variable", "-v", required=True, help="Variable")
    p_t.add_argument("--order", "-n", type=int, default=3, help="Ordre du développement")
    p_t.add_argument("--point", "-p", type=float, default=0, help="Point de développement")
    p_t.set_defaults(func=_dispatch_eqsolve)

    p_sp = sub.add_parser("simplify", help="Simplifier expression")
    p_sp.add_argument("expr", help="Expression")
    p_sp.set_defaults(func=_dispatch_eqsolve)

    p_ex = sub.add_parser("expand", help="Développer expression")
    p_ex.add_argument("expr", help="Expression")
    p_ex.set_defaults(func=_dispatch_eqsolve)

    p_fa = sub.add_parser("factor", help="Factoriser expression")
    p_fa.add_argument("expr", help="Expression")
    p_fa.set_defaults(func=_dispatch_eqsolve)

    p_l = sub.add_parser("limit", help="Calculer limite")
    p_l.add_argument("expr", help="Expression")
    p_l.add_argument("--variable", "-v", required=True, help="Variable")
    p_l.add_argument("--to", required=True, help="Point (nombre, 'oo', '-oo')")
    p_l.set_defaults(func=_dispatch_eqsolve)

    p_list = sub.add_parser("list", help="Liste des outils disponibles")
    p_list.set_defaults(func=_dispatch_eqsolve)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Solveur symbolique (wrapper sympy)")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()