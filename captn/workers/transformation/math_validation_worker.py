"""MathValidationWorker — Expert en mathématiques anciennes ET modernes.

Validateur mathématique déterministe (no LLM, pure Python / sympy optionnel).

Compétences couvertes :
── Mathématiques ANCIENNES ──
  • Pythagore : triplets pythagoriciens, nombres figurés (triangulaires, carrés, pentagonaux)
  • Euclide : algorithme d'Euclide (PGCD), nombres parfaits, éléments d'Euclide
  • Archimède : approximation de π, méthode d'exhaustion, spirale d'Archimède
  • Égypte antique : fractions égyptiennes, Papyrus Rhind
  • Babylone : base 60, système sexagésimal, tablettes Plimpton
  • Grèce antique : nombre d'or φ, moyenne proportionnelle, duplication du cube
  • Chine ancienne : théorème chinois des restes, triangle de Pascal (Chu Shih-Chieh)
  • Inde ancienne : zéro, système décimal, trigonométrie ancienne (Aryabhata)

── Mathématiques MODERNES ──
  • Analyse : dérivées, intégrales, séries, convergence
  • Algèbre linéaire : matrices, déterminants, vecteurs propres
  • Théorie des groupes : symétries, permutations
  • Théorie des nombres : nombres premiers, factorisation, primalité
  • Analyse complexe : nombres complexes, plan complexe
  • Combinatoire : factorielle, coefficients binomiaux, permutations
  • Géométrie analytique : coniques, transformation de coordonnées
  • Logique mathématique : ensembles, prédicats

Message contract (bus):
    request : Message(type="task", destination="math_validation", payload={
                  "task_id": str,
                  "expression": str,
                  "mode": "parse"|"evaluate"|"dimensional"|"verify"|
                          "ancient"|"modern"|"classify_math",
                  "variables": dict,
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "math_validation",
                  "valid": bool, "result": dict|None,
                  "error_message": str (when invalid)
              })
"""

from __future__ import annotations

import ast
import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("MathValidationWorker")

# =========================================================================
# PARTIE 1 — Safe expression evaluator (inchangé)
# =========================================================================

_SAFE_GLOBALS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "complex": complex,
    "divmod": divmod, "enumerate": enumerate, "filter": filter,
    "float": float, "hex": hex, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "oct": oct, "ord": ord, "pow": pow, "range": range, "repr": repr,
    "reversed": reversed, "round": round, "set": set, "slice": slice,
    "sorted": sorted, "str": str, "sum": sum, "tuple": tuple, "type": type,
    "zip": zip,
    "math": math,
    "pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf,
    "nan": math.nan,
}

_OPERATORS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
    ast.UAdd: lambda a: +a,
    ast.USub: lambda a: -a,
    ast.Not: lambda a: not a,
    ast.And: lambda a, b: a and b,
    ast.Or: lambda a, b: a or b,
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


def _safe_eval(node: ast.AST, variables: Dict[str, float]) -> float:
    """Evaluate an AST expression safely — no ``exec`` or ``eval``."""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body, variables)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in variables:
            return variables[node.id]
        if node.id in _SAFE_GLOBALS:
            return _SAFE_GLOBALS[node.id]
        raise ValueError(f"Unknown variable: {node.id}")
    if isinstance(node, ast.BinOp):
        fn = _OPERATORS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return fn(_safe_eval(node.left, variables), _safe_eval(node.right, variables))
    if isinstance(node, ast.UnaryOp):
        fn = _OPERATORS.get(type(node.op))
        if fn is None:
            raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")
        return fn(_safe_eval(node.operand, variables))
    if isinstance(node, ast.Call):
        func = _safe_eval(node.func, variables)
        if not callable(func):
            raise ValueError(f"Called a non-callable: {func}")
        args = [_safe_eval(a, variables) for a in node.args]
        return func(*args)
    if isinstance(node, ast.Attribute):
        obj = _safe_eval(node.value, variables)
        return getattr(obj, node.attr)
    if isinstance(node, ast.List):
        return [_safe_eval(e, variables) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_safe_eval(e, variables) for e in node.elts)
    if isinstance(node, ast.Dict):
        return {_safe_eval(k, variables): _safe_eval(v, variables)
                for k, v in zip(node.keys, node.values)}
    raise ValueError(f"Unsupported AST node: {type(node).__name__}")


def _extract_variables(expression: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", expression)
    reserved = {"math", "pi", "e", "tau", "inf", "nan", "sin", "cos", "tan",
                "sqrt", "log", "exp", "abs", "int", "float", "str", "list",
                "set", "dict", "tuple", "bool", "max", "min", "len", "sum",
                "pow", "round", "range", "sorted", "reversed", "enumerate",
                "zip", "map", "filter", "all", "any", "divmod", "hex", "oct",
                "ord", "chr", "repr", "type", "isinstance", "slice"}
    return sorted(set(t for t in tokens if t not in reserved and not t.startswith("_")))


def _check_balance(expression: str) -> Tuple[bool, str]:
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    for i, ch in enumerate(expression):
        if ch in '([{':
            stack.append((ch, i))
        elif ch in ')]}':
            if not stack or stack[-1][0] != pairs[ch]:
                return False, f"Unmatched '{ch}' at position {i}"
            stack.pop()
    if stack:
        return False, f"Unclosed '{stack[-1][0]}' at position {stack[-1][1]}"
    return True, ""


# =========================================================================
# PARTIE 2 — Dimensional analysis (inchangé)
# =========================================================================

_BASE_DIMS = {
    "M": "mass", "L": "length", "T": "time",
    "I": "current", "Θ": "temperature", "N": "amount", "J": "luminosity",
}

_PHYSICAL_DIMS: Dict[str, Dict[str, int]] = {
    "G": {"M": -1, "L": 3, "T": -2},
    "c": {"L": 1, "T": -1},
    "h": {"M": 1, "L": 2, "T": -1},
    "k_B": {"M": 1, "L": 2, "T": -2, "Θ": -1},
    "ε₀": {"M": -1, "L": -3, "T": 4, "I": 2},
    "μ₀": {"M": 1, "L": 1, "T": -2, "I": -2},
}

_FORMULA_SIGNATURES: Dict[str, Dict[str, int]] = {
    "F=ma": {"M": 1, "L": 1, "T": -2},
    "E=mc²": {"M": 1, "L": 2, "T": -2},
    "p=mv": {"M": 1, "L": 1, "T": -1},
    "W=Fd": {"M": 1, "L": 2, "T": -2},
    "P=F/A": {"M": 1, "L": -1, "T": -2},
    "v=λf": {"L": 1, "T": -1},
    "F=kx": {"M": 1, "L": 0, "T": -2},
}


# =========================================================================
# PARTIE 3 — MATHÉMATIQUES ANCIENNES
# =========================================================================

# -----------------------------------------------------------------------
# 3A. Pythagore — Triplets pythagoriciens
# -----------------------------------------------------------------------
_PYTHAGOREAN_TRIPLES = [(3, 4, 5), (5, 12, 13), (8, 15, 17), (7, 24, 25),
                         (20, 21, 29), (12, 35, 37), (9, 40, 41), (11, 60, 61)]


def _is_pythagorean_triple(a: int, b: int, c: int) -> bool:
    """Vérifie si (a, b, c) est un triplet pythagoricien : a² + b² = c²."""
    return a * a + b * b == c * c


def _generate_pythagorean_triples(limit: int) -> List[Tuple[int, int, int]]:
    """Génère des triplets pythagoriciens primitifs jusqu'à une limite."""
    triples = []
    # Formule d'Euclide : a = m² - n², b = 2mn, c = m² + n²
    for m in range(2, int(limit ** 0.5) + 2):
        for n in range(1, m):
            if (m - n) % 2 == 1 and math.gcd(m, n) == 1:  # primitif
                a = m * m - n * n
                b = 2 * m * n
                c = m * m + n * n
                if c <= limit:
                    triples.append((a, b, c))
    return sorted(triples, key=lambda x: x[2])


# -----------------------------------------------------------------------
# 3B. Nombres figurés (Pythagore, École pythagoricienne)
# -----------------------------------------------------------------------
def _triangular_number(n: int) -> int:
    """Nombre triangulaire T_n = n(n+1)/2."""
    return n * (n + 1) // 2


def _square_number(n: int) -> int:
    """Nombre carré S_n = n²."""
    return n * n


def _pentagonal_number(n: int) -> int:
    """Nombre pentagonal P_n = n(3n-1)/2."""
    return n * (3 * n - 1) // 2


def _hexagonal_number(n: int) -> int:
    """Nombre hexagonal H_n = n(2n-1)."""
    return n * (2 * n - 1)


def _is_triangular(t: int) -> bool:
    """Teste si t est un nombre triangulaire : 8t+1 doit être un carré parfait."""
    s = int(math.isqrt(8 * t + 1))
    return s * s == 8 * t + 1


def _figurate_numbers(limit: int) -> Dict[str, List[int]]:
    """Génère les nombres figurés jusqu'à une limite."""
    n = 1
    result = {"triangular": [], "square": [], "pentagonal": [], "hexagonal": []}
    while True:
        tri = _triangular_number(n)
        sq = _square_number(n)
        pent = _pentagonal_number(n)
        hexa = _hexagonal_number(n)
        if tri > limit and sq > limit and pent > limit and hexa > limit:
            break
        if tri <= limit:
            result["triangular"].append(tri)
        if sq <= limit:
            result["square"].append(sq)
        if pent <= limit:
            result["pentagonal"].append(pent)
        if hexa <= limit:
            result["hexagonal"].append(hexa)
        n += 1
    return result


# -----------------------------------------------------------------------
# 3C. Euclide — Algorithme d'Euclide, nombres parfaits
# -----------------------------------------------------------------------
def _euclidean_gcd(a: int, b: int) -> List[int]:
    """Algorithme d'Euclide étendu : retourne (gcd, x, y) où ax + by = gcd."""
    if b == 0:
        return [a, 1, 0]
    g, x1, y1 = _euclidean_gcd(b, a % b)
    return [g, y1, x1 - (a // b) * y1]


def _is_perfect_number(n: int) -> bool:
    """Nombre parfait : somme des diviseurs propres = n (Euclide, Éléments VII)."""
    if n < 2:
        return False
    # Formule d'Euclide-Euler : si 2^p - 1 est premier (Mersenne), alors 2^(p-1)(2^p-1) est parfait
    s = 1
    for i in range(2, int(math.isqrt(n)) + 1):
        if n % i == 0:
            s += i
            if i != n // i:
                s += n // i
    return s == n


def _perfect_numbers(limit: int) -> List[int]:
    """Liste les nombres parfaits jusqu'à une limite."""
    return [n for n in range(2, limit + 1) if _is_perfect_number(n)]


# -----------------------------------------------------------------------
# 3D. Archimède — Approximation de π, méthode d'exhaustion
# -----------------------------------------------------------------------
def _archimedes_pi(n_sides: int = 96) -> Dict[str, Any]:
    """Approximation de π par la méthode d'Archimède (polygones inscrits/circonscrits).

    Archimède a utilisé un polygone à 96 côtés pour encadrer π entre 3+10/71 et 3+1/7.
    """
    # Méthode moderne équivalente
    inner_perim = 0.0
    outer_perim = 0.0
    for n in [6, 12, 24, 48, n_sides]:
        angle = math.pi / n
        # Périmètre intérieur : n * sin(π/n)
        inner_perim = n * math.sin(angle)
        # Périmètre extérieur : n * tan(π/n)
        outer_perim = n * math.tan(angle)
    return {
        "n_sides": n_sides,
        "lower_bound": round(inner_perim / 2, 6),   # π > perimeter/2
        "upper_bound": round(outer_perim / 2, 6),
        "archimedes_range": f"[{inner_perim/2:.4f}, {outer_perim/2:.4f}]",
        "pi_actual": math.pi,
        "error": round((outer_perim / 2 - inner_perim / 2) / 2, 6),
    }


def _archimedes_spiral(theta_start: float = 0, theta_end: float = 4 * math.pi, steps: int = 100) -> Dict[str, Any]:
    """Génère les points de la spirale d'Archimède : r = a + bθ."""
    a, b = 1.0, 0.5
    points = []
    for i in range(steps + 1):
        theta = theta_start + (theta_end - theta_start) * i / steps
        r = a + b * theta
        points.append({"theta": round(theta, 4), "r": round(r, 4),
                        "x": round(r * math.cos(theta), 4),
                        "y": round(r * math.sin(theta), 4)})
    return {"formula": "r = a + bθ", "a": a, "b": b, "points_sample": points[:5],
            "total_points": len(points)}


# -----------------------------------------------------------------------
# 3E. Égypte antique — Fractions égyptiennes
# -----------------------------------------------------------------------
def _egyptian_fraction(numerator: int, denominator: int) -> List[Dict[str, Any]]:
    """Décompose une fraction en fractions unitaires égyptiennes (algorithme glouton).

    Exemple : 7/10 = 1/2 + 1/5  (mais l'algorithme donne 1/2 + 1/5 + 1/10)
    On utilise l'algo de Fibonacci-Sylvester pour la décomposition canonique.
    """
    if numerator <= 0 or denominator <= 0:
        return []
    if numerator == 1:
        return [{"unit": f"1/{denominator}", "numerator": 1, "denominator": denominator}]

    fractions = []
    a, b = numerator, denominator
    while a > 0:
        # Plus petite unité : ceil(b/a)
        unit_den = (b + a - 1) // a  # ceil division
        fractions.append({
            "unit": f"1/{unit_den}",
            "numerator": 1,
            "denominator": unit_den,
        })
        a = a * unit_den - b
        b = b * unit_den
        g = math.gcd(a, b) if a > 0 else 1
        if g > 1:
            a //= g
            b //= g
    return fractions


# Papyrus Rhind — problèmes résolus
_RHIND_PROBLEMS = {
    "Rhind #24": "Une quantité et son 1/7 donnent 19. Trouve la quantité.",
    "Rhind #50": "Aire d'un champ circulaire de diamètre 9 : (8/9 × 9)² = 64.",
    "Rhind #56": "Pente d'une pyramide (seked).",
    "Rhind #79": "Somme d'une progression géométrique : 7 maisons, 49 chats, 343 souris...",
}


# -----------------------------------------------------------------------
# 3F. Babylone — Base 60 / Sexagésimal
# -----------------------------------------------------------------------
def _decimal_to_sexagesimal(n: float) -> Dict[str, Any]:
    """Convertit un nombre décimal en notation sexagésimale babylonienne."""
    whole = int(n)
    frac = n - whole
    parts = []
    # Partie entière en base 60
    w = whole
    while w > 0:
        parts.insert(0, w % 60)
        w //= 60
    if not parts:
        parts = [0]

    # Partie fractionnaire en base 60 (3 digits max)
    frac_parts = []
    f = frac
    for _ in range(3):
        f *= 60
        digit = int(f)
        frac_parts.append(digit)
        f -= digit
        if f == 0:
            break

    return {
        "decimal": n,
        "sexagesimal": f"{'.'.join(str(p) for p in parts)};{','.join(str(p) for p in frac_parts)}" if frac_parts else ";".join(str(p) for p in parts),
        "integer_part_base60": parts,
        "fractional_part_base60": frac_parts,
        "cuneiform_note": f"𒐕𒐖𒐗 (notation simplifiée)" if parts else "(zéro)",
    }


# -----------------------------------------------------------------------
# 3G. Nombre d'or φ — Mathématiques grecques antiques
# -----------------------------------------------------------------------
PHI = (1 + math.sqrt(5)) / 2  # Nombre d'or


def _golden_ratio_properties() -> Dict[str, Any]:
    """Propriétés du nombre d'or φ = (1 + √5)/2."""
    return {
        "phi": round(PHI, 10),
        "phi_squared": round(PHI * PHI, 10),
        "phi_reciprocal": round(1 / PHI, 10),
        "phi_minus_1": round(PHI - 1, 10),
        "phi_approximation": 1.6180339887,
        "euclid_definition": "Division d'un segment en moyenne et extrême raison (Éléments, Livre VI)",
        "fibonacci_limit": "φ est la limite du rapport de deux termes consécutifs de Fibonacci",
        "pentagram": "Le pentagramme pythagoricien contient φ dans ses proportions",
    }


def _golden_ratio_check(value: float, tolerance: float = 1e-6) -> Dict[str, Any]:
    """Vérifie si une valeur est égale à φ (nombre d'or) à tolérance près."""
    return {
        "value": value,
        "is_phi": abs(value - PHI) < tolerance or abs(value - 1 / PHI) < tolerance,
        "diff_from_phi": round(value - PHI, 6),
        "phi_ratio": round(value / PHI, 6) if abs(value) > 1e-12 else None,
    }


# -----------------------------------------------------------------------
# 3H. Chine ancienne — Théorème chinois des restes
# -----------------------------------------------------------------------
def _chinese_remainder_theorem(moduli: List[int], remainders: List[int]) -> Dict[str, Any]:
    """Résout un système de congruences avec le théorème chinois des restes.

    Trouve x tel que x ≡ r_i (mod m_i) pour tout i.
    Les moduli doivent être premiers entre eux deux à deux.
    """
    if len(moduli) != len(remainders):
        return {"valid": False, "error": "moduli and remainders length mismatch"}
    if not moduli:
        return {"valid": False, "error": "empty system"}

    # Vérifier que les moduli sont premiers entre eux
    for i in range(len(moduli)):
        for j in range(i + 1, len(moduli)):
            if math.gcd(moduli[i], moduli[j]) != 1:
                return {"valid": False,
                        "error": f"moduli {moduli[i]} and {moduli[j]} are not coprime"}

    M = 1
    for m in moduli:
        M *= m

    result = 0
    steps = []
    for i in range(len(moduli)):
        Mi = M // moduli[i]
        # Inverse de Mi modulo moduli[i] (algorithme d'Euclide étendu)
        g, yi, _ = _euclidean_gcd(Mi, moduli[i])
        inv = yi % moduli[i]
        term = remainders[i] * Mi * inv
        steps.append({
            "step": i + 1,
            "Mi": Mi,
            "inverse": inv,
            "term": term,
        })
        result += term

    x = result % M
    return {
        "valid": True,
        "x": x,
        "modulus": M,
        "system": f"x ≡ {remainders} (mod {moduli})",
        "solution": f"x ≡ {x} (mod {M})",
        "steps": steps,
        "source": "Sun Tzu (IIIe siècle), généralisé par Qin Jiushao (XIIIe siècle)",
    }


# =========================================================================
# PARTIE 4 — MATHÉMATIQUES MODERNES
# =========================================================================


# -----------------------------------------------------------------------
# 4A. Nombres premiers — Test de primalité, factorisation
# -----------------------------------------------------------------------
def _is_prime(n: int) -> bool:
    """Test de primalité déterministe pour n ≤ 10^6."""
    if n < 2:
        return False
    if n < 4:
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False
    i = 5
    while i * i <= n:
        if n % i == 0 or n % (i + 2) == 0:
            return False
        i += 6
    return True


def _prime_factors(n: int) -> List[Dict[str, Any]]:
    """Factorisation en nombres premiers."""
    factors = []
    temp = n
    for p in [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31]:
        count = 0
        while temp % p == 0:
            temp //= p
            count += 1
        if count > 0:
            factors.append({"prime": p, "exponent": count, "value": p ** count})
    if temp > 1:
        # Tester les candidats 6k ± 1
        d = 37
        while d * d <= temp:
            count = 0
            while temp % d == 0:
                temp //= d
                count += 1
            if count > 0:
                factors.append({"prime": d, "exponent": count, "value": d ** count})
            d += 2
            if d * d <= temp:
                count = 0
                while temp % d == 0:
                    temp //= d
                    count += 1
                if count > 0:
                    factors.append({"prime": d, "exponent": count, "value": d ** count})
            d += 4
        if temp > 1:
            factors.append({"prime": temp, "exponent": 1, "value": temp})
    return factors


def _primes_until(limit: int) -> List[int]:
    """Crible d'Ératosthène : tous les nombres premiers jusqu'à limit."""
    sieve = [True] * (limit + 1)
    sieve[0] = sieve[1] = False
    for i in range(2, int(limit ** 0.5) + 1):
        if sieve[i]:
            for j in range(i * i, limit + 1, i):
                sieve[j] = False
    return [i for i, is_prime in enumerate(sieve) if is_prime]


# -----------------------------------------------------------------------
# 4B. Combinatoire
# -----------------------------------------------------------------------
def _factorial(n: int) -> int:
    """Factorielle n! (iterative, pas de stack overflow)."""
    if n < 0:
        raise ValueError("Factorial of negative number")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def _binomial(n: int, k: int) -> int:
    """Coefficient binomial C(n,k) = n!/(k!(n-k)!)."""
    if k < 0 or k > n:
        return 0
    if k == 0 or k == n:
        return 1
    # Forme optimisée : choisir le plus petit k
    k = min(k, n - k)
    result = 1
    for i in range(1, k + 1):
        result = result * (n - k + i) // i
    return result


def _pascals_triangle(rows: int) -> List[List[int]]:
    """Triangle de Pascal (Chu Shih-Chieh, 1303)."""
    triangle = []
    for i in range(rows):
        row = []
        for j in range(i + 1):
            row.append(_binomial(i, j))
        triangle.append(row)
    return triangle


def _permutations(n: int, k: int) -> int:
    """Nombre de permutations P(n,k) = n!/(n-k)!."""
    if k < 0 or k > n:
        return 0
    result = 1
    for i in range(n, n - k, -1):
        result *= i
    return result


# -----------------------------------------------------------------------
# 4C. Algèbre linéaire
# -----------------------------------------------------------------------
def _matrix_determinant_2x2(a: float, b: float, c: float, d: float) -> float:
    """Déterminant d'une matrice 2×2 : det([[a,b],[c,d]]) = ad - bc."""
    return a * d - b * c


def _matrix_trace(matrix: List[List[float]]) -> float:
    """Trace d'une matrice carrée (somme des éléments diagonaux)."""
    if not matrix or len(matrix) != len(matrix[0]):
        return 0.0
    return sum(matrix[i][i] for i in range(len(matrix)))


def _vector_dot(v1: List[float], v2: List[float]) -> float:
    """Produit scalaire de deux vecteurs."""
    if len(v1) != len(v2):
        raise ValueError("Vector dimension mismatch")
    return sum(a * b for a, b in zip(v1, v2))


def _vector_cross(v1: List[float], v2: List[float]) -> List[float]:
    """Produit vectoriel 3D de deux vecteurs."""
    if len(v1) != 3 or len(v2) != 3:
        raise ValueError("Cross product is defined for 3D vectors only")
    return [
        v1[1] * v2[2] - v1[2] * v2[1],
        v1[2] * v2[0] - v1[0] * v2[2],
        v1[0] * v2[1] - v1[1] * v2[0],
    ]


def _vector_norm(v: List[float]) -> float:
    """Norme euclidienne d'un vecteur."""
    return math.sqrt(sum(x * x for x in v))


# -----------------------------------------------------------------------
# 4D. Analyse — Convergence de séries
# -----------------------------------------------------------------------
def _fibonacci(limit: int) -> List[int]:
    """Suite de Fibonacci (Leonardo de Pise, 1202)."""
    seq = [0, 1]
    while seq[-1] + seq[-2] <= limit:
        seq.append(seq[-1] + seq[-2])
    return seq


def _fibonacci_n(n: int) -> int:
    """N-ième terme de Fibonacci (itératif, O(n))."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return 0
    a, b = 0, 1
    for _ in range(2, n + 1):
        a, b = b, a + b
    return b


def _arithmetic_series(a: float, d: float, n: int) -> Dict[str, Any]:
    """Suite arithmétique : termes et somme S = n(2a + (n-1)d)/2."""
    terms = [a + i * d for i in range(n)]
    return {
        "first_term": a,
        "common_difference": d,
        "n_terms": n,
        "terms": terms,
        "last_term": terms[-1],
        "sum": n * (2 * a + (n - 1) * d) / 2,
    }


def _geometric_series(a: float, r: float, n: int) -> Dict[str, Any]:
    """Suite géométrique : termes et somme S = a(1-rⁿ)/(1-r) si r≠1."""
    terms = [a * (r ** i) for i in range(n)]
    if abs(r - 1.0) < 1e-12:
        s = a * n
    else:
        s = a * (1 - r ** n) / (1 - r)
    return {
        "first_term": a,
        "common_ratio": r,
        "n_terms": n,
        "terms": terms,
        "last_term": terms[-1],
        "sum": s,
    }


def _convergence_check(series_type: str, **params) -> Dict[str, Any]:
    """Vérifie la convergence d'une série classique."""
    if series_type == "geometric":
        r = abs(params.get("r", 0))
        return {
            "type": "geometric",
            "converges": r < 1,
            "diverges": r >= 1,
            "condition": f"|r| = {r} {'<' if r < 1 else '≥'} 1 → "
                        f"{'converge' if r < 1 else 'diverge'}",
        }
    elif series_type == "harmonic":
        return {
            "type": "harmonic (Σ 1/n)",
            "converges": False,
            "diverges": True,
            "condition": "La série harmonique Σ 1/n diverge (test de la p-série avec p=1)",
        }
    elif series_type == "p_series":
        p = params.get("p", 1)
        return {
            "type": f"p-série (Σ 1/n^{p})",
            "converges": p > 1,
            "diverges": p <= 1,
            "condition": f"p = {p} {'>1 → converge' if p > 1 else '≤1 → diverge'}",
        }
    return {"type": series_type, "converges": None, "error": "unknown series type"}


# -----------------------------------------------------------------------
# 4E. Analyse complexe
# -----------------------------------------------------------------------
def _complex_operations(real: float, imag: float, op: str,
                         other_real: float = 0, other_imag: float = 0) -> Dict[str, Any]:
    """Opérations sur les nombres complexes."""
    z = complex(real, imag)
    if op == "conjugate":
        result = z.conjugate()
    elif op == "modulus":
        return {"value": complex(real, imag), "modulus": abs(z),
                "argument": math.atan2(imag, real)}
    elif op == "add":
        result = z + complex(other_real, other_imag)
    elif op == "multiply":
        result = z * complex(other_real, other_imag)
    elif op == "power":
        if other_real != 0 or other_imag != 0:
            result = z ** complex(other_real, other_imag)
        else:
            result = z
    else:
        return {"error": f"unknown operation: {op}"}

    return {
        "operation": op,
        "z": str(z),
        "result": str(result),
        "real": result.real if isinstance(result, complex) else None,
        "imag": result.imag if isinstance(result, complex) else None,
    }


# =========================================================================
# PARTIE 5 — CLASSIFICATION MATHÉMATIQUE (ancien vs moderne)
# =========================================================================

_MATH_DOMAIN_PATTERNS = {
    "pythagorean": [
        r"\bpythagore", r"\btriple[ts]?\s*pythagor", r"\b3[,\s]*4[,\s]*5\b",
        r"\ba\²\s*\+\s*b\²\s*=\s*c\²|\ba\^2\s*\+\s*b\^2\s*=\s*c\^2",
    ],
    "euclidean": [
        r"\beuclid", r"\bpgcd\b|\bgcd\b", r"\bperfect\s*number", r"\bparfait\b",
        r"\béléments\b|\belements\b",
    ],
    "archimedean": [
        r"\barchim[eè]de", r"\bexhaustion\b", r"\bspiral\b.*\barchim",
        r"\bπ\b.*\bpolygon", r"\bpi\b.*\bpolygon",
    ],
    "egyptian_fraction": [
        r"\bégypti[ae]n\b|\begyptian", r"\brhind\b", r"\bpapyrus\b",
        r"\bfraction\s*unit", r"\b1/",
    ],
    "babylonian": [
        r"\bbabylon", r"\bsexag[eé]sim", r"\bbase.60\b",
        r"\bplimpton\b",
    ],
    "golden_ratio": [
        r"\bφ\b|\bphi\b", r"\bgolden.ratio\b", r"\bnombre.d.or\b",
        r"\bmoyenne.et.extrême\b|\bmean.and.extreme\b",
    ],
    "chinese_remainder": [
        r"\bchinois?.*reste", r"\bsun.tzu\b", r"\bQin\b.*\bJiushao",
        r"\bmodulo\b.*\bsystem\b",
    ],
    "fibonacci": [
        r"\bfibonacc", r"\blapin\b|\brabbit",
    ],
    "modern_calculus": [
        r"\bderiv", r"\bintegral", r"\blimit\b|\blim\b", r"\bsérie\b|\bseries\b",
        r"\bconverg", r"\bdiverg", r"\btaylor\b", r"\bfourier\b",
    ],
    "linear_algebra": [
        r"\bmatrice\b|\bmatrix", r"\bd[eé]terminant\b|\bdeterminant",
        r"\bvecteur", r"\bvector", r"\bscalaire", r"\btrace\b",
        r"\bvaleurs.propres|\beigenvalue",
    ],
    "number_theory": [
        r"\bpremi[èe]r\b|\bprime\b", r"\bfactoris", r"\bcrible\b|\bsieve\b",
        r"\bErathost", r"\bEuler\b.*\btotient",
    ],
    "combinatorics": [
        r"\bfactoriel|\bfactorial", r"\bbinomial", r"\bpascal\b",
        r"\bpermut", r"\bcombinat", r"\barrang",
    ],
    "complex_analysis": [
        r"\bcomplexe\b|\bcomplex\b", r"\bnombre.*imag", r"\bplan.complex",
        r"\bArgand\b|\bGauss\b",
    ],
    "group_theory": [
        r"\bgroupe\b|\bgroup\b", r"\bsym[eé]trie\b|\bsymmetry",
        r"\bpermutation\b", r"\bsymétrique\b",
    ],
}


def classify_math_mode(expression: str) -> Dict[str, Any]:
    """Classifie une expression en mathématiques anciennes ou modernes.

    Retourne le type ('ancient', 'modern', 'general') et les domaines spécifiques.
    """
    low = expression.lower()
    scores: Dict[str, float] = {}

    for category, patterns in _MATH_DOMAIN_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, low):
                scores[category] = scores.get(category, 0.0) + 1.0

    # Bonus pour opérateurs modernes
    if re.search(r"\b∫\b|\b∑\b|\b∇\b|\bΔ\b|\b∂\b", low):
        scores["modern_symbolic"] = scores.get("modern_symbolic", 0.0) + 3.0

    # Si a des opérateurs différentiels → moderne
    if re.search(r"\bd[xyzt]/dt\b|\bdy/dx\b|\b∂/∂", low):
        scores["modern_calculus"] = scores.get("modern_calculus", 0.0) + 3.0

    # Classification
    ancient_categories = {"pythagorean", "euclidean", "archimedean",
                          "egyptian_fraction", "babylonian", "golden_ratio",
                          "chinese_remainder", "fibonacci"}
    modern_categories = {"modern_calculus", "linear_algebra", "number_theory",
                         "combinatorics", "complex_analysis", "group_theory"}

    ancient_score = sum(scores.get(c, 0.0) for c in ancient_categories)
    modern_score = sum(scores.get(c, 0.0) for c in modern_categories)

    if ancient_score > modern_score and ancient_score > 0:
        era = "ancient"
    elif modern_score > ancient_score and modern_score > 0:
        era = "modern"
    elif ancient_score > 0 and modern_score > 0:
        era = "both"
    else:
        era = "general"

    return {
        "expression": expression,
        "era": era,
        "ancient_score": round(ancient_score, 3),
        "modern_score": round(modern_score, 3),
        "matching_categories": sorted(
            [(c, s) for c, s in scores.items() if s > 0],
            key=lambda x: -x[1]
        ),
        "category_details": [
            {"category": cat, "description": _MATH_DOMAIN_DESCRIPTIONS.get(cat, "?"),
             "score": scores[cat]}
            for cat in sorted(scores, key=lambda c: -scores[c])
        ] if scores else [],
    }


_MATH_DOMAIN_DESCRIPTIONS = {
    "pythagorean": "Triplets pythagoriciens, nombres figurés, harmonie mathématique",
    "euclidean": "Algorithme d'Euclide, nombres parfaits, Éléments",
    "archimedean": "Approximation de π, méthode d'exhaustion, spirale d'Archimède",
    "egyptian_fraction": "Fractions égyptiennes, Papyrus Rhind, calcul fractionnaire antique",
    "babylonian": "Base 60, système sexagésimal, tablettes babyloniennes",
    "golden_ratio": "Nombre d'or φ, proportion divine, pentagramme pythagoricien",
    "chinese_remainder": "Théorème chinois des restes (Sun Tzu, Qin Jiushao)",
    "fibonacci": "Suite de Fibonacci, nombres de Fibonacci, Liber Abaci",
    "modern_calculus": "Analyse moderne : dérivées, intégrales, séries, convergence",
    "linear_algebra": "Algèbre linéaire : matrices, déterminants, vecteurs",
    "number_theory": "Théorie des nombres : primalité, factorisation, crible",
    "combinatorics": "Combinatoire : factorielle, binomial, permutations",
    "complex_analysis": "Analyse complexe : nombres complexes, plan complexe",
    "group_theory": "Théorie des groupes : symétries, permutations",
}


# =========================================================================
# PARTIE 6 — CLASSE PRINCIPALE
# =========================================================================

class MathValidationWorker:
    """Validateur mathématique déterministe : expert en mathématiques anciennes ET modernes.

    (no LLM, pure Python / sympy optionnel)

    Modes disponibles :
    - parse / evaluate / dimensional / verify  (existants)
    - ancient       : outils de mathématiques anciennes (Pythagore, Euclide, etc.)
    - modern        : outils de mathématiques modernes (calcul, algèbre linéaire, etc.)
    - classify_math : classifie si une expression est ancienne ou moderne
    """

    name = "math_validation"

    def __init__(self, bus=None):
        self.bus = bus
        self._sympy_available = False
        try:
            import sympy  # noqa: F401
            self._sympy_available = True
        except ImportError:
            pass

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Validateur mathématique initialisé. "
                    f"Expertise : mathématiques anciennes (Pythagore, Euclide, "
                    f"Archimède, Égypte, Babylone, Chine) + modernes "
                    f"(calcul, algèbre linéaire, combinatoire, nombres). "
                    f"sympy={self._sympy_available}")
        return True

    def shutdown(self) -> bool:
        return True

    # ── Modes existants (inchangés) ──────────────────────────

    def parse_expression(self, expression: str) -> Dict[str, Any]:
        try:
            tree = ast.parse(expression.strip(), mode="eval")
            return {"valid": True, "ast": tree, "error": None}
        except SyntaxError as e:
            return {"valid": False, "ast": None, "error": str(e)}

    def evaluate(
        self, expression: str, variables: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
        balanced, msg = _check_balance(expression)
        if not balanced:
            return {"valid": False, "result": None, "error": msg}
        try:
            tree = ast.parse(expression.strip(), mode="eval")
            result = _safe_eval(tree, variables or {})
            return {"valid": True, "result": result, "error": None}
        except (SyntaxError, ValueError, ZeroDivisionError) as e:
            return {"valid": False, "result": None, "error": str(e)}

    def verify_formula(
        self, formula_name: str, formula: str, values: Dict[str, float]
    ) -> Dict[str, Any]:
        if "=" not in formula:
            return {"valid": False, "error": "No '=' found in formula"}
        lhs_str, rhs_str = formula.split("=", 1)
        try:
            lhs_tree = ast.parse(lhs_str.strip(), mode="eval")
            rhs_tree = ast.parse(rhs_str.strip(), mode="eval")
            lhs_val = _safe_eval(lhs_tree, values)
            rhs_val = _safe_eval(rhs_tree, values)
            diff = abs(lhs_val - rhs_val)
            match = diff < 1e-9
            return {
                "valid": True, "match": match,
                "lhs": lhs_val, "rhs": rhs_val, "diff": diff,
                "error": None,
            }
        except (SyntaxError, ValueError, ZeroDivisionError) as e:
            return {"valid": False, "error": str(e)}

    def dimensional_analysis(self, expression: str) -> Dict[str, Any]:
        for known_name, dims in _FORMULA_SIGNATURES.items():
            clean_expr = expression.replace(" ", "").lower()
            clean_known = known_name.replace(" ", "").lower()
            if clean_expr == clean_known:
                return {
                    "valid": True,
                    "formula": known_name,
                    "dimensions": dims,
                    "description": f"Dimensions: {dict(dims)}",
                    "error": None,
                }
        vars_in_expr = _extract_variables(expression)
        return {
            "valid": True,
            "formula": expression,
            "variables": vars_in_expr,
            "dimensions": None,
            "description": f"Formula not in known signature table. "
                           f"Variables: {vars_in_expr}",
            "error": None,
        }

    # ── Mode ANCIENT ──────────────────────────────────────────

    def analyze_ancient_math(self, expression: str) -> Dict[str, Any]:
        """Analyse une expression avec les outils de mathématiques anciennes."""
        low = expression.lower().strip()
        results = {}
        sources = []

        # Si contient φ → nombre d'or
        if re.search(r"\bφ\b|\bphi\b|\bgolden", low):
            results["golden_ratio"] = _golden_ratio_properties()
            sources.append("Grèce antique — Pythagore/Euclide (nombre d'or φ)")

        # Si mentionne Pythagore ou triplets
        if re.search(r"\bpythagore|\btriple[ts]?\b", low):
            results["pythagorean_triples"] = {
                "known_triples": _PYTHAGOREAN_TRIPLES,
                "check": {f"({a},{b},{c})": _is_pythagorean_triple(a, b, c)
                          for a, b, c in _PYTHAGOREAN_TRIPLES[:5]},
                "generated": _generate_pythagorean_triples(100),
            }
            results["figurate_numbers"] = _figurate_numbers(100)
            sources.append("École pythagoricienne — triplets et nombres figurés")

        # Euclide
        if re.search(r"\beuclid|\bpgcd\b|\bgcd\b|\bparfait\b", low):
            results["euclidean_gcd"] = _euclidean_gcd(48, 18)  # example
            # Cherche 'n=' dans l'expression pour prendre un nombre
            n_match = re.search(r"n\s*=\s*(\d+)", low)
            n_val = int(n_match.group(1)) if n_match else 28
            results["perfect_numbers_until"] = _perfect_numbers(n_val) if n_val <= 10000 else []
            sources.append("Euclide d'Alexandrie — Éléments, PGCD, nombres parfaits")

        # Archimède
        if re.search(r"\barchim[eè]de|\bπ\b|\bpi\b", low):
            results["archimedes_pi"] = _archimedes_pi(96)
            sources.append("Archimède de Syracuse — approximation de π par polygones")

        # Égypte
        if re.search(r"\bégypti|\b[ée]gypt|\brhind\b|\bfraction", low):
            # Cherche une fraction dans l'expression
            frac_match = re.search(r"(\d+)\s*/\s*(\d+)", low)
            if frac_match:
                num, den = int(frac_match.group(1)), int(frac_match.group(2))
                results["egyptian_fraction"] = _egyptian_fraction(num, den)
            else:
                results["egyptian_fraction"] = _egyptian_fraction(7, 10)  # example
            results["rhind_problems"] = list(_RHIND_PROBLEMS.items())[:3]
            sources.append("Égypte antique — fractions unitaires, Papyrus Rhind")

        # Babylone
        if re.search(r"\bbabylon|\bsexag|\b60\b|\bcuneiform", low):
            n_match = re.search(r"n\s*=\s*(\d+\.?\d*)", low)
            n_val = float(n_match.group(1)) if n_match else 60.0
            results["sexagesimal"] = _decimal_to_sexagesimal(n_val)
            sources.append("Babylone — système sexagésimal (base 60), tablettes mathématiques")

        # Chine
        if re.search(r"\bchinois?|\breste\b|\bmodul", low):
            results["chinese_remainder"] = _chinese_remainder_theorem([3, 5, 7], [2, 3, 2])
            sources.append("Chine ancienne — Sun Tzu, Qin Jiushao, théorème des restes")

        return {
            "valid": True,
            "expression": expression,
            "era": "ancient",
            "results": results,
            "sources": sources,
        }

    # ── Mode MODERN ───────────────────────────────────────────

    def analyze_modern_math(self, expression: str) -> Dict[str, Any]:
        """Analyse une expression avec les outils de mathématiques modernes."""
        low = expression.lower().strip()
        results = {}

        # Fibonacci
        if re.search(r"\bfibonacc", low):
            n_match = re.search(r"n\s*=\s*(\d+)", low)
            n_val = int(n_match.group(1)) if n_match else 20
            results["fibonacci"] = {
                "sequence": _fibonacci(n_val),
                "nth_term": {f"F({n_val})": _fibonacci_n(n_val)},
            }

        # Série arithmétique/géométrique
        if re.search(r"\bsérie\b|\bseries\b|\bsuite\b|\barith|\bgéo", low):
            if re.search(r"\barith", low):
                results["arithmetic_series"] = _arithmetic_series(1, 2, 10)
            if re.search(r"\bgéo", low):
                results["geometric_series"] = _geometric_series(1, 2, 10)
            results["convergence"] = _convergence_check("geometric", r=0.5)

        # Nombres premiers
        if re.search(r"\bpremi[èe]r\b|\bprime\b|\bfactor[\w]*\b", low):
            n_match = re.search(r"n\s*=\s*(\d+)", low)
            n_val = int(n_match.group(1)) if n_match else 100
            results["prime_numbers"] = {
                "primes_until_limit": _primes_until(n_val)[:50],
                "prime_count": len(_primes_until(n_val)),
            }
            if n_val > 1:
                results["prime_factors"] = _prime_factors(n_val)

        # Combinatoire
        if re.search(r"\bfactoriel|\bbinomial|\bpascal|\bpermut|\bcombinat", low):
            n_match = re.search(r"n\s*=\s*(\d+)", low)
            n_val = int(n_match.group(1)) if n_match else 5
            results["combinatorics"] = {
                "factorial": _factorial(n_val),
                "binomial_C_5_2": _binomial(5, 2),
                "pascals_triangle": _pascals_triangle(n_val),
                "permutations_P_5_2": _permutations(5, 2),
            }

        return {
            "valid": True,
            "expression": expression,
            "era": "modern",
            "results": results,
        }

    # ── Mode CLASSIFY_MATH ────────────────────────────────────

    def classify_math(self, expression: str) -> Dict[str, Any]:
        """Classe une expression en mathématiques anciennes ou modernes."""
        base = classify_math_mode(expression)
        # Ajouter des détails contextuels
        if base["era"] == "ancient":
            base["ancient_details"] = self.analyze_ancient_math(expression).get("results", {})
        elif base["era"] == "modern":
            base["modern_details"] = self.analyze_modern_math(expression).get("results", {})
        elif base["era"] == "both":
            base["ancient_details"] = self.analyze_ancient_math(expression).get("results", {})
            base["modern_details"] = self.analyze_modern_math(expression).get("results", {})
        return base

    # ── Plugin bus interface ──────────────────────────────────

    def execute(self, message) -> None:
        from captn.runtime.base import Message
        task_id = message.payload.get("task_id")
        expression = message.payload.get("expression", "")
        mode = message.payload.get("mode", "parse")
        variables = message.payload.get("variables", {})

        logger.info(f"[{self.name}] Processing {mode} for task {task_id}")

        result = None
        error = None

        try:
            if mode == "parse":
                result = self.parse_expression(expression)
            elif mode == "evaluate":
                result = self.evaluate(expression, variables)
            elif mode == "dimensional":
                result = self.dimensional_analysis(expression)
            elif mode == "verify":
                result = self.verify_formula(
                    expression, expression,
                    {k: float(v) for k, v in variables.items()}
                )
            elif mode == "ancient":
                result = self.analyze_ancient_math(expression)
            elif mode == "modern":
                result = self.analyze_modern_math(expression)
            elif mode == "classify_math":
                result = self.classify_math(expression)
            else:
                error = f"Unknown mode: {mode}"
        except Exception as e:
            error = str(e)

        response = Message(
            sender=self.name,
            destination="captn",
            type="response",
            payload={
                "task_id": task_id,
                "current_step_plugin": self.name,
                "valid": error is None and (result is None or result.get("valid", False)),
                "result": result,
                "expression": expression,
                "error_message": error,
            },
        )
        if self.bus:
            self.bus.publish(response)


# ── Standalone CLI helper ────────────────────────────────────
def quick_check(expression: str, **variables) -> Dict[str, Any]:
    """Quick standalone check: evaluate an expression."""
    w = MathValidationWorker()
    return w.evaluate(expression, variables)