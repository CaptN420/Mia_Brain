#!/usr/bin/env python3
"""Equation Tools — 20+ outils déterministes pour math, chimie, physique, etc.

Tous les outils sont pure Python, sans LLM, sans dépendances externes.
Chaque outil est une fonction standalone qui prend des entrées simples
et retourne des résultats structurés (dict).

Utilisation:
    from equation_tools import *
    result = solve_quadratic(1, -3, 2)
    result = balance_stoichiometry({'H2': 2, 'O2': 1}, {'H2O': 2})
    result = kinematics_free_fall(10, 'm')
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union

# ═══════════════════════════════════════════════════════════════
# 1. MATH — OUTILS GÉNÉRAUX
# ═══════════════════════════════════════════════════════════════

def solve_quadratic(a: float, b: float, c: float) -> Dict[str, Any]:
    """Résout ax² + bx + c = 0. Retourne racines réelles ou complexes."""
    if a == 0:
        return {"valid": False, "error": "a ne peut pas être 0"}
    d = b*b - 4*a*c
    if d >= 0:
        x1 = (-b + math.sqrt(d)) / (2*a)
        x2 = (-b - math.sqrt(d)) / (2*a)
        return {
            "valid": True, "discriminant": round(d, 6),
            "real": True, "x1": round(x1, 6), "x2": round(x2, 6),
            "factorized": f"{a}(x - {round(x1,3)})(x - {round(x2,3)})" if a != 1 else f"(x - {round(x1,3)})(x - {round(x2,3)})"
        }
    real = -b/(2*a)
    imag = math.sqrt(-d)/(2*a)
    return {
        "valid": True, "discriminant": round(d, 6), "real": False,
        "x1": f"{round(real,4)} + {round(imag,4)}i",
        "x2": f"{round(real,4)} - {round(imag,4)}i",
    }


def solve_linear_system_2x2(a1: float, b1: float, c1: float,
                             a2: float, b2: float, c2: float) -> Dict[str, Any]:
    """Résout un système de 2 équations linéaires à 2 inconnues :
       a1*x + b1*y = c1
       a2*x + b2*y = c2
    """
    det = a1*b2 - a2*b1
    if abs(det) < 1e-12:
        return {"valid": False, "error": "Système singulier (déterminant nul)"}
    x = (c1*b2 - c2*b1) / det
    y = (a1*c2 - a2*c1) / det
    return {
        "valid": True, "x": round(x, 6), "y": round(y, 6),
        "determinant": round(det, 6),
        "matrix": f"[[{a1},{b1}],[{a2},{b2}]]·[x,y] = [{c1},{c2}]"
    }


def derivative_numeric(expr: str, x0: float, h: float = 1e-6) -> Dict[str, Any]:
    """Dérivée numérique par différence centrée : f'(x0) ≈ (f(x0+h)-f(x0-h))/(2h)

    Expr est une expression Python valide en x (ex: 'x**2 + 3*x').
    """
    try:
        x = x0 + h
        f_plus = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
        x = x0 - h
        f_minus = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
        x = x0
        f0 = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
        deriv = (f_plus - f_minus) / (2*h)
        return {
            "valid": True, "f_prime": round(deriv, 6),
            "f_at_x0": round(f0, 6), "h": h,
            "error_estimate": round(abs(deriv * h**2 / 6), 10)
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def integral_numeric(expr: str, a: float, b: float, n: int = 1000) -> Dict[str, Any]:
    """Intégration numérique par la méthode des trapèzes.

    Expr est une expression Python valide en x.
    """
    try:
        h = (b - a) / n
        total = 0.0
        x = a
        fa = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
        for i in range(1, n):
            x = a + i * h
            fx = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
            total += fx
        x = b
        fb = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
        integral = h * (0.5*fa + total + 0.5*fb)
        return {
            "valid": True, "integral": round(integral, 6),
            "a": a, "b": b, "n": n, "method": "trapezoidal"
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def taylor_series(expr: str, x0: float = 0, order: int = 4) -> Dict[str, Any]:
    """Développement limité (série de Taylor) d'une expression autour de x0.

    Utilise les dérivées numériques. Retourne les coefficients.
    """
    terms = []
    h = 1e-4
    try:
        for n in range(order + 1):
            if n == 0:
                x = x0
                coeff = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
            else:
                # Dérivée n-ième par différence finie
                xs = [x0 + (i - n//2) * h for i in range(n + 1)]
                ys = []
                for xi in xs:
                    ys.append(eval(expr, {"x": xi, "math": math, "e": math.e, "pi": math.pi}))
                # Différences divisées
                for k in range(1, n + 1):
                    for i in range(n, k - 1, -1):
                        ys[i] = (ys[i] - ys[i-1]) / (xs[i] - xs[i-k])
                coeff = ys[n] * math.factorial(n)
            terms.append({
                "n": n, "coefficient": round(coeff, 6),
                "term": f"{round(coeff,4)} * (x - {x0})^{n}" if n > 0 else str(round(coeff, 4)),
                "formula": f"f^{{{n}}}({x0})/{n}!" if n > 0 else f"f({x0})"
            })
        return {"valid": True, "center": x0, "order": order, "terms": terms}
    except Exception as e:
        return {"valid": False, "error": str(e)}


def matrix_operations(matrix: List[List[float]], op: str = "det") -> Dict[str, Any]:
    """Opérations matricielles : déterminant, trace, transposée, inverse.

    Args:
        matrix: Matrice carrée [[a,b],[c,d]] ou [[a,b,c],[d,e,f],[g,h,i]]
        op: 'det', 'trace', 'transpose', 'inverse', 'multiply'
    """
    n = len(matrix)
    if n == 0 or any(len(row) != n for row in matrix):
        return {"valid": False, "error": "Matrice non carrée"}

    if op == "det":
        if n == 2:
            det = matrix[0][0]*matrix[1][1] - matrix[0][1]*matrix[1][0]
            return {"valid": True, "determinant": round(det, 6), "size": "2x2"}
        elif n == 3:
            a, b, c = matrix[0]
            d, e, f = matrix[1]
            g, h, i = matrix[2]
            det = a*(e*i - f*h) - b*(d*i - f*g) + c*(d*h - e*g)
            return {"valid": True, "determinant": round(det, 6), "size": "3x3"}
        return {"valid": False, "error": "Déterminant implémenté pour 2x2 et 3x3 seulement"}

    if op == "trace":
        trace = sum(matrix[i][i] for i in range(n))
        return {"valid": True, "trace": round(trace, 6), "size": f"{n}x{n}"}

    if op == "transpose":
        t = [[matrix[j][i] for j in range(n)] for i in range(n)]
        return {"valid": True, "transpose": [[round(v, 6) for v in row] for row in t]}

    if op == "inverse":
        det = 0
        if n == 2:
            det = matrix[0][0]*matrix[1][1] - matrix[0][1]*matrix[1][0]
            if abs(det) < 1e-12:
                return {"valid": False, "error": "Matrice singulière"}
            inv = [[matrix[1][1]/det, -matrix[0][1]/det],
                   [-matrix[1][0]/det, matrix[0][0]/det]]
            return {"valid": True, "inverse": [[round(v, 6) for v in row] for row in inv]}
        return {"valid": False, "error": "Inverse implémentée pour 2x2 seulement"}

    return {"valid": False, "error": f"Opération inconnue: {op}"}


def statistics_basic(data: List[float]) -> Dict[str, Any]:
    """Statistiques descriptives de base : moyenne, médiane, variance, écart-type."""
    if not data:
        return {"valid": False, "error": "Données vides"}
    n = len(data)
    mean = sum(data) / n
    sorted_data = sorted(data)
    median = sorted_data[n//2] if n % 2 else (sorted_data[n//2-1] + sorted_data[n//2]) / 2
    variance = sum((x - mean)**2 for x in data) / (n - 1) if n > 1 else 0
    std = math.sqrt(variance)
    _min = min(data)
    _max = max(data)
    return {
        "valid": True, "n": n, "mean": round(mean, 6), "median": round(median, 6),
        "variance": round(variance, 6), "std": round(std, 6),
        "min": round(_min, 6), "max": round(_max, 6),
        "range": round(_max - _min, 6), "data": data[:10],
    }


def interpolation_linear(xs: List[float], ys: List[float], x_target: float) -> Dict[str, Any]:
    """Interpolation linéaire entre des points (x,y)."""
    if len(xs) < 2 or len(ys) < 2:
        return {"valid": False, "error": "Au moins 2 points requis"}
    points = sorted(zip(xs, ys))
    xs_sorted, ys_sorted = zip(*points)
    if x_target < xs_sorted[0] or x_target > xs_sorted[-1]:
        return {"valid": False, "error": f"x_target={x_target} hors intervalle [{xs_sorted[0]}, {xs_sorted[-1]}]"}
    for i in range(len(xs_sorted) - 1):
        if xs_sorted[i] <= x_target <= xs_sorted[i+1]:
            t = (x_target - xs_sorted[i]) / (xs_sorted[i+1] - xs_sorted[i])
            y = ys_sorted[i] + t * (ys_sorted[i+1] - ys_sorted[i])
            return {
                "valid": True, "x": x_target, "y": round(y, 6),
                "between": (xs_sorted[i], xs_sorted[i+1]),
                "method": "linear"
            }
    return {"valid": False, "error": "Erreur d'interpolation"}


# ═══════════════════════════════════════════════════════════════
# 2. PHYSIQUE — OUTILS SPÉCIALISÉS
# ═══════════════════════════════════════════════════════════════

def kinematics_free_fall(height: float, unit: str = "m") -> Dict[str, Any]:
    """Chute libre : temps, vitesse finale, énergie.

    g = 9.81 m/s². h = ½gt² → t = √(2h/g), v = gt
    """
    g = 9.81
    t = math.sqrt(2 * height / g)
    v = g * t
    return {
        "valid": True, "height": height, "unit": unit,
        "time": round(t, 4), "time_unit": "s",
        "final_velocity": round(v, 4), "velocity_unit": "m/s",
        "g": g,
        "formula": f"t = √(2*{height}/{g}) = {t:.4f}s, v = {g}*{t:.4f} = {v:.4f} m/s"
    }


def kinematics_projectile(v0: float, angle_deg: float, g: float = 9.81) -> Dict[str, Any]:
    """Mouvement parabolique : portée, hauteur max, temps de vol.

    Args:
        v0: Vitesse initiale (m/s)
        angle_deg: Angle de tir (degrés)
        g: Gravité (m/s²)
    """
    theta = math.radians(angle_deg)
    vx = v0 * math.cos(theta)
    vy = v0 * math.sin(theta)
    t_flight = 2 * vy / g
    t_peak = vy / g
    h_max = vy**2 / (2*g)
    range_m = vx * t_flight
    return {
        "valid": True, "v0": v0, "angle_deg": angle_deg, "g": g,
        "vx": round(vx, 4), "vy": round(vy, 4),
        "time_of_flight": round(t_flight, 4), "time_unit": "s",
        "max_height": round(h_max, 4), "height_unit": "m",
        "range": round(range_m, 4), "range_unit": "m",
        "time_to_peak": round(t_peak, 4),
    }


def energy_kinetic(mass: float, velocity: float) -> Dict[str, Any]:
    """Énergie cinétique : Ek = ½mv²."""
    ek = 0.5 * mass * velocity**2
    return {
        "valid": True, "mass": mass, "mass_unit": "kg",
        "velocity": velocity, "velocity_unit": "m/s",
        "kinetic_energy": round(ek, 4), "energy_unit": "J",
        "formula": f"Ek = ½*{mass}*{velocity}² = {ek:.4f} J"
    }


def energy_potential(mass: float, height: float, g: float = 9.81) -> Dict[str, Any]:
    """Énergie potentielle gravitationnelle : Ep = mgh."""
    ep = mass * g * height
    return {
        "valid": True, "mass": mass, "mass_unit": "kg",
        "height": height, "height_unit": "m", "g": g,
        "potential_energy": round(ep, 4), "energy_unit": "J",
        "formula": f"Ep = {mass}*{g}*{height} = {ep:.4f} J"
    }


def ohms_law(voltage: Optional[float] = None,
             current: Optional[float] = None,
             resistance: Optional[float] = None) -> Dict[str, Any]:
    """Loi d'Ohm : U = R·I. Donne 2 valeurs, calcule la 3e."""
    given = sum(1 for v in (voltage, current, resistance) if v is not None)
    if given < 2:
        return {"valid": False, "error": "Donne au moins 2 valeurs sur 3"}
    if voltage is not None and current is not None and resistance is None:
        r = voltage / current
        return {"valid": True, "voltage": voltage, "current": current, "resistance": round(r, 4)}
    if voltage is not None and resistance is not None and current is None:
        i = voltage / resistance
        return {"valid": True, "voltage": voltage, "current": round(i, 4), "resistance": resistance}
    if current is not None and resistance is not None and voltage is None:
        u = current * resistance
        return {"valid": True, "voltage": round(u, 4), "current": current, "resistance": resistance}
    return {"valid": False, "error": "Valeurs incohérentes"}


def wave_properties(frequency: Optional[float] = None,
                    wavelength: Optional[float] = None,
                    speed: Optional[float] = None) -> Dict[str, Any]:
    """Relation onde : v = λf. Donne 2 valeurs, calcule la 3e."""
    given = sum(1 for v in (frequency, wavelength, speed) if v is not None)
    if given < 2:
        return {"valid": False, "error": "Donne au moins 2 valeurs sur 3"}
    if speed is not None and frequency is not None and wavelength is None:
        lam = speed / frequency
        return {"valid": True, "speed": speed, "frequency": frequency, "wavelength": round(lam, 6)}
    if speed is not None and wavelength is not None and frequency is None:
        f = speed / wavelength
        return {"valid": True, "speed": speed, "frequency": round(f, 6), "wavelength": wavelength}
    if frequency is not None and wavelength is not None and speed is None:
        v = frequency * wavelength
        return {"valid": True, "speed": round(v, 6), "frequency": frequency, "wavelength": wavelength}
    return {"valid": False, "error": "Valeurs incohérentes"}


def gas_law_ideal(P: Optional[float] = None, V: Optional[float] = None,
                  n: Optional[float] = None, T: Optional[float] = None,
                  R: float = 8.314) -> Dict[str, Any]:
    """Loi des gaz parfaits : PV = nRT.

    Donne 3 valeurs, calcule la 4e.
    Units: P (Pa), V (m³), n (mol), T (K), R = 8.314 J/(mol·K)
    """
    given = {k: v for k, v in [("P", P), ("V", V), ("n", n), ("T", T)] if v is not None}
    if len(given) < 3:
        return {"valid": False, "error": "Donne au moins 3 valeurs sur 4"}
    try:
        if P is None:
            result = n * R * T / V
            return {"valid": True, "P": round(result, 4), "V": V, "n": n, "T": T, "R": R}
        if V is None:
            result = n * R * T / P
            return {"valid": True, "P": P, "V": round(result, 4), "n": n, "T": T, "R": R}
        if n is None:
            result = P * V / (R * T)
            return {"valid": True, "P": P, "V": V, "n": round(result, 4), "T": T, "R": R}
        if T is None:
            result = P * V / (n * R)
            return {"valid": True, "P": P, "V": V, "n": n, "T": round(result, 4), "R": R}
    except (ZeroDivisionError, ValueError) as e:
        return {"valid": False, "error": str(e)}
    return {"valid": False, "error": "Valeurs incohérentes"}


# ═══════════════════════════════════════════════════════════════
# 3. CHIMIE — OUTILS SPÉCIALISÉS
# ═══════════════════════════════════════════════════════════════

# Constantes
AVOGADRO = 6.02214076e23
R_GAS = 8.314462618
ATOMIC_MASSES = {
    "H": 1.008, "He": 4.003, "Li": 6.941, "Be": 9.012, "B": 10.811,
    "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180,
    "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.086, "P": 30.974,
    "S": 32.065, "Cl": 35.453, "K": 39.098, "Ar": 39.948, "Ca": 40.078,
    "Fe": 55.845, "Cu": 63.546, "Zn": 65.380, "Br": 79.904, "Ag": 107.868,
    "I": 126.904, "Pt": 195.084, "Au": 196.967, "Hg": 200.590, "Pb": 207.200,
}


def molecular_weight(formula: str) -> Dict[str, Any]:
    """Calcule la masse molaire d'une formule chimique simple.

    Ex: 'H2O' → 18.015, 'CO2' → 44.009, 'NaCl' → 58.443
    """
    # Parse la formule: lettres majuscules + minuscules optionnelles + chiffres optionnels
    pattern = re.findall(r'([A-Z][a-z]?)(\d*\.?\d*)', formula)
    if not pattern:
        return {"valid": False, "error": f"Formule non reconnue: {formula}"}
    total = 0.0
    details = []
    for element, count_str in pattern:
        if element not in ATOMIC_MASSES:
            return {"valid": False, "error": f"Élément inconnu: {element}"}
        count = float(count_str) if count_str else 1.0
        mass = ATOMIC_MASSES[element] * count
        total += mass
        details.append({"element": element, "count": count, "mass": round(mass, 4)})
    return {
        "valid": True, "formula": formula,
        "molecular_weight": round(total, 4), "unit": "g/mol",
        "details": details,
        "atoms": sum(d["count"] for d in details),
    }


def concentration_dilution(C1: float, V1: float, C2: Optional[float] = None,
                           V2: Optional[float] = None) -> Dict[str, Any]:
    """Dilution : C1V1 = C2V2. Donne 3 valeurs, calcule la 4e."""
    given = sum(1 for v in (C1, V1, C2, V2) if v is not None)
    if given < 3:
        return {"valid": False, "error": "Donne au moins 3 valeurs sur 4"}
    try:
        if C2 is None:
            return {"valid": True, "C1": C1, "V1": V1, "C2": round(C1*V1/V2, 6), "V2": V2}
        if V2 is None:
            return {"valid": True, "C1": C1, "V1": V1, "C2": C2, "V2": round(C1*V1/C2, 6)}
        if C1 is None:
            return {"valid": True, "C1": round(C2*V2/V1, 6), "V1": V1, "C2": C2, "V2": V2}
        if V1 is None:
            return {"valid": True, "C1": C1, "V1": round(C2*V2/C1, 6), "C2": C2, "V2": V2}
    except (ZeroDivisionError, ValueError) as e:
        return {"valid": False, "error": str(e)}
    return {"valid": False, "error": "Valeurs incohérentes"}


def ph_calculator(H_conc: Optional[float] = None, pH: Optional[float] = None) -> Dict[str, Any]:
    """Calcule pH = -log[H+] ou [H+] = 10^(-pH)."""
    if H_conc is not None and pH is None:
        if H_conc <= 0:
            return {"valid": False, "error": "[H+] doit être > 0"}
        ph = -math.log10(H_conc)
        return {"valid": True, "H_conc": H_conc, "pH": round(ph, 4), "method": "pH = -log[H+]"}
    if pH is not None and H_conc is None:
        h = 10**(-pH)
        return {"valid": True, "H_conc": f"{h:.6e}", "pH": pH, "method": "[H+] = 10^(-pH)"}
    return {"valid": False, "error": "Donne [H+] OU pH"}


def reaction_rate(k: float, concentrations: Dict[str, float],
                  orders: Dict[str, float]) -> Dict[str, Any]:
    """Loi de vitesse : v = k * Π [C_i]^{order_i}.

    Args:
        k: Constante de vitesse
        concentrations: Dict {espèce: concentration}
        orders: Dict {espèce: ordre partiel}
    """
    rate = k
    details = []
    for species, conc in concentrations.items():
        order = orders.get(species, 0)
        term = conc ** order
        rate *= term
        details.append({"species": species, "conc": conc, "order": order, "term": round(term, 6)})
    return {
        "valid": True, "rate": round(rate, 6),
        "k": k, "details": details,
        "total_order": sum(orders.values()),
        "formula": f"v = {k} * " + " * ".join(f"[{s}]^{{{orders.get(s,0)}}}" for s in concentrations),
    }


def equilibrium_constant(K: Optional[float] = None,
                         products: Optional[Dict[str, float]] = None,
                         reactants: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Constante d'équilibre : K = Π [produits]^{coeff} / Π [réactifs]^{coeff}.

    Si K donnée, vérifie l'équilibre. Sinon calcule K.
    """
    if products and reactants:
        Q = 1.0
        for species, conc in products.items():
            Q *= conc
        for species, conc in reactants.items():
            Q /= conc if conc > 0 else 1e-12
        if K is not None:
            return {
                "valid": True, "K": K, "Q": round(Q, 6),
                "equilibrium": Q == K,
                "direction": "→" if Q < K else "←" if Q > K else "↔",
                "delta": round(Q - K, 6),
            }
        return {"valid": True, "K": round(Q, 6), "Q": round(Q, 6), "calculated": True}
    return {"valid": False, "error": "Donne produits et réactifs"}


def arrhenius(A: float, Ea: float, T: float, R: float = 8.314) -> Dict[str, Any]:
    """Loi d'Arrhenius : k = A·exp(-Ea/(RT)).

    Args:
        A: Facteur pré-exponentiel (s⁻¹)
        Ea: Énergie d'activation (J/mol)
        T: Température (K)
        R: Constante des gaz (J/(mol·K))
    """
    k = A * math.exp(-Ea / (R * T))
    return {
        "valid": True, "k": round(k, 6),
        "A": A, "Ea": Ea, "T": T, "R": R,
        "exponent": round(-Ea/(R*T), 4),
        "formula": f"k = {A}·exp(-{Ea}/({R}·{T})) = {k:.6f}"
    }


# ═══════════════════════════════════════════════════════════════
# 4. OUTILS TRANSVERSAUX
# ═══════════════════════════════════════════════════════════════

def unit_converter(value: float, from_unit: str, to_unit: str) -> Dict[str, Any]:
    """Convertisseur d'unités simple.

    Unités supportées: m, km, cm, mm, g, kg, mg, s, min, h, J, kJ, cal, kcal, N, kN, Pa, kPa, bar, atm, L, mL, m³, °C, K, °F
    """
    # Longueur
    length = {"m": 1, "km": 1000, "cm": 0.01, "mm": 0.001, "µm": 1e-6, "nm": 1e-9,
              "inch": 0.0254, "ft": 0.3048, "mile": 1609.344}
    # Masse
    mass = {"g": 1, "kg": 1000, "mg": 0.001, "µg": 1e-6, "ton": 1e6, "lb": 453.592}
    # Temps
    time = {"s": 1, "min": 60, "h": 3600, "day": 86400, "ms": 0.001, "µs": 1e-6}
    # Énergie
    energy = {"J": 1, "kJ": 1000, "cal": 4.184, "kcal": 4184, "eV": 1.602e-19, "kWh": 3.6e6}
    # Pression
    pressure = {"Pa": 1, "kPa": 1000, "bar": 1e5, "atm": 101325, "mmHg": 133.322, "psi": 6894.76}
    # Volume
    volume = {"L": 0.001, "mL": 1e-6, "m³": 1, "cm³": 1e-6, "gal": 0.00378541, "fl_oz": 2.957e-5}

    categories = [
        ("length", length), ("mass", mass), ("time", time),
        ("energy", energy), ("pressure", pressure), ("volume", volume),
    ]

    for cat_name, cat in categories:
        if from_unit in cat and to_unit in cat:
            base = value * cat[from_unit]
            converted = base / cat[to_unit]
            return {
                "valid": True, "value": value, "from": from_unit, "to": to_unit,
                "result": round(converted, 6), "category": cat_name,
                "formula": f"{value} {from_unit} = {converted:.6f} {to_unit}"
            }

    # Température (spécial)
    if from_unit == "°C" and to_unit == "K":
        return {"valid": True, "result": round(value + 273.15, 4), "from": "°C", "to": "K"}
    if from_unit == "K" and to_unit == "°C":
        return {"valid": True, "result": round(value - 273.15, 4), "from": "K", "to": "°C"}
    if from_unit == "°C" and to_unit == "°F":
        return {"valid": True, "result": round(value * 9/5 + 32, 4), "from": "°C", "to": "°F"}
    if from_unit == "°F" and to_unit == "°C":
        return {"valid": True, "result": round((value - 32) * 5/9, 4), "from": "°F", "to": "°C"}

    return {"valid": False, "error": f"Conversion {from_unit} → {to_unit} non supportée"}


def scientific_notation(value: float) -> Dict[str, Any]:
    """Convertit un nombre en notation scientifique."""
    if value == 0:
        return {"valid": True, "value": 0, "scientific": "0", "mantissa": 0, "exponent": 0}
    sign = "-" if value < 0 else ""
    mantissa, exponent = f"{abs(value):.6e}".split("e")
    mantissa = float(mantissa)
    exponent = int(exponent)
    return {
        "valid": True, "value": value,
        "scientific": f"{sign}{mantissa}×10^{exponent}",
        "mantissa": mantissa, "exponent": exponent,
        "engineering": f"{sign}{mantissa}E{exponent:+d}",
    }


def significant_figures(value: float, sig_figs: int = 3) -> Dict[str, Any]:
    """Arrondit une valeur à N chiffres significatifs."""
    if value == 0:
        return {"valid": True, "original": 0, "rounded": 0, "sig_figs": sig_figs}
    return {
        "valid": True, "original": value, "sig_figs": sig_figs,
        "rounded": round(value, sig_figs - 1 - int(math.floor(math.log10(abs(value))))),
        "format": f"{value:.{sig_figs - 1}e}" if abs(value) < 10**-(sig_figs-1) or abs(value) >= 10**sig_figs else str(round(value, sig_figs - 1 - int(math.floor(math.log10(abs(value)))))),
    }


def percentage_calculator(total: float, part: Optional[float] = None,
                          percent: Optional[float] = None) -> Dict[str, Any]:
    """Calculs de pourcentage : part = total × percent / 100.

    Donne 2 valeurs, calcule la 3e.
    """
    given = sum(1 for v in (total, part, percent) if v is not None)
    if given < 2:
        return {"valid": False, "error": "Donne au moins 2 valeurs sur 3"}
    if total is not None and part is not None and percent is None:
        p = part / total * 100
        return {"valid": True, "total": total, "part": part, "percent": round(p, 4)}
    if total is not None and percent is not None and part is None:
        pa = total * percent / 100
        return {"valid": True, "total": total, "part": round(pa, 6), "percent": percent}
    if part is not None and percent is not None and total is None:
        t = part / percent * 100
        return {"valid": True, "total": round(t, 6), "part": part, "percent": percent}
    return {"valid": False, "error": "Valeurs incohérentes"}


def equation_parser(equation: str) -> Dict[str, Any]:
    """Analyse une équation mathématique : extrait les variables, opérateurs, structure.

    Ex: 'Ndot = k * A * B / (1 + R)' → variables, opérateurs, complexité
    """
    eq = equation.strip()
    if "=" in eq:
        lhs, rhs = eq.split("=", 1)
        lhs = lhs.strip()
        rhs = rhs.strip()
    else:
        lhs = ""
        rhs = eq

    # Variables
    variables = sorted(set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', rhs)))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    variables = [v for v in variables if v not in reserved]

    # Opérateurs
    operators = re.findall(r'[+\-*/^()]', rhs)
    op_counts = {}
    for op in operators:
        op_counts[op] = op_counts.get(op, 0) + 1

    # Constantes
    constants = re.findall(r'\b\d+\.?\d*\b', rhs)

    # Complexité
    complexity = len(variables) + len(set(operators)) + (1 if "(" in rhs else 0)

    # Structure
    structure = "equation" if "=" in eq else "expression"
    if lhs:
        structure += f" ({lhs} = ...)"

    return {
        "valid": True, "equation": eq,
        "lhs": lhs, "rhs": rhs,
        "variables": variables,
        "n_variables": len(variables),
        "operators": op_counts,
        "n_operators": len(operators),
        "constants": constants,
        "complexity": complexity,
        "has_division": "/" in rhs,
        "has_multiplication": "*" in rhs,
        "has_exponent": "**" in rhs or "^" in rhs,
        "has_parentheses": "(" in rhs,
        "structure": structure,
    }


# ═══════════════════════════════════════════════════════════════
# 5. MATH — OUTILS AVANCÉS
# ═══════════════════════════════════════════════════════════════

def vector_operations(v1: List[float], v2: Optional[List[float]] = None,
                      op: str = "norm") -> Dict[str, Any]:
    """Opérations vectorielles : norme, produit scalaire, produit vectoriel, angle.

    Args:
        v1: Premier vecteur
        v2: Second vecteur (optionnel, requis pour scalaire/vectoriel/angle)
        op: 'norm', 'dot', 'cross', 'angle', 'normalize'
    """
    dim = len(v1)
    if op == "norm":
        norm = math.sqrt(sum(x**2 for x in v1))
        return {"valid": True, "vector": v1, "norm": round(norm, 6), "dim": dim}
    if op == "normalize":
        norm = math.sqrt(sum(x**2 for x in v1))
        if norm == 0:
            return {"valid": False, "error": "Vecteur nul"}
        normalized = [round(x/norm, 6) for x in v1]
        return {"valid": True, "unit_vector": normalized, "norm": round(norm, 6)}
    if v2 is None:
        return {"valid": False, "error": "v2 requis pour cette opération"}
    if op == "dot":
        if len(v1) != len(v2):
            return {"valid": False, "error": "Dimensions différentes"}
        dot = sum(a*b for a, b in zip(v1, v2))
        return {"valid": True, "dot_product": round(dot, 6), "v1": v1, "v2": v2}
    if op == "angle":
        dot = sum(a*b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(x**2 for x in v1))
        n2 = math.sqrt(sum(x**2 for x in v2))
        if n1 * n2 == 0:
            return {"valid": False, "error": "Vecteur nul"}
        cos_angle = dot / (n1 * n2)
        cos_angle = max(-1, min(1, cos_angle))
        angle_rad = math.acos(cos_angle)
        angle_deg = math.degrees(angle_rad)
        return {"valid": True, "angle_rad": round(angle_rad, 6), "angle_deg": round(angle_deg, 4),
                "cos": round(cos_angle, 6), "v1": v1, "v2": v2}
    if op == "cross":
        if len(v1) != 3 or len(v2) != 3:
            return {"valid": False, "error": "Produit vectoriel défini pour 3D seulement"}
        cx = v1[1]*v2[2] - v1[2]*v2[1]
        cy = v1[2]*v2[0] - v1[0]*v2[2]
        cz = v1[0]*v2[1] - v1[1]*v2[0]
        return {"valid": True, "cross_product": [round(cx,6), round(cy,6), round(cz,6)]}
    return {"valid": False, "error": f"Opération inconnue: {op}"}


def complex_operations(z1_real: float, z1_imag: float, op: str = "modulus",
                       z2_real: float = 0, z2_imag: float = 0) -> Dict[str, Any]:
    """Opérations sur les nombres complexes.

    Args:
        z1_real, z1_imag: Premier nombre complexe
        op: 'modulus', 'conjugate', 'add', 'subtract', 'multiply', 'divide', 'power'
        z2_real, z2_imag: Second nombre (pour opérations binaires)
    """
    z1 = complex(z1_real, z1_imag)
    z2 = complex(z2_real, z2_imag)
    z1_str = f"{z1_real}{z1_imag:+.2f}i" if z1_imag != 0 else str(z1_real)
    if op == "modulus":
        r = abs(z1)
        theta = math.atan2(z1_imag, z1_real)
        return {"valid": True, "z": z1_str, "modulus": round(r, 6),
                "argument_rad": round(theta, 6), "argument_deg": round(math.degrees(theta), 4),
                "polar": f"{r:.4f}·e^{theta:.4f}i"}
    if op == "conjugate":
        conj = z1.conjugate()
        return {"valid": True, "z": z1_str, "conjugate": f"{conj.real:.4f}{conj.imag:+.4f}i"}
    if op == "add":
        result = z1 + z2
        return {"valid": True, "z1": z1_str, "z2": f"{z2_real}{z2_imag:+.2f}i",
                "result": f"{result.real:.4f}{result.imag:+.4f}i", "operation": "add"}
    if op == "subtract":
        result = z1 - z2
        return {"valid": True, "z1": z1_str, "z2": f"{z2_real}{z2_imag:+.2f}i",
                "result": f"{result.real:.4f}{result.imag:+.4f}i", "operation": "subtract"}
    if op == "multiply":
        result = z1 * z2
        return {"valid": True, "z1": z1_str, "z2": f"{z2_real}{z2_imag:+.2f}i",
                "result": f"{result.real:.4f}{result.imag:+.4f}i", "operation": "multiply"}
    if op == "divide":
        if abs(z2) < 1e-12:
            return {"valid": False, "error": "Division par zéro"}
        result = z1 / z2
        return {"valid": True, "z1": z1_str, "z2": f"{z2_real}{z2_imag:+.2f}i",
                "result": f"{result.real:.4f}{result.imag:+.4f}i", "operation": "divide"}
    if op == "power":
        result = z1 ** z2 if (z2_real != 0 or z2_imag != 0) else z1
        return {"valid": True, "z1": z1_str, "z2": f"{z2_real}{z2_imag:+.2f}i" if (z2_real != 0 or z2_imag != 0) else "0",
                "result": f"{result.real:.4f}{result.imag:+.4f}i", "operation": "power"}
    return {"valid": False, "error": f"Opération inconnue: {op}"}


def prime_factors(n: int) -> Dict[str, Any]:
    """Factorisation en nombres premiers."""
    if n < 2:
        return {"valid": False, "error": "Doit être ≥ 2"}
    temp = n
    factors = []
    d = 2
    while d * d <= temp:
        count = 0
        while temp % d == 0:
            temp //= d
            count += 1
        if count > 0:
            factors.append({"prime": d, "exponent": count, "value": d**count})
        d += 1 if d == 2 else 2
    if temp > 1:
        factors.append({"prime": temp, "exponent": 1, "value": temp})
    return {
        "valid": True, "n": n,
        "factors": factors,
        "factorization": " × ".join(f"{f['prime']}^{f['exponent']}" if f['exponent'] > 1 else str(f['prime']) for f in factors),
        "is_prime": len(factors) == 1 and factors[0]["exponent"] == 1,
    }


def combinatorics(n: int, k: Optional[int] = None, op: str = "factorial") -> Dict[str, Any]:
    """Outils combinatoires : factorielle, coefficient binomial, permutations.

    Args:
        n: Nombre d'éléments
        k: Tirage (optionnel)
        op: 'factorial', 'binomial', 'permutations', 'pascal'
    """
    def fact(x: int) -> int:
        if x < 0:
            raise ValueError("négatif")
        r = 1
        for i in range(2, x + 1):
            r *= i
        return r

    if op == "factorial":
        if n > 170:
            return {"valid": False, "error": "n! trop grand (dépassement)"}
        f = fact(n)
        return {"valid": True, "n": n, "factorial": f, "notation": f"{n}!"}
    if op == "binomial" and k is not None:
        if k < 0 or k > n:
            return {"valid": False, "error": "k doit être entre 0 et n"}
        if n > 1000:
            return {"valid": False, "error": "n trop grand"}
        c = 1
        k = min(k, n - k)
        for i in range(1, k + 1):
            c = c * (n - k + i) // i
        return {"valid": True, "n": n, "k": k, "binomial": c, "notation": f"C({n},{k}) = {c}"}
    if op == "permutations" and k is not None:
        if k < 0 or k > n:
            return {"valid": False, "error": "k doit être entre 0 et n"}
        p = 1
        for i in range(n, n - k, -1):
            p *= i
        return {"valid": True, "n": n, "k": k, "permutations": p, "notation": f"P({n},{k}) = {p}"}
    if op == "pascal":
        rows = []
        for i in range(n):
            row = []
            c = 1
            for j in range(i + 1):
                row.append(c)
                c = c * (i - j) // (j + 1)
            rows.append(row)
        return {"valid": True, "rows": n, "triangle": rows}
    return {"valid": False, "error": "Opération inconnue"}


def series_sum(a: float, r_or_d: float, n: int, kind: str = "arithmetic") -> Dict[str, Any]:
    """Somme de série arithmétique ou géométrique.

    Args:
        a: Premier terme
        r_or_d: Raison (géométrique) ou différence (arithmétique)
        n: Nombre de termes
        kind: 'arithmetic' ou 'geometric'
    """
    if n < 1:
        return {"valid": False, "error": "n doit être ≥ 1"}
    if kind == "arithmetic":
        terms = [a + i * r_or_d for i in range(n)]
        s = n * (2 * a + (n - 1) * r_or_d) / 2
        return {"valid": True, "kind": "arithmetic",
                "first_term": a, "difference": r_or_d, "n": n,
                "last_term": terms[-1], "sum": round(s, 6),
                "formula": f"S = {n}/2·(2·{a} + ({n}-1)·{r_or_d}) = {s:.4f}"}
    if kind == "geometric":
        terms = [a * (r_or_d ** i) for i in range(n)]
        if abs(r_or_d - 1) < 1e-12:
            s = a * n
        else:
            s = a * (1 - r_or_d**n) / (1 - r_or_d)
        return {"valid": True, "kind": "geometric",
                "first_term": a, "ratio": r_or_d, "n": n,
                "last_term": terms[-1], "sum": round(s, 6),
                "converges": abs(r_or_d) < 1 if n > 100 else None,
                "formula": f"S = {a}·(1-{r_or_d}^{n})/(1-{r_or_d}) = {s:.4f}"}
    return {"valid": False, "error": f"Type inconnu: {kind}"}


def linear_regression(xs: List[float], ys: List[float]) -> Dict[str, Any]:
    """Régression linéaire : y = ax + b par moindres carrés."""
    if len(xs) < 2 or len(ys) < 2:
        return {"valid": False, "error": "Au moins 2 points requis"}
    if len(xs) != len(ys):
        return {"valid": False, "error": "xs et ys doivent avoir la même longueur"}
    n = len(xs)
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x*x for x in xs)
    sxy = sum(x*y for x, y in zip(xs, ys))
    det = n * sxx - sx * sx
    if abs(det) < 1e-12:
        return {"valid": False, "error": "Points colinéaires verticalement"}
    a = (n * sxy - sx * sy) / det
    b = (sxx * sy - sx * sxy) / det
    # Coefficient de corrélation
    syy = sum(y*y for y in ys)
    r_num = n * sxy - sx * sy
    r_den = math.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy))
    r = r_num / r_den if r_den != 0 else 0
    return {
        "valid": True, "a": round(a, 6), "b": round(b, 6),
        "equation": f"y = {a:.4f}x + {b:.4f}",
        "r_squared": round(r**2, 6), "r": round(r, 6),
        "n": n, "x_mean": round(sx/n, 4), "y_mean": round(sy/n, 4),
    }


def root_newton(expr: str, x0: float, tol: float = 1e-8, max_iter: int = 100) -> Dict[str, Any]:
    """Recherche de racine par méthode de Newton-Raphson.

    Expr est une expression Python valide en x.
    Utilise f'(x) ≈ (f(x+h)-f(x-h))/(2h).
    """
    h = 1e-6
    x = x0
    iterations = []
    for i in range(max_iter):
        fx = eval(expr, {"x": x, "math": math, "e": math.e, "pi": math.pi})
        fpx = (eval(expr, {"x": x+h, "math": math, "e": math.e, "pi": math.pi}) -
               eval(expr, {"x": x-h, "math": math, "e": math.e, "pi": math.pi})) / (2*h)
        if abs(fpx) < 1e-15:
            return {"valid": False, "error": "Dérivée nulle", "x": x, "iterations": i}
        x_new = x - fx / fpx
        iterations.append({"iter": i+1, "x": round(x, 8), "f(x)": round(fx, 8), "f'(x)": round(fpx, 8)})
        if abs(x_new - x) < tol:
            fx_final = eval(expr, {"x": x_new, "math": math, "e": math.e, "pi": math.pi})
            return {"valid": True, "root": round(x_new, 8), "f(root)": round(fx_final, 8),
                    "iterations": i+1, "converged": True, "history": iterations[:10]}
        x = x_new
    return {"valid": True, "root": round(x, 8), "iterations": max_iter, "converged": False}


# ═══════════════════════════════════════════════════════════════
# 6. PHYSIQUE — OUTILS AVANCÉS
# ═══════════════════════════════════════════════════════════════

def coulomb_law(q1: float, q2: float, r: float, k: float = 8.988e9) -> Dict[str, Any]:
    """Loi de Coulomb : F = k·|q1·q2|/r²."""
    F = k * abs(q1 * q2) / (r * r)
    return {
        "valid": True, "q1": q1, "q2": q2, "r": r, "k": k,
        "force": round(F, 6), "unit": "N",
        "attractive": (q1 * q2) < 0,
        "formula": f"F = {k:.4e}·|{q1}·{q2}|/{r}² = {F:.4e} N",
    }


def lens_formula(f: Optional[float] = None, u: Optional[float] = None,
                 v: Optional[float] = None) -> Dict[str, Any]:
    """Formule des lentilles : 1/f = 1/u + 1/v. Donne 2 valeurs, calcule la 3e."""
    given = sum(1 for x in (f, u, v) if x is not None)
    if given < 2:
        return {"valid": False, "error": "Donne au moins 2 valeurs sur 3"}
    try:
        if f is None:
            f_calc = 1 / (1/u + 1/v) if u != 0 and v != 0 else None
            return {"valid": True, "f": round(f_calc, 6), "u": u, "v": v,
                    "magnification": round(-v/u, 4) if u else None}
        if u is None:
            u_calc = 1 / (1/f - 1/v) if v != 0 and f != 0 else None
            return {"valid": True, "f": f, "u": round(u_calc, 6), "v": v,
                    "magnification": round(-v/u_calc, 4) if u_calc and v else None}
        if v is None:
            v_calc = 1 / (1/f - 1/u) if u != 0 and f != 0 else None
            return {"valid": True, "f": f, "u": u, "v": round(v_calc, 6),
                    "magnification": round(-v_calc/u, 4) if v_calc else None}
    except (ZeroDivisionError, ValueError) as e:
        return {"valid": False, "error": str(e)}
    return {"valid": False, "error": "Valeurs incohérentes"}


def doppler_effect(f: float, v: float = 343, v_observer: float = 0,
                   v_source: float = 0, source_approaches: bool = True) -> Dict[str, Any]:
    """Effet Doppler : f' = f·(v ± v₀)/(v ± vₛ).

    Args:
        f: Fréquence source (Hz)
        v: Vitesse du son dans le milieu (m/s, défaut=343 air)
        v_observer: Vitesse de l'observateur (m/s)
        v_source: Vitesse de la source (m/s)
        source_approaches: True si la source s'approche, False si elle s'éloigne
    """
    sign = -1 if source_approaches else 1
    f_prime = f * (v + v_observer) / (v - sign * v_source)
    return {
        "valid": True, "f_source": f, "f_observed": round(f_prime, 4), "unit": "Hz",
        "v_sound": v, "v_observer": v_observer, "v_source": v_source,
        "source_approaches": source_approaches,
        "delta_f": round(f_prime - f, 4),
        "formula": f"f' = {f}·({v}+{v_observer})/({v}{'-' if source_approaches else '+'}{v_source}) = {f_prime:.2f} Hz",
    }


def hooke_law(k: float, x: float) -> Dict[str, Any]:
    """Loi de Hooke : F = -kx, énergie élastique E = ½kx²."""
    F = -k * x
    E = 0.5 * k * x * x
    return {
        "valid": True, "k": k, "k_unit": "N/m", "x": x, "x_unit": "m",
        "force": round(F, 6), "force_unit": "N",
        "elastic_energy": round(E, 6), "energy_unit": "J",
        "formula": f"F = -{k}·{x} = {F:.4f} N, E = ½·{k}·{x}² = {E:.4f} J",
    }


def specific_heat(mass: float, c: float, delta_T: float) -> Dict[str, Any]:
    """Chaleur massique : Q = mcΔT."""
    Q = mass * c * delta_T
    return {
        "valid": True, "mass": mass, "mass_unit": "kg",
        "specific_heat": c, "c_unit": "J/(kg·K)",
        "delta_T": delta_T, "delta_T_unit": "K",
        "heat": round(Q, 4), "heat_unit": "J",
        "formula": f"Q = {mass}·{c}·{delta_T} = {Q:.4f} J",
    }


def radioactive_decay(N0: float, half_life: Optional[float] = None,
                      decay_const: Optional[float] = None, t: Optional[float] = None) -> Dict[str, Any]:
    """Décroissance radioactive : N = N₀·e^(-λt).

    Donne t¹/² ou λ, calcule l'autre : λ = ln(2)/t¹/².
    Si t donné, calcule N restant.
    """
    if half_life is not None and decay_const is None:
        lam = math.log(2) / half_life
        return {"valid": True, "N0": N0, "half_life": half_life,
                "decay_const": round(lam, 6), "unit": "s⁻¹",
                "formula": f"λ = ln(2)/{half_life} = {lam:.6f} s⁻¹"}
    if decay_const is not None and half_life is None:
        hl = math.log(2) / decay_const
        return {"valid": True, "N0": N0, "half_life": round(hl, 6), "unit": "s",
                "decay_const": decay_const,
                "formula": f"t¹/² = ln(2)/{decay_const} = {hl:.4f} s"}
    lam = decay_const if decay_const else math.log(2) / half_life if half_life else 0.0
    if lam == 0:
        return {"valid": False, "error": "Donne t¹/² ou λ"}
    if t is not None:
        N = N0 * math.exp(-lam * t)
        return {"valid": True, "N0": N0, "N": round(N, 4),
                "t": t, "decay_const": lam, "half_life": round(math.log(2)/lam, 4),
                "fraction_remaining": round(N/N0, 6),
                "formula": f"N = {N0}·e^(-{lam:.4f}·{t}) = {N:.4f}"}
    return {"valid": True, "N0": N0, "decay_const": lam, "half_life": round(math.log(2)/lam, 4)}


def hydrostatic_pressure(density: float, depth: float, g: float = 9.81) -> Dict[str, Any]:
    """Pression hydrostatique : P = ρgh."""
    P = density * g * depth
    return {
        "valid": True, "density": density, "density_unit": "kg/m³",
        "depth": depth, "depth_unit": "m", "g": g,
        "pressure": round(P, 4), "pressure_unit": "Pa",
        "pressure_atm": round(P / 101325, 4), "atm_unit": "atm",
        "formula": f"P = {density}·{g}·{depth} = {P:.2f} Pa = {P/101325:.4f} atm",
    }


# ═══════════════════════════════════════════════════════════════
# 7. CHIMIE — OUTILS AVANCÉS
# ═══════════════════════════════════════════════════════════════

def half_life(decay_const: Optional[float] = None, t_half: Optional[float] = None) -> Dict[str, Any]:
    """Demi-vie : t¹/² = ln(2)/λ. Donne 1 valeur, calcule l'autre."""
    if decay_const is not None and t_half is None:
        if decay_const <= 0:
            return {"valid": False, "error": "λ doit être > 0"}
        th = math.log(2) / decay_const
        return {"valid": True, "decay_const": decay_const, "half_life": round(th, 6), "unit": "s"}
    if t_half is not None and decay_const is None:
        if t_half <= 0:
            return {"valid": False, "error": "t¹/² doit être > 0"}
        lam = math.log(2) / t_half
        return {"valid": True, "half_life": t_half, "decay_const": round(lam, 6), "unit": "s⁻¹"}
    return {"valid": False, "error": "Donne λ OU t¹/²"}


def nernst_equation(E0: float, n: int, Q: float, T: float = 298.15,
                    R: float = 8.314, F: float = 96485) -> Dict[str, Any]:
    """Équation de Nernst : E = E° - (RT/nF)·ln(Q)."""
    E = E0 - (R * T / (n * F)) * math.log(Q)
    return {
        "valid": True, "E0": E0, "n": n, "Q": Q, "T": T,
        "E": round(E, 6), "unit": "V",
        "RT_nF": round(R * T / (n * F), 6),
        "formula": f"E = {E0} - ({R}·{T}/({n}·{F}))·ln({Q}) = {E:.4f} V",
    }


def empirical_formula(elements: Dict[str, float]) -> Dict[str, Any]:
    """Formule empirique à partir de pourcentages massiques.

    Args:
        elements: Dict {symbole: pourcentage_massique}
        Ex: {'C': 40.0, 'H': 6.67, 'O': 53.33}
    """
    if not elements:
        return {"valid": False, "error": "Aucun élément"}
    # Vérifier que la somme ≈ 100%
    total = sum(elements.values())
    if abs(total - 100) > 1:
        return {"valid": False, "error": f"La somme des pourcentages ({total:.2f}%) devrait être ≈ 100%"}

    moles = {}
    for element, pct in elements.items():
        if element not in ATOMIC_MASSES:
            return {"valid": False, "error": f"Élément inconnu: {element}"}
        moles[element] = pct / ATOMIC_MASSES[element]

    # Normaliser au plus petit
    min_moles = min(moles.values())
    if min_moles <= 0:
        return {"valid": False, "error": "Pourcentage nul"}
    ratios = {el: m / min_moles for el, m in moles.items()}

    # Arrondir aux entiers les plus proches
    integers = {}
    for el, ratio in ratios.items():
        integers[el] = max(1, round(ratio))

    # Simplifier
    from math import gcd
    g = 0
    for v in integers.values():
        g = gcd(g, v) if g else v
    if g > 1:
        integers = {el: v // g for el, v in integers.items()}

    formula = "".join(f"{el}{integers[el]}" if integers[el] > 1 else el for el in sorted(integers))
    mw = sum(ATOMIC_MASSES[el] * cnt for el, cnt in integers.items())

    return {
        "valid": True, "elements": elements,
        "ratios": {el: round(r, 4) for el, r in ratios.items()},
        "empirical_formula": formula,
        "molecular_weight": round(mw, 4),
        "details": [{"element": el, "mass_pct": elements[el], "moles": round(moles[el], 4),
                     "ratio": round(ratios[el], 4), "atoms": integers[el]} for el in sorted(integers)],
    }


def yield_calculator(theoretical: float, actual: Optional[float] = None,
                     percent: Optional[float] = None) -> Dict[str, Any]:
    """Calcul de rendement : rendement% = (réel/théorique)×100.

    Donne 2 valeurs, calcule la 3e.
    """
    given = sum(1 for v in (theoretical, actual, percent) if v is not None)
    if given < 2 or theoretical is None or theoretical <= 0:
        return {"valid": False, "error": "Donne au moins 2 valeurs (dont théorique > 0)"}
    if actual is not None and percent is None:
        p = actual / theoretical * 100
        return {"valid": True, "theoretical": theoretical, "actual": actual,
                "percent_yield": round(p, 2), "unit": "%"}
    if percent is not None and actual is None:
        a = theoretical * percent / 100
        return {"valid": True, "theoretical": theoretical, "actual": round(a, 6),
                "percent_yield": percent, "unit": "%"}
    return {"valid": False, "error": "Valeurs incohérentes"}


def molarity_molality(moles: float, volume_L: Optional[float] = None,
                      mass_kg: Optional[float] = None) -> Dict[str, Any]:
    """Convertit molarité (M = mol/L) et molalité (m = mol/kg).

    Donne m ou V, calcule l'autre.
    """
    if volume_L is not None:
        M = moles / volume_L
        return {"valid": True, "moles": moles, "volume_L": volume_L,
                "molarity": round(M, 6), "M_unit": "mol/L"}
    if mass_kg is not None:
        m = moles / mass_kg
        return {"valid": True, "moles": moles, "mass_kg": mass_kg,
                "molality": round(m, 6), "m_unit": "mol/kg"}
    return {"valid": False, "error": "Donne volume_L ou mass_kg"}


def combustion_analysis(mass_sample: float, mass_CO2: float, mass_H2O: float) -> Dict[str, Any]:
    """Analyse élémentaire par combustion : CxHy + O₂ → CO₂ + H₂O.

    Calcule la formule empirique à partir des masses de CO₂ et H₂O produites.
    """
    # Moles de C et H
    moles_C = mass_CO2 / 44.009  # M_CO2 = 44.009 g/mol
    moles_H = 2 * mass_H2O / 18.015  # M_H2O = 18.015 g/mol

    mass_C = moles_C * 12.011
    mass_H = moles_H * 1.008
    mass_O = mass_sample - mass_C - mass_H

    elements = {}
    elements["C"] = mass_C / mass_sample * 100
    elements["H"] = mass_H / mass_sample * 100
    if mass_O > 0:
        elements["O"] = mass_O / mass_sample * 100

    # Formule empirique
    moles = {"C": moles_C, "H": moles_H}
    if mass_O > 0:
        moles["O"] = mass_O / 15.999

    min_mol = min(moles.values())
    if min_mol <= 0:
        return {"valid": False, "error": "Masse nulle"}
    ratios = {el: m / min_mol for el, m in moles.items()}
    integers = {el: max(1, round(r)) for el, r in ratios.items()}

    from math import gcd
    g = 0
    for v in integers.values():
        g = gcd(g, v) if g else v
    if g > 1:
        integers = {el: v // g for el, v in integers.items()}

    formula = "".join(f"{el}{integers[el]}" if integers[el] > 1 else el for el in sorted(integers))

    return {
        "valid": True, "mass_sample": mass_sample,
        "mass_CO2": mass_CO2, "mass_H2O": mass_H2O,
        "mass_C": round(mass_C, 4), "mass_H": round(mass_H, 4), "mass_O": round(mass_O, 4),
        "percentages": {el: round(pct, 2) for el, pct in elements.items()},
        "empirical_formula": formula,
        "moles": {el: round(m, 6) for el, m in moles.items()},
    }


# ═══════════════════════════════════════════════════════════════
# 8. OUTILS CROSS-DOMAIN AVANCÉS
# ═══════════════════════════════════════════════════════════════

def dimensional_analysis(equation: str) -> Dict[str, Any]:
    """Analyse dimensionnelle basique d'une équation.

    Vérifie la cohérence des dimensions et identifie le type de loi.
    """
    eq = equation.strip()
    lhs, rhs = eq.split("=", 1) if "=" in eq else ("?", eq)
    lhs = lhs.strip()
    rhs = rhs.strip()

    # Dictionnaire de dimensions basiques pour symboles communs
    dims = {
        # Longueur
        "L": "L", "l": "L", "d": "L", "h": "L", "r": "L", "x": "L", "y": "L", "z": "L",
        "Δx": "L", "Δy": "L", "Δz": "L", "λ": "L",
        # Temps
        "t": "T", "τ": "T", "T": "Θ",  # T peut être temps OU température
        "Δt": "T", "f": "T⁻¹",
        # Masse
        "m": "M", "M": "M",
        # Quantité de matière
        "n": "N", "C": "N·L⁻³", "[": "N·L⁻³",
        # Force
        "F": "M·L·T⁻²", "P": "M·L⁻¹·T⁻²",
        # Énergie
        "E": "M·L²·T⁻²", "W": "M·L²·T⁻²", "Q": "M·L²·T⁻²",
        # Vitesse
        "v": "L·T⁻¹", "c": "L·T⁻¹", "u": "L·T⁻¹",
        # Accélération
        "a": "L·T⁻²", "g": "L·T⁻²",
        # Densité
        "ρ": "M·L⁻³",
        # Surface
        "A": "L²", "S": "L²",
        # Volume
        "V": "L³",
        # Pression
        "p": "M·L⁻¹·T⁻²",
        # Courant
        "I": "I",
        # Température
        "θ": "Θ", "Θ": "Θ", "ΔT": "Θ",
        # Constantes
        "k": "?",
        "R": "M·L²·T⁻²·Θ⁻¹·N⁻¹",  # gaz parfaits
        "G": "M⁻¹·L³·T⁻²",  # gravitation
        "h": "M·L²·T⁻¹",  # Planck
        "k_B": "M·L²·T⁻²·Θ⁻¹",  # Boltzmann
        "ε₀": "M⁻¹·L⁻³·T⁴·I²",  # permittivité
        "μ₀": "M·L·T⁻²·I⁻²",  # perméabilité
    }

    # Extraire les symboles
    symbols = set(re.findall(r'[A-Za-z_α-ωΑ-Ω][A-Za-z0-9_α-ωΑ-Ω]*', rhs))
    reserved = {"exp", "log", "sin", "cos", "tan", "sqrt", "abs", "math", "pi", "e"}
    symbols = [s for s in symbols if s not in reserved and len(s) <= 4]

    found_dims = {}
    unknown = []
    for s in symbols:
        if s in dims:
            found_dims[s] = dims[s]
        else:
            unknown.append(s)

    # Type de loi
    law_types = []
    if "/" in rhs:
        law_types.append("ratio")
    if "*" in rhs:
        law_types.append("multiplicative")
    if "+" in rhs or "-" in rhs:
        law_types.append("additive")
    if "**" in rhs or "^" in rhs:
        law_types.append("non-linear")
    if "exp" in rhs:
        law_types.append("exponential")

    return {
        "valid": True, "equation": eq,
        "lhs": lhs, "rhs": rhs,
        "symbols_found": {s: found_dims[s] for s in symbols if s in found_dims},
        "symbols_unknown": unknown,
        "law_type": " + ".join(law_types) if law_types else "unknown",
        "n_terms": rhs.count("+") + rhs.count("-") + 1,
        "has_derivative": "d/" in rhs or "∂" in rhs,
        "has_integral": "∫" in rhs,
        "structure_summary": f"{'Équation' if '=' in eq else 'Expression'} à {len(found_dims)} dimensions identifiées",
    }


def error_propagation(formula: str, variables: Dict[str, Dict[str, float]]) -> Dict[str, Any]:
    """Propagation d'incertitudes par différences finies.

    Args:
        formula: Expression Python valide (ex: 'a*b/c')
        variables: Dict {nom: {'value': val, 'error': err}}
    """
    import copy
    names = list(variables.keys())
    values = {n: variables[n]["value"] for n in names}
    errors = {n: variables[n]["error"] for n in names}

    try:
        # Valeur centrale
        f0 = eval(formula, {"math": math, "e": math.e, "pi": math.pi}, values)
    except Exception as e:
        return {"valid": False, "error": f"Évaluation: {e}"}

    # Propagation par différences finies
    contributions = {}
    h = 1e-6
    for name in names:
        v_plus = dict(values)
        v_plus[name] += h
        v_minus = dict(values)
        v_minus[name] -= h
        try:
            f_plus = eval(formula, {"math": math, "e": math.e, "pi": math.pi}, v_plus)
            f_minus = eval(formula, {"math": math, "e": math.e, "pi": math.pi}, v_minus)
            deriv = (f_plus - f_minus) / (2 * h)
            contributions[name] = {
                "partial_derivative": round(deriv, 6),
                "error_contribution": round(abs(deriv * errors[name]), 6),
            }
        except Exception:
            contributions[name] = {"partial_derivative": None, "error_contribution": None}

    # Incertitude totale (somme quadratique)
    total_error = math.sqrt(sum(
        c["error_contribution"]**2 for c in contributions.values()
        if c["error_contribution"] is not None
    ))

    return {
        "valid": True, "formula": formula,
        "value": round(f0, 6), "error": round(total_error, 6),
        "relative_error": round(abs(total_error / f0 * 100), 4) if f0 != 0 else None,
        "contributions": contributions,
        "result": f"{f0:.4f} ± {total_error:.4f}",
    }


# ═══════════════════════════════════════════════════════════════
# 5. TOOLBOX — REGISTRE DE TOUS LES OUTILS
# ═══════════════════════════════════════════════════════════════

TOOL_REGISTRY = {
    # Math
    "solve_quadratic": {"fn": solve_quadratic, "domain": "math", "description": "Résout ax²+bx+c=0"},
    "solve_linear_system": {"fn": solve_linear_system_2x2, "domain": "math", "description": "Système 2x2"},
    "derivative_numeric": {"fn": derivative_numeric, "domain": "math", "description": "Dérivée numérique f'(x0)"},
    "integral_numeric": {"fn": integral_numeric, "domain": "math", "description": "Intégrale numérique ∫f(x)dx"},
    "taylor_series": {"fn": taylor_series, "domain": "math", "description": "Série de Taylor autour de x0"},
    "matrix_operations": {"fn": matrix_operations, "domain": "math", "description": "det, trace, transpose, inverse"},
    "statistics_basic": {"fn": statistics_basic, "domain": "math", "description": "Moyenne, médiane, variance, std"},
    "interpolation_linear": {"fn": interpolation_linear, "domain": "math", "description": "Interpolation linéaire"},
    "vector_operations": {"fn": vector_operations, "domain": "math", "description": "Norme, produit scalaire, vectoriel, angle"},
    "complex_operations": {"fn": complex_operations, "domain": "math", "description": "Nombres complexes : module, conjugué, +, -, ×, ÷"},
    "prime_factors": {"fn": prime_factors, "domain": "math", "description": "Factorisation en nombres premiers"},
    "combinatorics": {"fn": combinatorics, "domain": "math", "description": "Factorielle, binomial, permutations, triangle Pascal"},
    "series_sum": {"fn": series_sum, "domain": "math", "description": "Somme de série arithmétique ou géométrique"},
    "linear_regression": {"fn": linear_regression, "domain": "math", "description": "Régression linéaire y=ax+b, R²"},
    "root_newton": {"fn": root_newton, "domain": "math", "description": "Recherche de racine par Newton-Raphson"},
    # Physics
    "kinematics_free_fall": {"fn": kinematics_free_fall, "domain": "physics", "description": "Chute libre : t, v, h"},
    "kinematics_projectile": {"fn": kinematics_projectile, "domain": "physics", "description": "Mouvement parabolique"},
    "energy_kinetic": {"fn": energy_kinetic, "domain": "physics", "description": "Énergie cinétique Ek=½mv²"},
    "energy_potential": {"fn": energy_potential, "domain": "physics", "description": "Énergie potentielle Ep=mgh"},
    "ohms_law": {"fn": ohms_law, "domain": "physics", "description": "Loi d'Ohm U=RI"},
    "wave_properties": {"fn": wave_properties, "domain": "physics", "description": "Relation onde v=λf"},
    "gas_law_ideal": {"fn": gas_law_ideal, "domain": "physics", "description": "Gaz parfaits PV=nRT"},
    "coulomb_law": {"fn": coulomb_law, "domain": "physics", "description": "Loi de Coulomb F=k·|q1·q2|/r²"},
    "lens_formula": {"fn": lens_formula, "domain": "physics", "description": "Formule des lentilles 1/f=1/u+1/v"},
    "doppler_effect": {"fn": doppler_effect, "domain": "physics", "description": "Effet Doppler f'=f·(v±v₀)/(v±vₛ)"},
    "hooke_law": {"fn": hooke_law, "domain": "physics", "description": "Loi de Hooke F=-kx, énergie élastique"},
    "specific_heat": {"fn": specific_heat, "domain": "physics", "description": "Chaleur massique Q=mcΔT"},
    "radioactive_decay": {"fn": radioactive_decay, "domain": "physics", "description": "Décroissance radioactive N=N₀·e^(-λt)"},
    "hydrostatic_pressure": {"fn": hydrostatic_pressure, "domain": "physics", "description": "Pression hydrostatique P=ρgh"},
    # Chemistry
    "molecular_weight": {"fn": molecular_weight, "domain": "chemistry", "description": "Masse molaire d'une formule"},
    "concentration_dilution": {"fn": concentration_dilution, "domain": "chemistry", "description": "Dilution C1V1=C2V2"},
    "ph_calculator": {"fn": ph_calculator, "domain": "chemistry", "description": "pH ↔ [H+]"},
    "reaction_rate": {"fn": reaction_rate, "domain": "chemistry", "description": "Loi de vitesse v=kΠ[C]^order"},
    "equilibrium_constant": {"fn": equilibrium_constant, "domain": "chemistry", "description": "Constante d'équilibre K"},
    "arrhenius": {"fn": arrhenius, "domain": "chemistry", "description": "Loi d'Arrhenius k=A·exp(-Ea/RT)"},
    "half_life": {"fn": half_life, "domain": "chemistry", "description": "Demi-vie t¹/²=ln(2)/λ"},
    "nernst_equation": {"fn": nernst_equation, "domain": "chemistry", "description": "Équation de Nernst E=E°-RT/nF·ln(Q)"},
    "empirical_formula": {"fn": empirical_formula, "domain": "chemistry", "description": "Formule empirique depuis % massiques"},
    "yield_calculator": {"fn": yield_calculator, "domain": "chemistry", "description": "Rendement théorique/réel/pourcent"},
    "molarity_molality": {"fn": molarity_molality, "domain": "chemistry", "description": "Molarité ↔ molalité"},
    "combustion_analysis": {"fn": combustion_analysis, "domain": "chemistry", "description": "Analyse par combustion CO₂/H₂O"},
    # Cross-domain
    "unit_converter": {"fn": unit_converter, "domain": "general", "description": "Conversion d'unités"},
    "scientific_notation": {"fn": scientific_notation, "domain": "general", "description": "Notation scientifique"},
    "significant_figures": {"fn": significant_figures, "domain": "general", "description": "Chiffres significatifs"},
    "percentage_calculator": {"fn": percentage_calculator, "domain": "general", "description": "Calculs de pourcentage"},
    "equation_parser": {"fn": equation_parser, "domain": "general", "description": "Analyse structurelle d'équation"},
    "dimensional_analysis": {"fn": dimensional_analysis, "domain": "general", "description": "Analyse dimensionnelle d'équation"},
    "error_propagation": {"fn": error_propagation, "domain": "general", "description": "Propagation d'incertitudes"},
}


def list_tools(domain: Optional[str] = None) -> List[Dict[str, str]]:
    """Liste tous les outils disponibles, optionnellement filtrés par domaine."""
    result = []
    for name, info in TOOL_REGISTRY.items():
        if domain and info["domain"] != domain:
            continue
        result.append({"name": name, "domain": info["domain"], "description": info["description"]})
    return result


def run_tool(name: str, **kwargs) -> Dict[str, Any]:
    """Exécute un outil par son nom avec les arguments donnés.

    Ex: run_tool('solve_quadratic', a=1, b=-3, c=2)
    """
    if name not in TOOL_REGISTRY:
        return {"valid": False, "error": f"Outil inconnu: {name}. Utilise list_tools() pour voir les outils."}
    try:
        return TOOL_REGISTRY[name]["fn"](**kwargs)
    except Exception as e:
        return {"valid": False, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    # Test rapide
    print("=== OUTILS DISPONIBLES ===")
    for t in list_tools():
        print(f"  [{t['domain']:10s}] {t['name']:25s} — {t['description']}")

    print("\n=== TESTS ===")
    print(solve_quadratic(1, -3, 2))
    print(kinematics_free_fall(10))
    print(molecular_weight("H2O"))
    print(ohms_law(voltage=12, resistance=4))
    print(ph_calculator(H_conc=1e-7))
    print(equation_parser("Ndot = k * A * B / (1 + R)"))
    print(unit_converter(100, "cm", "m"))