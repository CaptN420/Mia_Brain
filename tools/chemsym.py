#!/usr/bin/env python3
"""
chemsym.py — Outils de chimie symbolique.

Parsing de formules, balancement d'équations, rendements, masse molaire, stoechiométrie.
Déterministe pur (règles + sympy optionnel pour les systèmes linéaires).

Usage:
    python -m tools.chemsym molar-mass "H2O"
    python -m tools.chemsym balance "H2 + O2 = H2O"
    python -m tools.chemsym balance "C6H12O6 + O2 = CO2 + H2O"
    python -m tools.chemsym yield "C6H12O6" --product "CO2" --mass 180
    python -m tools.chemsym parse "Fe2(SO4)3"
    python -m tools.chemsym formula "Sodium chloride"
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ─── Périodique (masse molaire) ───

ELEMENTS = {
    "H": 1.008, "He": 4.0026, "Li": 6.94, "Be": 9.0122, "B": 10.81,
    "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180,
    "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974,
    "S": 32.06, "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078,
    "Sc": 44.956, "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938,
    "Fe": 55.845, "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38,
    "Ga": 69.723, "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904,
    "Kr": 83.798, "Rb": 85.468, "Sr": 87.62, "Y": 88.906, "Zr": 91.224,
    "Nb": 92.906, "Mo": 95.95, "Tc": 98.0, "Ru": 101.07, "Rh": 102.91,
    "Pd": 106.42, "Ag": 107.87, "Cd": 112.41, "In": 114.82, "Sn": 118.71,
    "Sb": 121.76, "Te": 127.60, "I": 126.90, "Xe": 131.29, "Cs": 132.91,
    "Ba": 137.33, "La": 138.91, "Ce": 140.12, "Pr": 140.91, "Nd": 144.24,
    "Pm": 145.0, "Sm": 150.36, "Eu": 151.96, "Gd": 157.25, "Tb": 158.93,
    "Dy": 162.50, "Ho": 164.93, "Er": 167.26, "Tm": 168.93, "Yb": 173.05,
    "Lu": 174.97, "Hf": 178.49, "Ta": 180.95, "W": 183.84, "Re": 186.21,
    "Os": 190.23, "Ir": 192.22, "Pt": 195.08, "Au": 196.97, "Hg": 200.59,
    "Tl": 204.38, "Pb": 207.2, "Bi": 208.98, "Po": 209.0, "At": 210.0,
    "Rn": 222.0, "Fr": 223.0, "Ra": 226.0, "Ac": 227.0, "Th": 232.04,
    "Pa": 231.04, "U": 238.03, "Np": 237.0, "Pu": 244.0, "Am": 243.0,
    "Cm": 247.0, "Bk": 247.0, "Cf": 251.0, "Es": 252.0, "Fm": 257.0,
    "Md": 258.0, "No": 259.0, "Lr": 262.0,
}

# Noms communs -> formule
COMMON_NAMES = {
    "sodium chloride": "NaCl",
    "table salt": "NaCl",
    "water": "H2O",
    "carbon dioxide": "CO2",
    "methane": "CH4",
    "ethanol": "C2H5OH",
    "glucose": "C6H12O6",
    "sulfuric acid": "H2SO4",
    "nitric acid": "HNO3",
    "hydrochloric acid": "HCl",
    "ammonia": "NH3",
    "sodium hydroxide": "NaOH",
    "calcium carbonate": "CaCO3",
    "potassium permanganate": "KMnO4",
    "hydrogen peroxide": "H2O2",
    "oxygen": "O2",
    "hydrogen": "H2",
    "nitrogen": "N2",
    "chlorine": "Cl2",
}


@dataclass
class ParseResult:
    formula: str
    elements: Dict[str, int] = field(default_factory=dict)
    molar_mass: float = 0.0
    ok: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "formula": self.formula,
            "elements": self.elements,
            "molar_mass": round(self.molar_mass, 4),
            "ok": self.ok,
            "error": self.error,
        }


# Parser de formule chimique
_FORMULA_RE = re.compile(
    r'([A-Z][a-z]?)(\d*)|'   # élément + nombre optionnel
    r'\(([^)]+)\)(\d*)|'     # groupe (X)n
    r'\[([^\]]+)\](\d*)'     # groupe [X]n
)


def _parse_molecule(formula: str) -> Dict[str, int]:
    """Parse une formule chimique en dictionnaire {élément: nombre}.

    Ex : "H2O" → {"H": 2, "O": 1}
         "Fe2(SO4)3" → {"Fe": 2, "S": 3, "O": 12}
    """
    atoms: Dict[str, int] = defaultdict(int)
    i = 0
    while i < len(formula):
        if formula[i] == '(':
            depth = 1
            j = i + 1
            while j < len(formula) and depth > 0:
                if formula[j] == '(':
                    depth += 1
                elif formula[j] == ')':
                    depth -= 1
                j += 1
            group = formula[i+1:j-1]
            # Get multiplier after ')'
            mult_str = ''
            while j < len(formula) and formula[j].isdigit():
                mult_str += formula[j]
                j += 1
            multiplier = int(mult_str) if mult_str else 1
            inner = _parse_molecule(group)
            for elem, count in inner.items():
                atoms[elem] += count * multiplier
            i = j
        elif formula[i] == '[':
            j = i + 1
            depth = 1
            while j < len(formula) and depth > 0:
                if formula[j] == '[':
                    depth += 1
                elif formula[j] == ']':
                    depth -= 1
                j += 1
            group = formula[i+1:j-1]
            mult_str = ''
            while j < len(formula) and formula[j].isdigit():
                mult_str += formula[j]
                j += 1
            multiplier = int(mult_str) if mult_str else 1
            inner = _parse_molecule(group)
            for elem, count in inner.items():
                atoms[elem] += count * multiplier
            i = j
        elif formula[i].isupper() or formula[i] == '[':
            # Element: uppercase letter (optional lowercase)
            elem = formula[i]
            i += 1
            while i < len(formula) and formula[i].islower():
                elem += formula[i]
                i += 1
            # Number
            num_str = ''
            while i < len(formula) and formula[i].isdigit():
                num_str += formula[i]
                i += 1
            n = int(num_str) if num_str else 1
            atoms[elem] += n
        else:
            i += 1

    return dict(atoms)


def _molar_mass(atoms: Dict[str, int]) -> float:
    """Calcule la masse molaire d'un dictionnaire d'atomes."""
    mass = 0.0
    for elem, count in atoms.items():
        if elem in ELEMENTS:
            mass += ELEMENTS[elem] * count
        else:
            raise ValueError(f"Élément inconnu : {elem}")
    return mass


def parse_formula(formula: str) -> ParseResult:
    """Parse une formule chimique."""
    try:
        atoms = _parse_molecule(formula)
        mass = _molar_mass(atoms)
        return ParseResult(
            formula=formula,
            elements=atoms,
            molar_mass=mass,
        )
    except Exception as e:
        return ParseResult(formula=formula, ok=False, error=str(e))


# ─── Balancement d'équations chimiques ───
# Utilise sympy optionnellement, sinon règle heuristique simple.

def _extract_side(side: str) -> List[Tuple[str, int]]:
    """Parse un côté d'équation : '2 H2 + O2' → [('H2', 2), ('O2', 1)]"""
    molecules = []
    # Split by +
    parts = re.split(r'\s*\+\s*', side.strip())
    for part in parts:
        part = part.strip()
        if not part:
            continue
        match = re.match(r'(\d*)\s*(.*)', part)
        if match:
            coeff = int(match.group(1)) if match.group(1) else 1
            formula = match.group(2).strip()
            molecules.append((formula, coeff))
    return molecules


def _balance_matrix(reactants: List[Tuple[str, int]],
                    products: List[Tuple[str, int]]) -> Optional[Dict[str, int]]:
    """Balancement par méthode matricielle (sympy)."""
    try:
        from sympy import Matrix, linsolve, symbols
    except ImportError:
        return _balance_heuristic(reactants, products)

    # Collect all elements
    all_atoms = defaultdict(int)
    for formula, coeff in reactants:
        atoms = _parse_molecule(formula)
        for e, c in atoms.items():
            all_atoms[e] += c  # temp
    for formula, coeff in products:
        atoms = _parse_molecule(formula)
        for e, c in atoms.items():
            all_atoms[e] += 0  # ensure present

    elements = list(all_atoms.keys())
    n_unknowns = len(reactants) + len(products)
    n_eqs = len(elements)

    if n_eqs < 1 or n_unknowns < 2:
        return _balance_heuristic(reactants, products)

    # Build matrix
    A = [[0] * n_unknowns for _ in range(n_eqs)]
    B = [0] * n_eqs

    for ei, elem in enumerate(elements):
        for ri, (formula, coeff) in enumerate(reactants):
            atoms = _parse_molecule(formula)
            A[ei][ri] = atoms.get(elem, 0) * coeff
        for pi, (formula, coeff) in enumerate(products):
            atoms = _parse_molecule(formula)
            A[ei][len(reactants) + pi] = -atoms.get(elem, 0) * coeff

    try:
        m = Matrix(A)
        # Nullspace
        ns = m.nullspace()
        if not ns or len(ns) == 0:
            return _balance_heuristic(reactants, products)

        solution = ns[0]
        # Scale to integers
        smallest = min(abs(v) for v in solution if v != 0)
        scale = 1 / smallest if smallest != 0 else 1
        int_sol = [round(float(v * scale) * 1000) / 1000 for v in solution]
        # Find lcm
        from math import gcd, lcm
        denoms = []
        for v in int_sol:
            s = str(v)
            if '.' in s:
                denom = 10 ** len(s.split('.')[1])
            else:
                denom = 1
            denoms.append(denom)
        lcm_val = 1
        for d in denoms:
            lcm_val = lcm_val * d // gcd(lcm_val, d) if d > 1 else lcm_val

        scaled = [round(v * lcm_val) for v in int_sol]
        # Normalize: make first non-zero positive
        for v in scaled:
            if v != 0:
                if v < 0:
                    scaled = [-x for x in scaled]
                break

        result = {}
        for i, (formula, _) in enumerate(reactants + products):
            result[formula] = abs(scaled[i])
        return result
    except Exception:
        return _balance_heuristic(reactants, products)


def _balance_heuristic(reactants: List[Tuple[str, int]],
                        products: List[Tuple[str, int]]) -> Optional[Dict[str, int]]:
    """Balancement heuristique simple (quand sympy indisponible)."""
    # Approche : itérer sur les coefficients de 1 à 10
    all_molecules = [f for f, _ in reactants] + [f for f, _ in products]
    n = len(all_molecules)

    for max_coeff in range(1, 12):
        # Generate coefficient combinations (simplified: increment)
        coeffs = [1] * n
        while True:
            # Check balance
            left_atoms: Dict[str, int] = defaultdict(int)
            right_atoms: Dict[str, int] = defaultdict(int)

            for i, (formula, _) in enumerate(reactants):
                atoms = _parse_molecule(formula)
                for e, c in atoms.items():
                    left_atoms[e] += c * coeffs[i]
            for i, (formula, _) in enumerate(products):
                atoms = _parse_molecule(formula)
                for e, c in atoms.items():
                    right_atoms[e] += c * coeffs[len(reactants) + i]

            if left_atoms == right_atoms:
                result = {}
                for i, formula in enumerate(all_molecules):
                    result[formula] = coeffs[i]
                return result

            # Increment
            idx = 0
            while idx < n and coeffs[idx] >= max_coeff:
                coeffs[idx] = 1
                idx += 1
            if idx >= n:
                break
            coeffs[idx] += 1

    return None


def balance_equation(eq_str: str) -> str:
    """Balance une équation chimique.

    Ex : "H2 + O2 = H2O" → "2 H2 + O2 = 2 H2O"
    """
    try:
        sides = eq_str.split("=")
        if len(sides) != 2:
            return json.dumps({"ok": False, "error": "Format attendu : react = prod", "equation": eq_str})

        reactants = _extract_side(sides[0])
        products = _extract_side(sides[1])

        if not reactants or not products:
            return json.dumps({"ok": False, "error": "Aucun réactif/produit trouvé", "equation": eq_str})

        result = _balance_matrix(reactants, products)

        if not result:
            return json.dumps({"ok": False, "error": "Balancement impossible", "equation": eq_str})

        # Build string
        left_parts = []
        for formula, _ in reactants:
            c = result[formula]
            left_parts.append(f"{c} {formula}" if c > 1 else formula)
        right_parts = []
        for formula, _ in products:
            c = result[formula]
            right_parts.append(f"{c} {formula}" if c > 1 else formula)

        balanced = " + ".join(left_parts) + " = " + " + ".join(right_parts)

        return json.dumps({
            "ok": True,
            "equation": eq_str,
            "balanced": balanced,
            "coefficients": {str(k): int(v) for k, v in result.items()},
            "reactants": [{"formula": f, "coeff": result[f]} for f, _ in reactants],
            "products": [{"formula": f, "coeff": result[f]} for f, _ in products],
        }, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e), "equation": eq_str})


def molar_mass(formula: str) -> str:
    """Calcule la masse molaire d'une formule."""
    result = parse_formula(formula)
    if result.ok:
        return json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    else:
        return json.dumps({"ok": False, "error": result.error})


def yield_calc(reactant_formula: str, product_formula: str,
               reactant_mass: float, product_name: Optional[str] = None) -> str:
    """Calcule le rendement théorique / réel."""
    try:
        reactant = parse_formula(reactant_formula)
        product = parse_formula(product_formula)
        if not reactant.ok:
            return json.dumps({"ok": False, "error": f"Réactif invalide : {reactant.error}"})
        if not product.ok:
            return json.dumps({"ok": False, "error": f"Produit invalide : {product.error}"})

        # Moles du réactif
        reactant_moles = reactant_mass / reactant.molar_mass
        # Stoechiométrie simple 1:1
        product_moles = reactant_moles
        theoretical_mass = product_moles * product.molar_mass

        result = {
            "ok": True,
            "reactant": reactant_formula,
            "product": product_formula,
            "reactant_mass_g": reactant_mass,
            "reactant_molar_mass": round(reactant.molar_mass, 4),
            "reactant_moles": round(reactant_moles, 4),
            "product_molar_mass": round(product.molar_mass, 4),
            "theoretical_mass_g": round(theoretical_mass, 4),
            "note": "Stoechiométrie 1:1 par défaut (utiliser l'équation balancée pour précision)",
        }
        if product_name:
            result["product_name"] = product_name

        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)})


def formula_from_name(name: str) -> str:
    """Convertit un nom commun en formule chimique."""
    name_lower = name.strip().lower()
    if name_lower in COMMON_NAMES:
        formula = COMMON_NAMES[name_lower]
        return json.dumps({
            "ok": True,
            "name": name,
            "formula": formula,
            "parsed": parse_formula(formula).to_dict(),
        }, indent=2, ensure_ascii=False)
    else:
        return json.dumps({
            "ok": False,
            "name": name,
            "error": f"Nom inconnu. Connus : {', '.join(sorted(COMMON_NAMES.keys())[:10])}...",
        })


def _dispatch_chemsym(args):
    """Dispatch chemsym subcommand using pre-parsed args."""
    import json
    cmd = args.command
    if cmd in ("molar-mass", "mass", "mm"):
        print(molar_mass(args.formula))
    elif cmd == "balance":
        print(balance_equation(args.equation))
    elif cmd == "yield":
        print(yield_calc(args.reactant, args.product, args.mass, args.name))
    elif cmd == "parse":
        result = parse_formula(args.formula)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif cmd == "formula":
        print(formula_from_name(args.name))
    else:
        print(f"Unknown command: {cmd}")


def register_cli(subparsers):
    """Register chemsym as a CLI subcommand."""
    p = subparsers.add_parser("chemsym", help="Chimie symbolique (masse molaire, balancement)")
    sub = p.add_subparsers(dest="command", required=True)

    p_mm = sub.add_parser("molar-mass", aliases=["mass", "mm"], help="Masse molaire")
    p_mm.add_argument("formula", help="Formule chimique (ex: H2O, Fe2(SO4)3)")
    p_mm.set_defaults(func=_dispatch_chemsym)

    p_b = sub.add_parser("balance", help="Balancer équation chimique")
    p_b.add_argument("equation", help="Équation (ex: H2 + O2 = H2O)")
    p_b.set_defaults(func=_dispatch_chemsym)

    p_y = sub.add_parser("yield", help="Rendement théorique")
    p_y.add_argument("reactant", help="Formule du réactif")
    p_y.add_argument("--product", "-p", required=True, help="Formule du produit")
    p_y.add_argument("--mass", "-m", type=float, required=True, help="Masse du réactif (g)")
    p_y.add_argument("--name", "-n", help="Nom du produit (optionnel)")
    p_y.set_defaults(func=_dispatch_chemsym)

    p_ps = sub.add_parser("parse", help="Parser une formule en atomes")
    p_ps.add_argument("formula", help="Formule chimique")
    p_ps.set_defaults(func=_dispatch_chemsym)

    p_f = sub.add_parser("formula", help="Nom commun → formule")
    p_f.add_argument("name", help="Nom (ex: water, sodium chloride)")
    p_f.set_defaults(func=_dispatch_chemsym)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Outils de chimie symbolique")
    sub = parser.add_subparsers()
    register_cli(sub)
    args = parser.parse_args()
    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()