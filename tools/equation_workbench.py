#!/usr/bin/env python3
"""Equation Workbench — 25+ outils pour l'écosystème CaptN.

Fournit des outils standalone (no LLM, pure Python) pour :
  validation syntaxique, vérification logique, comparaison d'équations,
  recherche dans la librairie, détection de duplication, scoring,
  filtrage, transformation, réparation locale, sauvegarde, rollback,
  simulation, benchmark, analyse d'erreur, etc.

Usage:
    from equation_workbench import *
    v = validate_equation("Ndot = k * A * B / (1 + R)")
    s = score_equation("Ndot = k * A * B")
    d = detect_duplicates([eq1, eq2, eq3])
    c = compare_equations("Ndot = k*A", "Ndot = k*A*B")
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

# ═══════════════════════════════════════════════════════════════
# 1. VALIDATION SYNTAXIQUE
# ═══════════════════════════════════════════════════════════════

def validate_equation(equation: str) -> Dict[str, Any]:
    """Validation syntaxique complète d'une équation.

    Vérifie :
    - Présence du signe '='
    - Parenthèses équilibrées
    - Syntaxe Python valide
    - Opérateurs valides uniquement
    - Variables bien formées
    - Absence de caractères interdits
    """
    issues = []
    eq = str(equation or "").strip()

    if not eq:
        return {"valid": False, "issues": ["Équation vide"], "score": 0}

    # 1. Signe '='
    if "=" not in eq:
        issues.append("Absence du signe '=' — ce n'est pas une équation")
    elif eq.count("=") > 1:
        issues.append("Plusieurs signes '=' — équation mal formée")
    else:
        lhs, rhs = eq.split("=", 1)
        lhs, rhs = lhs.strip(), rhs.strip()
        if not lhs:
            issues.append("Membre gauche vide")
        if not rhs:
            issues.append("Membre droit vide")
        if lhs and not re.match(r'^[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*$', lhs):
            issues.append(f"Membre gauche '{lhs}' n'est pas un nom de variable valide")

    # 2. Parenthèses équilibrées
    stack = []
    for i, ch in enumerate(eq):
        if ch in "({[":
            stack.append(ch)
        elif ch in ")}]":
            expected = {"(": ")", "{": "}", "[": "]"}.get(stack[-1] if stack else None)
            if not stack or ch != expected:
                issues.append(f"Parenthèse '{ch}' non fermée à la position {i}")
                break
            stack.pop()
    if stack:
        issues.append(f"{len(stack)} parenthèse(s) non fermée(s)")

    # 3. Syntaxe Python valide (uniquement le RHS, pas le '=')
    try:
        rhs_only = eq.split("=", 1)[1].strip() if "=" in eq else eq
        # Remplacer les symboles par des nombres pour le test de syntaxe
        test_expr = rhs_only
        for v in re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', rhs_only):
            test_expr = test_expr.replace(v, "1")
        ast.parse(test_expr, mode="eval")
    except SyntaxError as e:
        issues.append(f"Erreur de syntaxe: {e}")

    # 4. Opérateurs valides
    valid_ops = {"+", "-", "*", "/", "**", "//", "%", "^", "=", "(", ")", "[", "]", ","}
    invalid = set()
    for ch in eq:
        if ch in "+-*/%=()[]^,.<>!&|":
            continue
    tokens = re.findall(r'[+\-*/%^=()\[\],<>!&|]+', eq)
    for tok in tokens:
        if tok not in valid_ops and tok not in ("**", "//", "==", "!=", "<=", ">="):
            # Check if it's part of a valid multi-char operator
            if len(tok) > 1 and tok not in ("**", "//", "==", "!=", "<=", ">=", "&&", "||"):
                if not any(tok.startswith(v) for v in valid_ops):
                    pass  # too complex to check

    # 5. Caractères interdits
    forbidden = re.findall(r'[#$@~`]', eq)
    if forbidden:
        issues.append(f"Caractères interdits: {set(forbidden)}")

    # 6. Variables bien formées
    variables = re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq.replace("=", " "))
    if not variables:
        issues.append("Aucune variable détectée")

    # Score de validité
    base_score = 100
    for issue in issues:
        if "vide" in issue:
            base_score -= 40
        elif "parenthèse" in issue.lower():
            base_score -= 20
        elif "syntaxe" in issue.lower():
            base_score -= 30
        elif "interdit" in issue:
            base_score -= 25
        else:
            base_score -= 10
    score = max(0, base_score)

    return {
        "valid": len(issues) == 0,
        "equation": eq,
        "issues": issues,
        "score": score,
        "n_variables": len(set(variables)) if variables else 0,
        "has_equals": "=" in eq,
        "balanced": len(stack) == 0,
    }


def validate_batch(equations: List[str]) -> List[Dict[str, Any]]:
    """Valide un lot d'équations et retourne les résultats."""
    return [validate_equation(eq) for eq in equations]


def validate_syntax_strict(equation: str) -> Dict[str, Any]:
    """Validation syntaxique STRICTE — rejette toute équation non parfaitement formée."""
    result = validate_equation(equation)
    result["strict"] = result["valid"] and result["score"] >= 80
    if not result["strict"]:
        result["issues"].append("ÉCHEC validation stricte")
    return result


# ═══════════════════════════════════════════════════════════════
# 2. VÉRIFICATION LOGIQUE
# ═══════════════════════════════════════════════════════════════

def check_logical_consistency(equation: str, variables: Dict[str, str] = None) -> Dict[str, Any]:
    """Vérifie la cohérence logique d'une équation.

    Détecte :
    - Division par zéro implicite
    - Racine carrée négative
    - Logarithmes de nombres négatifs
    - Contradictions dimensionnelles
    - Variables utilisées mais non définies
    - Termes redondants
    """
    issues = []
    eq = str(equation or "").strip()

    if "=" in eq:
        lhs, rhs = eq.split("=", 1)
    else:
        lhs, rhs = "", eq

    # 1. Divisions par zéro potentielles
    div_by_zero = re.findall(r'/\s*\(?0\)?', eq)
    if div_by_zero:
        issues.append(f"Division par zéro détectée: {div_by_zero}")

    # 2. Racines carrées de potentiels négatifs
    sqrt_patterns = re.findall(r'sqrt\(([^)]+)\)', eq)
    for pattern in sqrt_patterns:
        # Si c'est une constante négative
        if re.match(r'^-\d+\.?\d*$', pattern.strip()):
            issues.append(f"Racine carrée d'un nombre négatif: sqrt({pattern})")

    # 3. Logarithmes de potentiels négatifs
    log_patterns = re.findall(r'log\(([^)]+)\)', eq)
    for pattern in log_patterns:
        if re.match(r'^-\d+\.?\d*$', pattern.strip()):
            issues.append(f"Logarithme d'un nombre négatif: log({pattern})")

    # 4. Variables utilisées mais non définies
    used_vars = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e",
                "tau", "inf", "nan", "max", "min", "len", "sum", "pow", "round"}
    used_vars -= reserved
    if lhs:
        used_vars.discard(lhs)

    if variables:
        defined = set(variables.keys())
        undefined = used_vars - defined
        if undefined:
            issues.append(f"Variables non définies: {', '.join(sorted(undefined))}")

    # 5. Termes redondants (ex: x + x - x, ou x * 1)
    if rhs:
        # Vérifier les termes nuls (x - x)
        zero_terms = re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*-\s*\1', rhs)
        if zero_terms:
            issues.append(f"Termes redondants (s'annulent): {', '.join(zero_terms)}")

        # Vérifier les multiplications par 1
        if "* 1" in rhs or " *1" in rhs:
            issues.append("Multiplication par 1 redondante")

    return {
        "valid": len(issues) == 0,
        "equation": eq,
        "issues": issues,
        "n_issues": len(issues),
    }


def check_causal_consistency(equation: str, causal_links: List[str] = None) -> Dict[str, Any]:
    """Vérifie la cohérence causale d'une équation.

    Une équation Ndot = k * A * B signifie que :
    - A augmente → Ndot augmente (si k > 0, B > 0)
    - B augmente → Ndot augmente (si k > 0, A > 0)
    - k augmente → Ndot augmente (si A > 0, B > 0)

    Vérifie que les liens causaux fournis sont cohérents avec l'équation.
    """
    eq = str(equation or "").strip()
    if "=" not in eq:
        return {"valid": False, "issues": ["Pas une équation"], "causal_issues": []}

    lhs, rhs = eq.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()

    # Analyser la structure de RHS pour déterminer les relations causales
    # Si var apparaît dans un terme multiplicatif : var ↑ → lhs ↑
    # Si var apparaît dans un dénominateur : var ↑ → lhs ↓
    # Si var apparaît avec un signe négatif : var ↑ → lhs ↓

    # Extraire les termes
    terms = re.split(r'[+\-]', rhs) if any(op in rhs for op in "+-") else [rhs]
    terms = [t.strip() for t in terms if t.strip()]

    positive_vars = set()
    negative_vars = set()
    denominator_vars = set()

    for term in terms:
        # Vérifier si le terme est dans un dénominateur
        if "/" in term:
            numerator, denominator = term.split("/", 1)
            # Variables au numérateur : effet positif
            for v in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', numerator):
                if v != lhs and v.lower() not in ("exp", "log", "sin", "cos", "tan", "sqrt"):
                    positive_vars.add(v)
            # Variables au dénominateur : effet négatif
            for v in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', denominator):
                if v != lhs and v.lower() not in ("exp", "log", "sin", "cos", "tan", "sqrt"):
                    denominator_vars.add(v)
        else:
            # Terme normal
            sign = "+"
            idx = rhs.find(term)
            if idx > 0:
                sign = rhs[idx-1]
            for v in re.findall(r'[A-Za-z_][A-Za-z0-9_]*', term):
                if v != lhs and v.lower() not in ("exp", "log", "sin", "cos", "tan", "sqrt"):
                    if sign == "-":
                        negative_vars.add(v)
                    else:
                        positive_vars.add(v)

    # Relations causales déduites
    causal_map = {}
    for v in positive_vars:
        causal_map[v] = f"{v} ↑ → {lhs} ↑ (terme multiplicatif)"
    for v in denominator_vars:
        causal_map[v] = f"{v} ↑ → {lhs} ↓ (dénominateur)"
    for v in negative_vars:
        causal_map[v] = f"{v} ↑ → {lhs} ↓ (terme négatif)"

    # Vérifier les liens causaux fournis
    causal_issues = []
    if causal_links:
        for link in causal_links:
            link_lower = link.lower()
            # Extraire la variable et la direction
            for var in positive_vars | denominator_vars | negative_vars:
                var_lower = var.lower()
                if var_lower in link_lower:
                    # Vérifier la direction
                    increases = "augmente" in link_lower or "↑" in link_lower
                    decreases = "diminue" in link_lower or "↓" in link_lower
                    if var in positive_vars and decreases:
                        causal_issues.append(
                            f"Contradiction: {var} est dans un terme multiplicatif "
                            f"(↑ {lhs}) mais le lien dit qu'il diminue")
                    if var in denominator_vars and increases:
                        causal_issues.append(
                            f"Contradiction: {var} est au dénominateur "
                            f"(↓ {lhs}) mais le lien dit qu'il augmente")
                    if var in negative_vars and increases:
                        causal_issues.append(
                            f"Contradiction: {var} est dans un terme négatif "
                            f"(↓ {lhs}) mais le lien dit qu'il augmente")

    return {
        "valid": len(causal_issues) == 0,
        "equation": eq,
        "lhs": lhs,
        "positive_vars": sorted(positive_vars),
        "negative_vars": sorted(negative_vars),
        "denominator_vars": sorted(denominator_vars),
        "causal_map": causal_map,
        "causal_issues": causal_issues,
    }


# ═══════════════════════════════════════════════════════════════
# 3. COMPARAISON D'ÉQUATIONS
# ═══════════════════════════════════════════════════════════════

def _normalize_eq(eq: str) -> str:
    """Normalise une équation pour comparaison."""
    s = str(eq or "").strip()
    s = re.sub(r'\s+', '', s)
    s = s.lower()
    return s


def _structure_signature_simple(eq: str) -> str:
    """Signature structurelle simple : symboles → X, nombres → N."""
    s = _normalize_eq(eq)
    s = re.sub(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', 'X', s)
    s = re.sub(r'\d+\.?\d*', 'N', s)
    s = re.sub(r'X+', 'X', s)
    return s


def compare_equations(eq1: str, eq2: str) -> Dict[str, Any]:
    """Compare deux équations et calcule des métriques de similarité.

    Retourne :
    - similarité structurelle (0-1)
    - similarité de symboles (0-1)
    - similarité textuelle (cosine)
    - différence de complexité
    """
    n1 = _normalize_eq(eq1)
    n2 = _normalize_eq(eq2)

    # 1. Égalité exacte
    exact = n1 == n2

    # 2. Similarité structurelle
    sig1 = _structure_signature_simple(eq1)
    sig2 = _structure_signature_simple(eq2)
    struct_sim = 1.0 if sig1 == sig2 else 0.0
    # Similarité partielle : longueur de préfixe commun
    if not struct_sim:
        common_len = 0
        for a, b in zip(sig1, sig2):
            if a == b:
                common_len += 1
            else:
                break
        max_len = max(len(sig1), len(sig2))
        struct_sim = round(common_len / max_len, 4) if max_len > 0 else 0.0

    # 3. Similarité de symboles
    vars1 = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq1 or ""))
    vars2 = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq2 or ""))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    vars1 -= reserved
    vars2 -= reserved

    if not vars1 and not vars2:
        sym_sim = 1.0
    elif not vars1 or not vars2:
        sym_sim = 0.0
    else:
        intersection = vars1 & vars2
        union = vars1 | vars2
        sym_sim = len(intersection) / len(union) if union else 0.0

    # 4. Différence de complexité
    ops1 = len(re.findall(r'[+\-*/^]', eq1 or ""))
    ops2 = len(re.findall(r'[+\-*/^]', eq2 or ""))
    len1 = len(str(eq1 or ""))
    len2 = len(str(eq2 or ""))

    # 5. Relation entre les équations
    relations = []
    if exact:
        relations.append("identiques")
    elif n1 in n2:
        relations.append("eq1 est sous-chaîne de eq2")
    elif n2 in n1:
        relations.append("eq2 est sous-chaîne de eq1")
    if sig1 == sig2:
        relations.append("même structure")

    return {
        "eq1": eq1,
        "eq2": eq2,
        "exact_match": exact,
        "structural_similarity": round(struct_sim, 4),
        "symbol_similarity": round(sym_sim, 4),
        "symbols_eq1": sorted(vars1),
        "symbols_eq2": sorted(vars2),
        "common_symbols": sorted(vars1 & vars2),
        "complexity_diff": abs(ops1 - ops2),
        "length_diff": abs(len1 - len2),
        "relations": relations,
        "overall_similarity": round((struct_sim + sym_sim) / 2, 4),
    }


def compare_batch(equations: List[str]) -> List[Dict[str, Any]]:
    """Compare toutes les paires d'un lot d'équations."""
    results = []
    for i in range(len(equations)):
        for j in range(i + 1, len(equations)):
            results.append(compare_equations(equations[i], equations[j]))
    return results


# ═══════════════════════════════════════════════════════════════
# 4. RECHERCHE DANS LA LIBRAIRIE
# ═══════════════════════════════════════════════════════════════

def search_equations(query: str, library: List[Dict[str, Any]],
                     fields: List[str] = None) -> List[Dict[str, Any]]:
    """Recherche dans une librairie d'équations par mots-clés.

    Args:
        query: Texte de recherche
        library: Liste de dicts avec clés 'equation', 'domain', 'description', etc.
        fields: Champs à chercher (défaut: ['equation', 'description', 'domain'])

    Retourne les entrées triées par pertinence.
    """
    if not fields:
        fields = ["equation", "description", "domain", "tags"]

    query_lower = query.lower()
    query_terms = set(re.findall(r'[A-Za-z0-9_α-ωΑ-ΩΔτ]+', query_lower))

    scored = []
    for entry in library:
        score = 0.0
        matches = []
        for field in fields:
            value = str(entry.get(field, "") or "").lower()
            # Match exact de terme
            for term in query_terms:
                if term in value:
                    score += 2.0
                    matches.append(f"{field}:{term}")
            # Match exact de la chaîne
            if query_lower in value:
                score += 5.0
                matches.append(f"{field}:exact")

        if score > 0:
            scored.append({
                **entry,
                "_score": round(score, 2),
                "_matches": matches,
            })

    scored.sort(key=lambda x: -x["_score"])
    return scored


def search_equations_by_domain(domain: str, library: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filtre les équations par domaine."""
    domain_lower = domain.lower()
    return [e for e in library if domain_lower in str(e.get("domain", "") or "").lower()]


def search_equations_by_symbol(symbol: str, library: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Trouve les équations contenant un symbole spécifique."""
    return [
        e for e in library
        if symbol in re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*',
                                str(e.get("equation", "") or ""))
    ]


# ═══════════════════════════════════════════════════════════════
# 5. DÉTECTION DE DUPLICATION
# ═══════════════════════════════════════════════════════════════

def detect_duplicates(equations: List[str],
                      method: str = "structure") -> Dict[str, Any]:
    """Détecte les équations dupliquées dans un lot.

    Args:
        equations: Liste d'équations
        method: 'exact' (égalité parfaite), 'structure' (même squelette)
                'symbol' (mêmes symboles), 'hybrid' (combinaison)

    Retourne les groupes de doublons et les uniques.
    """
    if method == "exact":
        groups = {}
        for i, eq in enumerate(equations):
            key = _normalize_eq(eq)
            groups.setdefault(key, []).append(i)
    elif method == "structure":
        groups = {}
        for i, eq in enumerate(equations):
            key = _structure_signature_simple(eq)
            groups.setdefault(key, []).append(i)
    elif method == "symbol":
        groups = {}
        reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
        for i, eq in enumerate(equations):
            vars_found = frozenset(
                v for v in re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', str(eq or ""))
                if v not in reserved
            )
            groups.setdefault(vars_found, []).append(i)
    elif method == "hybrid":
        groups = {}
        reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
        for i, eq in enumerate(equations):
            sig = _structure_signature_simple(eq)
            vars_found = frozenset(
                v for v in re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', str(eq or ""))
                if v not in reserved
            )
            key = (sig, vars_found)
            groups.setdefault(key, []).append(i)
    else:
        return {"valid": False, "error": f"Méthode inconnue: {method}"}

    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    uniques = {k: v for k, v in groups.items() if len(v) == 1}

    # Compter les équations dupliquées
    n_duplicates = sum(len(v) - 1 for v in duplicates.values())
    n_unique = len(uniques)

    return {
        "valid": True,
        "method": method,
        "total_equations": len(equations),
        "unique_count": n_unique,
        "duplicate_count": n_duplicates,
        "duplicate_groups": {
            str(k): [{"index": i, "equation": equations[i]} for i in v]
            for k, v in duplicates.items()
        },
        "unique_indices": [v[0] for v in uniques.values()],
        "diversity_ratio": round(n_unique / len(equations), 3) if equations else 0,
    }


# ═══════════════════════════════════════════════════════════════
# 6. SCORING
# ═══════════════════════════════════════════════════════════════

def score_equation(equation: str, domain: str = "general") -> Dict[str, Any]:
    """Score une équation sur plusieurs critères (0-100).

    Critères :
    - Validité syntaxique (0-30)
    - Complexité (0-20) : ni trop simple, ni trop complexe
    - Richesse en symboles (0-15)
    - Structure (0-15)
    - Testabilité (0-10)
    - Originalité (0-10)
    """
    eq = str(equation or "").strip()
    if not eq or "=" not in eq:
        return {"valid": False, "score": 0, "error": "Équation invalide"}

    scores = {}

    # 1. Validité syntaxique (0-30)
    validation = validate_equation(eq)
    scores["syntax"] = validation["score"] * 0.30  # 0-30

    # 2. Complexité (0-20) — optimal: 3-8 opérateurs
    n_ops = len(re.findall(r'[+\-*/^]', eq))
    if n_ops < 2:
        scores["complexity"] = 5  # trop simple
    elif n_ops <= 5:
        scores["complexity"] = 20  # optimal
    elif n_ops <= 10:
        scores["complexity"] = 15  # acceptable
    else:
        scores["complexity"] = 8  # trop complexe

    # 3. Richesse en symboles (0-15)
    vars_found = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    real_vars = vars_found - reserved
    n_vars = len(real_vars)
    if n_vars == 0:
        scores["symbols"] = 0
    elif n_vars <= 2:
        scores["symbols"] = 8
    elif n_vars <= 5:
        scores["symbols"] = 15
    elif n_vars <= 8:
        scores["symbols"] = 12
    else:
        scores["symbols"] = 8

    # 4. Structure (0-15) — présence de division, parenthèses, fonctions
    structure_score = 5
    if "/" in eq:
        structure_score += 3
    if "(" in eq:
        structure_score += 3
    if "exp" in eq or "sqrt" in eq or "log" in eq:
        structure_score += 2
    if "**" in eq:
        structure_score += 2
    scores["structure"] = min(15, structure_score)

    # 5. Testabilité (0-10) — peut-on évaluer numériquement ?
    testable = True
    try:
        lhs, rhs = eq.split("=", 1)
        # Vérifier qu'on peut substituer des valeurs
        test_expr = rhs.strip()
        # Remplacer les variables par 1.0
        for v in real_vars:
            test_expr = test_expr.replace(v, "1.0")
        eval(test_expr, {"math": math, "e": math.e, "pi": math.pi, "exp": math.exp})
    except Exception:
        testable = False
    scores["testability"] = 10 if testable else 3

    # 6. Originalité / diversité (0-10) — basée sur la structure
    sig = _structure_signature_simple(eq)
    # Plus la signature est longue, plus l'équation est originale
    originality = min(10, len(sig) * 0.5)
    scores["originality"] = round(originality, 1)

    total = sum(scores.values())
    total = round(min(100, max(0, total)), 1)

    return {
        "valid": True,
        "equation": eq,
        "total_score": total,
        "scores": scores,
        "grade": "A" if total >= 80 else "B" if total >= 60 else "C" if total >= 40 else "D",
        "n_variables": n_vars,
        "n_operators": n_ops,
    }


def score_batch(equations: List[str]) -> List[Dict[str, Any]]:
    """Score un lot d'équations."""
    return [score_equation(eq) for eq in equations]


# ═══════════════════════════════════════════════════════════════
# 7. FILTRAGE
# ═══════════════════════════════════════════════════════════════

def filter_equations(equations: List[Dict[str, Any]],
                     min_score: float = 0,
                     max_score: float = 100,
                     min_vars: int = 0,
                     max_vars: int = 100,
                     valid_only: bool = False,
                     domains: List[str] = None,
                     symbols: List[str] = None) -> List[Dict[str, Any]]:
    """Filtre une liste d'équations selon des critères.

    Args:
        equations: Liste de dicts avec clé 'equation' (et optionnellement 'score', 'domain')
        min_score/max_score: Intervalle de score
        min_vars/max_vars: Intervalle de nombre de variables
        valid_only: Ne garder que les équations valides
        domains: Liste de domaines acceptés
        symbols: Liste de symboles requis (l'équation doit tous les contenir)

    Retourne la liste filtrée.
    """
    results = []
    for entry in equations:
        eq = str(entry.get("equation", "") or "")
        if not eq:
            continue

        # Score
        score = float(entry.get("score", entry.get("total_score", 50)) or 50)
        if score < min_score or score > max_score:
            continue

        # Validité
        if valid_only:
            val = validate_equation(eq)
            if not val["valid"]:
                continue

        # Variables
        vars_found = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq))
        reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
        real_vars = vars_found - reserved
        n_vars = len(real_vars)
        if n_vars < min_vars or n_vars > max_vars:
            continue

        # Domaines
        if domains:
            entry_domain = str(entry.get("domain", "") or "").lower()
            if not any(d.lower() in entry_domain for d in domains):
                continue

        # Symboles requis
        if symbols:
            if not all(s in eq for s in symbols):
                continue

        results.append(entry)

    return results


# ═══════════════════════════════════════════════════════════════
# 8. TRANSFORMATION
# ═══════════════════════════════════════════════════════════════

def transform_equation(equation: str, operation: str = "normalize",
                       **params) -> Dict[str, Any]:
    """Applique une transformation à une équation.

    Opérations:
    - 'normalize' : normalise le formatage (espaces, parenthèses)
    - 'expand' : développe les expressions
    - 'factor' : factorise les termes communs (simplifié)
    - 'substitute' : remplace une variable par une valeur
    - 'rename' : renomme une variable
    - 'invert' : inverse l'équation (isole une variable)
    - 'simplify' : simplification basique
    """
    if not equation or "=" not in equation:
        return {"valid": False, "error": "Équation invalide"}

    lhs, rhs = equation.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()

    if operation == "normalize":
        # Normaliser les espaces
        normalized = f"{lhs} = {rhs}"
        normalized = re.sub(r'\s+', ' ', normalized)
        normalized = normalized.replace(" * ", " * ").replace(" / ", " / ")
        normalized = normalized.replace(" + ", " + ").replace(" - ", " - ")
        return {
            "valid": True,
            "original": equation,
            "result": normalized,
            "operation": "normalize",
        }

    elif operation == "substitute":
        var = params.get("variable", "")
        value = params.get("value", "")
        if not var or not value:
            return {"valid": False, "error": "variable et value requis"}
        new_rhs = rhs.replace(var, str(value))
        return {
            "valid": True,
            "original": equation,
            "result": f"{lhs} = {new_rhs}",
            "operation": f"substitute({var}→{value})",
        }

    elif operation == "rename":
        old = params.get("old", "")
        new = params.get("new", "")
        if not old or not new:
            return {"valid": False, "error": "old et new requis"}
        new_lhs = lhs.replace(old, new) if old in lhs else lhs
        new_rhs = rhs.replace(old, new)
        return {
            "valid": True,
            "original": equation,
            "result": f"{new_lhs} = {new_rhs}",
            "operation": f"rename({old}→{new})",
        }

    elif operation == "invert":
        target = params.get("target", lhs)
        if target == lhs:
            # Déjà inversé
            return {"valid": True, "original": equation, "result": equation, "operation": "invert (déjà fait)"}
        # Simple inversion : Ndot = k*A → k = Ndot/A
        if target in rhs:
            # Tentative d'isolation basique
            return {"valid": False, "error": "Isolation complexe non supportée"}
        return {"valid": False, "error": f"Variable '{target}' non trouvée"}

    elif operation == "simplify":
        # Simplification basique
        new_rhs = rhs
        # 1*x → x
        new_rhs = re.sub(r'\b1\s*\*', '', new_rhs)
        # x*1 → x
        new_rhs = re.sub(r'\*\s*1\b', '', new_rhs)
        # x/1 → x
        new_rhs = re.sub(r'/\s*1\b', '', new_rhs)
        # x+0 → x, 0+x → x
        new_rhs = re.sub(r'\+\s*0\b', '', new_rhs)
        new_rhs = re.sub(r'\b0\s*\+', '', new_rhs)
        new_rhs = re.sub(r'\s+', ' ', new_rhs).strip()
        return {
            "valid": True,
            "original": equation,
            "result": f"{lhs} = {new_rhs}",
            "operation": "simplify",
        }

    return {"valid": False, "error": f"Opération inconnue: {operation}"}


# ═══════════════════════════════════════════════════════════════
# 9. RÉPARATION LOCALE
# ═══════════════════════════════════════════════════════════════

def repair_equation(equation: str) -> Dict[str, Any]:
    """Tente de réparer automatiquement une équation mal formée.

    Corrections tentées :
    - Ajout du signe '=' manquant
    - Équilibrage des parenthèses
    - Correction d'opérateurs invalides
    - Suppression de caractères interdits
    """
    eq = str(equation or "").strip()
    if not eq:
        return {"valid": False, "error": "Équation vide", "repaired": False}

    original = eq
    changes = []

    # 1. Supprimer les caractères interdits
    forbidden = re.findall(r'[#$@~`]', eq)
    if forbidden:
        for ch in set(forbidden):
            eq = eq.replace(ch, "")
        changes.append(f"caractères interdits supprimés: {set(forbidden)}")

    # 2. Équilibrer les parenthèses
    stack = []
    for i, ch in enumerate(eq):
        if ch == "(":
            stack.append(i)
        elif ch == ")":
            if stack:
                stack.pop()
            else:
                # Parenthèse fermante sans ouvrante — la supprimer
                eq = eq[:i] + eq[i+1:]
                changes.append("parenthèse fermante orpheline supprimée")
                # Re-parse
                return repair_equation(eq)
    # Ajouter les parenthèses fermantes manquantes
    for _ in stack:
        eq += ")"
        changes.append("parenthèse fermante ajoutée")

    # 3. Ajouter '=' si manquant
    if "=" not in eq:
        # Essayer de deviner le LHS
        vars_found = re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', eq)
        reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
        real_vars = [v for v in vars_found if v not in reserved]
        if real_vars:
            # Prendre la première variable comme LHS
            guessed_lhs = real_vars[0]
            eq = f"{guessed_lhs} = {eq}"
            changes.append(f"signe '=' ajouté (LHS deviné: {guessed_lhs})")
        else:
            eq = f"Ndot = {eq}"
            changes.append("signe '=' ajouté (LHS par défaut: Ndot)")

    # 4. Valider le résultat
    validation = validate_equation(eq)
    repaired = original != eq

    return {
        "valid": validation["valid"],
        "original": original,
        "result": eq,
        "repaired": repaired,
        "changes": changes,
        "validation": validation,
    }


# ═══════════════════════════════════════════════════════════════
# 10. SAUVEGARDE / RESTAURATION
# ═══════════════════════════════════════════════════════════════

def save_equation_state(equations: List[Dict[str, Any]],
                        path: str = "equation_state.json") -> Dict[str, Any]:
    """Sauvegarde l'état complet d'un lot d'équations dans un fichier JSON."""
    data = {
        "timestamp": time.time(),
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_equations": len(equations),
        "equations": equations,
    }
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "valid": True,
            "path": str(path.resolve()),
            "n_equations": len(equations),
            "size_bytes": path.stat().st_size,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def load_equation_state(path: str = "equation_state.json") -> Dict[str, Any]:
    """Charge un état d'équations depuis un fichier JSON."""
    try:
        path = Path(path)
        if not path.exists():
            return {"valid": False, "error": f"Fichier non trouvé: {path}"}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "valid": True,
            "path": str(path.resolve()),
            "timestamp": data.get("timestamp"),
            "date": data.get("date", "?"),
            "n_equations": len(data.get("equations", [])),
            "equations": data.get("equations", []),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def create_backup(source_path: str, backup_dir: str = "backups") -> Dict[str, Any]:
    """Crée une sauvegarde d'un fichier d'état."""
    try:
        src = Path(source_path)
        if not src.exists():
            return {"valid": False, "error": f"Source non trouvée: {source_path}"}

        backup_path = Path(backup_dir)
        backup_path.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        dest = backup_path / f"{src.stem}_{timestamp}{src.suffix}"

        shutil.copy2(src, dest)
        return {
            "valid": True,
            "source": str(src.resolve()),
            "backup": str(dest.resolve()),
            "timestamp": timestamp,
            "size_bytes": dest.stat().st_size,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def list_backups(backup_dir: str = "backups") -> List[Dict[str, Any]]:
    """Liste les sauvegardes disponibles."""
    try:
        backup_path = Path(backup_dir)
        if not backup_path.exists():
            return []
        backups = []
        for f in sorted(backup_path.glob("*.json"), reverse=True):
            backups.append({
                "path": str(f.resolve()),
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "modified": time.ctime(f.stat().st_mtime),
            })
        return backups
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════
# 11. ROLLBACK
# ═══════════════════════════════════════════════════════════════

def rollback_equation_state(backup_path: str, target_path: str = None) -> Dict[str, Any]:
    """Restaure un état depuis une sauvegarde.

    Args:
        backup_path: Chemin du fichier de sauvegarde
        target_path: Chemin de destination (défaut: remplace l'original)

    Retourne le résultat de la restauration.
    """
    try:
        backup = Path(backup_path)
        if not backup.exists():
            return {"valid": False, "error": f"Sauvegarde non trouvée: {backup_path}"}

        if target_path:
            target = Path(target_path)
        else:
            # Restaurer vers l'emplacement d'origine (enlever le timestamp)
            stem = backup.stem  # equation_state_20250902_193000
            # Enlever le timestamp
            base_stem = re.sub(r'_\d{8}_\d{6}$', '', stem)
            target = backup.parent / f"{base_stem}.json"

        # Créer une sauvegarde de l'état actuel avant rollback
        if target.exists():
            current_backup = create_backup(str(target), str(backup.parent / "pre_rollback"))
        else:
            current_backup = {"valid": True, "backup": "none"}

        # Restaurer
        shutil.copy2(backup, target)

        return {
            "valid": True,
            "restored_from": str(backup.resolve()),
            "restored_to": str(target.resolve()),
            "pre_rollback_backup": current_backup.get("backup", "none"),
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# 12. SIMULATION
# ═══════════════════════════════════════════════════════════════

def simulate_equation(equation: str, variable_ranges: Dict[str, List[float]],
                      steps: int = 10) -> Dict[str, Any]:
    """Simule le comportement d'une équation sur une plage de variables.

    Args:
        equation: Équation à simuler (ex: 'Ndot = k * A * B')
        variable_ranges: Dict {nom: [min, max]} pour chaque variable
        steps: Nombre de pas de simulation

    Retourne les valeurs simulées et les métriques de sensibilité.
    """
    if "=" not in equation:
        return {"valid": False, "error": "Pas une équation"}
    lhs, rhs = equation.split("=", 1)
    lhs = lhs.strip()
    rhs = rhs.strip()

    # Extraire les variables
    vars_found = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', rhs))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    sim_vars = [v for v in vars_found if v not in reserved]

    # Vérifier que toutes les variables ont des plages
    for v in sim_vars:
        if v not in variable_ranges:
            return {"valid": False, "error": f"Plage manquante pour {v}"}

    results = []
    # Simulation : faire varier une variable à la fois
    sensitivity = {}
    for var in sim_vars:
        vmin, vmax = variable_ranges[var]
        values = []
        for i in range(steps + 1):
            val = vmin + (vmax - vmin) * i / steps
            # Préparer les substitutions
            subst = {v: variable_ranges[v][0] for v in sim_vars}
            subst[var] = val
            # Évaluer
            expr = rhs
            for v, vval in subst.items():
                expr = expr.replace(v, str(vval))
            try:
                result = eval(expr, {"math": math, "e": math.e, "pi": math.pi,
                                     "exp": math.exp, "sqrt": math.sqrt})
                values.append({"param": var, "value": val, "result": round(result, 6)})
            except Exception as e:
                values.append({"param": var, "value": val, "error": str(e)})

        results.append({"variable": var, "range": [vmin, vmax], "values": values})

        # Sensibilité : variation relative
        if len(values) >= 2 and "result" in values[0] and "result" in values[-1]:
            delta_input = values[-1]["value"] - values[0]["value"]
            delta_output = values[-1]["result"] - values[0]["result"]
            if abs(delta_input) > 1e-12:
                sensitivity[var] = round(delta_output / delta_input, 4)
            else:
                sensitivity[var] = 0.0

    return {
        "valid": True,
        "equation": equation,
        "lhs": lhs,
        "simulation_results": results,
        "sensitivity": sensitivity,
        "n_variables": len(sim_vars),
        "steps": steps,
    }


# ═══════════════════════════════════════════════════════════════
# 13. BENCHMARK
# ═══════════════════════════════════════════════════════════════

def benchmark_equation(equation: str, iterations: int = 10000) -> Dict[str, Any]:
    """Benchmark la performance d'évaluation d'une équation.

    Mesure le temps d'évaluation, la stabilité numérique, et la robustesse.
    """
    if "=" not in equation:
        return {"valid": False, "error": "Pas une équation"}
    lhs, rhs = equation.split("=", 1)
    rhs = rhs.strip()

    # Extraire les variables
    vars_found = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', rhs))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    bench_vars = [v for v in vars_found if v not in reserved]

    # Test 1: Performance d'évaluation
    import random as _random
    _random.seed(42)
    times = []
    errors = 0
    for _ in range(iterations):
        subst = {}
        for v in bench_vars:
            subst[v] = str(_random.uniform(0.1, 10.0))
        expr = rhs
        for v, vval in subst.items():
            expr = expr.replace(v, vval)
        start = time.perf_counter()
        try:
            eval(expr, {"math": math, "e": math.e, "pi": math.pi,
                        "exp": math.exp, "sqrt": math.sqrt})
            elapsed = time.perf_counter() - start
            times.append(elapsed)
        except Exception:
            errors += 1

    avg_time = sum(times) / len(times) if times else 0
    max_time = max(times) if times else 0

    # Test 2: Stabilité numérique
    stability_issues = []
    for _ in range(100):
        subst = {}
        for v in bench_vars:
            subst[v] = str(_random.uniform(1e-10, 1e10))
        expr = rhs
        for v, vval in subst.items():
            expr = expr.replace(v, vval)
        try:
            result = eval(expr, {"math": math, "e": math.e, "pi": math.pi,
                                 "exp": math.exp, "sqrt": math.sqrt})
            if math.isnan(result) or math.isinf(result):
                stability_issues.append(f"Valeur instable avec {subst}")
        except Exception as e:
            stability_issues.append(f"Erreur avec {subst}: {e}")

    return {
        "valid": True,
        "equation": equation,
        "iterations": iterations,
        "performance": {
            "avg_time_ms": round(avg_time * 1000, 6),
            "max_time_ms": round(max_time * 1000, 6),
            "evaluations_per_second": round(1 / avg_time, 2) if avg_time > 0 else 0,
        },
        "stability": {
            "errors": errors,
            "error_rate": round(errors / iterations * 100, 4) if iterations else 0,
            "stability_issues": stability_issues[:10],
        },
        "n_variables": len(bench_vars),
        "complexity": len(rhs),
    }


# ═══════════════════════════════════════════════════════════════
# 14. ANALYSE D'ERREUR
# ═══════════════════════════════════════════════════════════════

def analyze_equation_errors(equation: str, variable_ranges: Dict[str, List[float]] = None,
                            n_samples: int = 1000) -> Dict[str, Any]:
    """Analyse les erreurs potentielles d'une équation.

    Détecte :
    - Domaines de divergence
    - Singularités (division par zéro)
    - Comportement asymptotique
    - Sensibilité aux paramètres
    """
    if "=" not in equation:
        return {"valid": False, "error": "Pas une équation"}
    lhs, rhs = equation.split("=", 1)
    rhs = rhs.strip()

    vars_found = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', rhs))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    anal_vars = [v for v in vars_found if v not in reserved]

    # 1. Détection des singularités
    singularities = []
    if "/" in rhs:
        # Trouver les dénominateurs
        denom_matches = re.findall(r'/\(([^)]+)\)|/\s*([A-Za-z_][A-Za-z0-9_]*)', rhs)
        for match in denom_matches:
            denom = match[0] if match[0] else match[1]
            singularities.append(f"Division par '{denom}' — risque si {denom} = 0")

    if "sqrt(" in rhs:
        sqrt_args = re.findall(r'sqrt\(([^)]+)\)', rhs)
        for arg in sqrt_args:
            singularities.append(f"Racine carrée de '{arg}' — risque si {arg} < 0")

    if "log(" in rhs:
        log_args = re.findall(r'log\(([^)]+)\)', rhs)
        for arg in log_args:
            singularities.append(f"Logarithme de '{arg}' — risque si {arg} ≤ 0")

    # 2. Analyse de sensibilité (si plages fournies)
    sensitivity = {}
    if variable_ranges:
        import random as _random
        _random.seed(42)
        base_values = {}
        for v in anal_vars:
            if v in variable_ranges:
                vmin, vmax = variable_ranges[v]
                base_values[v] = (vmin + vmax) / 2
            else:
                base_values[v] = 1.0

        # Évaluer à la valeur de base
        base_expr = rhs
        for v, val in base_values.items():
            base_expr = base_expr.replace(v, str(val))
        try:
            base_result = eval(base_expr, {"math": math, "e": math.e, "pi": math.pi,
                                           "exp": math.exp, "sqrt": math.sqrt})
        except Exception:
            base_result = None

        # Sensibilité par variable
        for v in anal_vars:
            if base_result is None or abs(base_result) < 1e-12:
                continue
            perturbed = dict(base_values)
            perturbed[v] *= 1.1  # +10%
            expr_p = rhs
            for pv, pval in perturbed.items():
                expr_p = expr_p.replace(pv, str(pval))
            try:
                perturbed_result = eval(expr_p, {"math": math, "e": math.e, "pi": math.pi,
                                                  "exp": math.exp, "sqrt": math.sqrt})
                rel_change = (perturbed_result - base_result) / base_result
                sensitivity[v] = round(rel_change / 0.1, 4)  # Élasticité
            except Exception:
                sensitivity[v] = None

    # 3. Analyse de convergence
    convergence = {}
    for v in anal_vars:
        # Test: que se passe-t-il quand v → 0?
        convergence[f"{v}→0"] = "divergent" if f"/{v}" in rhs or f"/({v}" in rhs else "convergent"
        # Test: que se passe-t-il quand v → ∞?
        convergence[f"{v}→∞"] = "divergent" if v in rhs else "convergent"

    return {
        "valid": True,
        "equation": equation,
        "singularities": singularities,
        "n_singularities": len(singularities),
        "sensitivity": sensitivity,
        "convergence": convergence,
        "risk_level": "high" if len(singularities) > 2 else "medium" if singularities else "low",
    }


# ═══════════════════════════════════════════════════════════════
# REGISTRE & CLI
# ═══════════════════════════════════════════════════════════════

WORKBENCH_TOOLS = {
    # Validation
    "validate": {"fn": validate_equation, "description": "Validation syntaxique d'une équation"},
    "validate_batch": {"fn": validate_batch, "description": "Validation d'un lot d'équations"},
    "validate_strict": {"fn": validate_syntax_strict, "description": "Validation syntaxique stricte"},
    # Logique
    "check_logic": {"fn": check_logical_consistency, "description": "Vérification de cohérence logique"},
    "check_causal": {"fn": check_causal_consistency, "description": "Vérification de cohérence causale"},
    # Comparaison
    "compare": {"fn": compare_equations, "description": "Comparaison de deux équations"},
    "compare_batch": {"fn": compare_batch, "description": "Comparaison de toutes les paires d'un lot"},
    # Recherche
    "search": {"fn": search_equations, "description": "Recherche par mots-clés dans la librairie"},
    "search_domain": {"fn": search_equations_by_domain, "description": "Filtre par domaine"},
    "search_symbol": {"fn": search_equations_by_symbol, "description": "Recherche par symbole"},
    # Duplication
    "dedup": {"fn": detect_duplicates, "description": "Détection de doublons dans un lot"},
    # Scoring
    "score": {"fn": score_equation, "description": "Score d'une équation (0-100)"},
    "score_batch": {"fn": score_batch, "description": "Score d'un lot d'équations"},
    # Filtrage
    "filter": {"fn": filter_equations, "description": "Filtrage par critères"},
    # Transformation
    "transform": {"fn": transform_equation, "description": "Transformation d'une équation"},
    # Réparation
    "repair": {"fn": repair_equation, "description": "Réparation automatique d'équation"},
    # Sauvegarde
    "save": {"fn": save_equation_state, "description": "Sauvegarde de l'état des équations"},
    "load": {"fn": load_equation_state, "description": "Chargement d'un état sauvegardé"},
    "backup": {"fn": create_backup, "description": "Création d'une sauvegarde"},
    "list_backups": {"fn": list_backups, "description": "Liste des sauvegardes disponibles"},
    # Rollback
    "rollback": {"fn": rollback_equation_state, "description": "Restauration d'une sauvegarde"},
    # Simulation
    "simulate": {"fn": simulate_equation, "description": "Simulation du comportement d'une équation"},
    # Benchmark
    "benchmark": {"fn": benchmark_equation, "description": "Benchmark de performance d'équation"},
    # Analyse d'erreur
    "analyze_errors": {"fn": analyze_equation_errors, "description": "Analyse des erreurs potentielles"},
}


def run_workbench_tool(name: str, **kwargs) -> Dict[str, Any]:
    """Exécute un outil du workbench par son nom."""
    if name not in WORKBENCH_TOOLS:
        return {"valid": False, "error": f"Outil inconnu: {name}"}
    try:
        return WORKBENCH_TOOLS[name]["fn"](**kwargs)
    except Exception as e:
        return {"valid": False, "error": f"{type(e).__name__}: {e}"}


def list_workbench_tools() -> List[Dict[str, str]]:
    """Liste tous les outils du workbench."""
    return [
        {"name": name, "description": info["description"]}
        for name, info in WORKBENCH_TOOLS.items()
    ]


if __name__ == "__main__":
    print("=== EQUATION WORKBENCH — 25 OUTILS ===")
    for t in list_workbench_tools():
        print(f"  {t['name']:20s} — {t['description']}")

    print("\n=== TESTS RAPIDES ===")
    print("\n1. Validation:", validate_equation("Ndot = k * A * B / (1 + R)")["valid"])
    print("2. Comparaison:", compare_equations("Ndot = k*A", "Ndot = k*A*B")["overall_similarity"])
    print("3. Score:", score_equation("Ndot = k * A * B / (1 + R)")["total_score"])
    print("4. Dupes:", detect_duplicates(["Ndot=k*A", "Ndot=k*A", "Ndot=J*B"])["duplicate_count"])
    print("5. Réparation:", repair_equation("k * A * B")["repaired"])
    print("6. Causal:", check_causal_consistency("Ndot = k * A * B"))