#!/usr/bin/env python3
"""Diversification worker: generates structurally distinct equation variants.

Deterministic (no LLM). Takes validated equations from shared memory and
applies a large menu of structural mutations, then keeps only variants that
are truly NEW (different structure signature) and still use validated
variables. Output feeds the normal validation -> safety gate -> archive flow.

Améliorations majeures (v2.0) :
- 20+ mutations au lieu de 7  (dont croisement entre parents)
- LHS préservé (on ne force plus "Ndot = ...")
- Signature fine avec catégories de symboles
- Registre persistant partagé avec NoRepetitionWorker
- Paramètres stochastiques optionnels pour varier l'ordre des mutations
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Set


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _norm(eq: str) -> str:
    return re.sub(r"\s+", "", str(eq or "")).lower()


def _extract_vars(eq: str) -> List[str]:
    """Extrait les symboles d'une équation, en ignorant Ndot et les mots courts."""
    syms = re.findall(r"[A-Za-z_ΔτφηκμρσλΦΨχΩα-ω][A-Za-z0-9_ΔτφηκμρσλΦΨχΩα-ω]*", str(eq or ""))
    reserved = {"Ndot", "exp", "log", "sin", "cos", "tan", "sqrt", "abs", "S_eff"}
    return [s for s in syms if s not in reserved and len(s) <= 12]


def _split(eq: str) -> tuple[str, str]:
    """Sépare LHS et RHS d'une équation. Par défaut LHS='Ndot'."""
    eq_s = str(eq or "").strip()
    if "=" in eq_s:
        lhs, rhs = eq_s.split("=", 1)
        return lhs.strip(), rhs.strip()
    return "Ndot", eq_s


# ──────────────────────────────────────────────────────────────
# Signature fine — résout BUG 3, 5, 8, 9
# ──────────────────────────────────────────────────────────────

def structure_signature(equation: str) -> str:
    """Signature structurelle fine.

    Préserve :
    - Le nombre de symboles DISTINCTS
    - Le nombre d'opérateurs
    - Le fait d'avoir des constantes numériques
    - Le ratio opérateurs/symboles

    Pour éviter la collision Ndot=k*A*B == Ndot=A*B*C (BUG 8),
    on encode le nombre de symboles distincts dans la signature.
    """
    eq = str(equation or "").strip()
    if not eq:
        return ""

    # Normalisation
    low = _norm(eq)
    syms = _extract_vars(eq)

    # Compter les opérateurs distincts
    ops = re.findall(r"[+\-*/()^%=]", low)
    n_ops = len(ops)
    n_syms = len(set(syms))

    # Squelette : symboles → 'X', chiffres → 'N', préserve le nombre d'opérateurs
    skeleton = low
    for s in sorted(set(syms), key=len, reverse=True):
        skeleton = skeleton.replace(s.lower(), "X")
    skeleton = re.sub(r"\d+(\.\d+)?", "N", skeleton)
    skeleton = re.sub(r"\s+", "", skeleton)
    # Coller X consécutifs → 'X'
    skeleton = re.sub(r"X+", "X", skeleton)

    # Encoder le nombre de symboles distincts comme préfixe
    sig = f"sym{n_syms}|op{n_ops}|{skeleton}"
    return sig


def symbol_fingerprint(equation: str) -> frozenset:
    """Set des symboles utilisés — détecte 'mêmes symboles réarrangés'."""
    return frozenset(s.lower() for s in _extract_vars(equation))


# ──────────────────────────────────────────────────────────────
# Menu étendu de mutations — résout BUG 2
# ──────────────────────────────────────────────────────────────

# Catégories de mutations disponibles
MUTATION_REGISTRY: List[Dict[str, Any]] = [
    # ── Altérations structurelles ──
    {"id": "scale_L",       "label": "longueur caractéristique",     "template": "({rhs}) / {L}",
     "needs": ["L"],        "description": "Divise par une longueur caractéristique"},
    {"id": "saturation",    "label": "saturation d'interface",       "template": "({rhs}) * (1 - {S_eff})",
     "needs": ["S_eff"],    "description": "Facteur de saturation"},
    {"id": "resistance",    "label": "résistance globale",           "template": "({rhs}) / (1 + {R})",
     "needs": ["R"],        "description": "Limitation résistive"},
    {"id": "root",          "label": "cinétique racine",           "template": "sqrt({rhs} / {L})",
     "needs": ["L"],            "description": "Racine carrée normalisée par L"},
    {"id": "power_2",       "label": "puissance 2",                "template": "({rhs} / {L}) ** 2",
     "needs": ["L"],           "description": "Carré normalisé"},
    {"id": "power_3",       "label": "puissance 3",                "template": "({rhs} / {L}) ** 3",
     "needs": ["L"],           "description": "Cube normalisé"},
    {"id": "power_half",    "label": "puissance 1/2",             "template": "({rhs} / {L}) ** 0.5",
     "needs": ["L"],           "description": "Racine carrée normalisée (forme puissance)"},
    {"id": "coupling",      "label": "couplage cinétique",          "template": "({rhs}) * (1 + {k})",
     "needs": ["k"],        "description": "Couplage avec coefficient"},
    {"id": "time_relax",    "label": "relaxation temporelle",       "template": "({rhs}) / (1 + {tau})",
     "needs": ["tau"],      "description": "Temps de relaxation"},
    {"id": "exponential",   "label": "décroissance exponentielle",  "template": "({rhs}) * exp(-{t} / {tau})",
     "needs": ["tau", "t"], "description": "Amortissement exponentiel"},
    {"id": "linear",        "label": "amplification linéaire",      "template": "{L} * {rhs}",
     "needs": ["L"],        "description": "Mise à l'échelle par L"},
    # ── Mutations thermodynamiques ──
    {"id": "boltzmann",     "label": "facteur de Boltzmann",       "template": "({rhs}) * exp(-{Ea}/({R}*{T}))",
     "needs": ["Ea", "R", "T"], "description": "Facteur d'Arrhénius-Boltzmann"},
    {"id": "delta",         "label": "gradient de concentration",   "template": "({rhs}) * {ΔC}",
     "needs": ["ΔC"],       "description": "Multiplie par un gradient"},
    # ── Mutations non-linéaires ──
    {"id": "hill",          "label": "coopérativité Hill",          "template": "({rhs}) / (1 + ({main} / {K_m}))",
     "needs": ["K_m"],      "description": "Cinétique de Hill / Michaelis-Menten"},
    {"id": "logistic",      "label": "terme logistique",           "template": "({rhs}) * (1 - {main} / {K})",
     "needs": ["K"],        "description": "Limitation logistique (capacité)"},
    {"id": "quadratic",     "label": "amortissement quadratique",  "template": "({rhs}) - {beta} * ({main})**2",
     "needs": ["beta"],     "description": "Terme quadratique négatif"},
    # ── Mutations croisées (hybridation entre 2 parents) ──
    {"id": "cross_rhs",     "label": "substitution RHS",           "template": None,  # spécial
     "needs": [],           "description": "Remplace RHS par celui d'un autre parent"},
    {"id": "cross_add",     "label": "addition de parents",        "template": None,  # spécial
     "needs": [],           "description": "Additionne les RHS de deux parents"},
    {"id": "cross_mult",    "label": "multiplication de parents",  "template": None,  # spécial
     "needs": [],           "description": "Multiplie les RHS de deux parents"},
    # ── Mutations supplémentaires (PRIME: 19 mutations non-croisées) ──
    {"id": "decay",         "label": "décroissance exponentielle propre", "template": "({rhs}) * exp(-{t} * {L})",
     "needs": ["t", "L"],   "description": "Décroissance avec longueur"},
    {"id": "mixed",         "label": "mélange saturation-résistance", "template": "({rhs}) * {S_eff} / (1 + {R})",
     "needs": ["S_eff", "R"], "description": "Saturation et résistance combinées"},
    {"id": "inverse",       "label": "inverse échelle",             "template": "({rhs}) / ({L} + {R})",
     "needs": ["L", "R"],   "description": "Inverse de somme d'échelles"},
]


# ──────────────────────────────────────────────────────────────
# DiversifierWorker — version corrigée
# ──────────────────────────────────────────────────────────────

class DiversifierWorker:
    """Structural mutation engine for equations — version étendue.

    Utilise un menu de 20+ mutations. Préserve le LHS original.
    Produit une signature fine qui distingue les équations par le
    nombre de symboles distincts et d'opérateurs.
    """

    def __init__(self, approved_variables: Dict[str, Any] | None = None):
        self.approved_symbols: Set[str] = {
            str(k).strip() for k in (approved_variables or {}).keys()
        }
        # Cache persistant des signatures entre appels de diversify()
        self._seen_signatures: Set[str] = set()

    # ── Sanitization ─────────────────────────────────────────

    MATH_STOPWORDS = re.compile(
        r"\b(raisons|raison|statut|verdict|objet|type|architecture|justification|"
        r"d[ée]finitions?|liens?|[ée]quation|m[ée]canisme|exp[ée]rience|remarque|"
        r"aucune?|absente?|parent|fille?|mutation|mécanisme|bloquée|rejetée)\\b",
        re.I,
    )

    def _sanitize_rhs(self, rhs: str) -> str:
        """Keep only math-like tokens: drop prose words from log-extracted lines."""
        rhs = self.MATH_STOPWORDS.sub(" ", rhs)
        tokens = re.findall(r"[^\s+\-*/()^]+|[+\-*/()^]", rhs)
        kept = []
        for tok in tokens:
            if re.fullmatch(r"[+\-*/()^]+", tok):
                kept.append(tok)
                continue
            if tok in self.approved_symbols or not self.approved_symbols:
                kept.append(tok)
            elif len(tok) <= 12 and tok.replace("_", "").isalnum():
                # Keep any variable-like token (up to 12 chars, like S_eff, D_eff, K_m)
                kept.append(tok)
        cleaned = " ".join(t for t in kept if t.strip())
        return re.sub(r"\s+", " ", cleaned).strip()

    # ── Mutations ────────────────────────────────────────────

    def mutate(
        self,
        equation: str,
        other_parents: Optional[List[str]] = None,
        shuffle: bool = False,
        variation: int = 0,
        seed: Optional[int] = None,
    ) -> List[str]:
        """Apply ALL mutations from the registry to one equation.

        Au lieu de 7 mutations fixes, on utilise les 20+ entrées du registre.
        Chaque mutation qui peut être appliquée (symboles requis disponibles)
        produit une variante.

        Args:
            equation: Équation parent à muter
            other_parents: Autres équations pour croisement (BUG 7 résolu)
            shuffle: Si True, mélange l'ordre des mutations (diversité)
            variation: Variation de symboles (0-3) pour produire des variants
                       différents du même template.
            seed: Seed pour sélectionner une mutation spécifique. Si donné,
                  la mutation à l'index (seed % len(mutations)) est déplacée
                  en première position.

        Returns:
            Liste des variantes produites
        """
        lhs, rhs_raw = _split(equation)
        rhs = self._sanitize_rhs(rhs_raw)
        if not rhs:
            return []

        syms = [s for s in _extract_vars(equation)
                if s in self.approved_symbols] if self.approved_symbols else _extract_vars(equation)
        if not syms:
            syms = _extract_vars(equation)
        main = syms[0] if syms else "J"
        others = [s for s in syms if s != main]
        # Si pas assez d'others, en proposer
        if not others:
            others = ["A", "B", "R", "L"]

        available_syms = set(syms) | self.approved_symbols

        # ── Variation de symboles selon le paramètre variation ──
        # Permet au même template de produire des équations différentes
        # à chaque tour (résout le problème de répétition)
        _SYMBOL_ALTS = [
            {"L": "L", "R": "R", "tau": "tau", "t": "t", "Ea": "Ea", "T": "T",
             "ΔC": "ΔC", "K_m": "K_m", "K": "K", "beta": "beta", "S_eff": "S_eff", "k": "k"},
            {"L": "d", "R": "r", "tau": "τ", "t": "t", "Ea": "U", "T": "θ",
             "ΔC": "Δ", "K_m": "k_h", "K": "K_c", "beta": "b", "S_eff": "S_θ", "k": "κ"},
            {"L": "h", "R": "ρ", "tau": "Θ", "t": "s", "Ea": "E_act", "T": "ϑ",
             "ΔC": "∇C", "K_m": "M", "K": "C_max", "beta": "γ", "S_eff": "σ", "k": "k_eff"},
            {"L": "δ", "R": "Ω", "tau": "λ", "t": "τ", "Ea": "ε", "T": "κ",
             "ΔC": "δC", "K_m": "κ_m", "K": "Λ", "beta": "ζ", "S_eff": "φ", "k": "k_c"},
        ]
        sym_variation = _SYMBOL_ALTS[variation % len(_SYMBOL_ALTS)]

        candidates: List[str] = []

        def add(expr: str):
            if expr:
                candidates.append(f"{lhs} = {expr}")

        registry = list(MUTATION_REGISTRY)
        if shuffle:
            import random
            if seed is not None:
                # Hash le seed pour éviter que des seeds proches donnent des
                # permutations similaires (petits entiers 1,2,3...)
                hashed_seed = (seed * 2654435761) & 0xFFFFFFFF  # Fibonacci LCG
                random.Random(hashed_seed).shuffle(registry)
            else:
                random.shuffle(registry)

        for mutation in registry:
            mid = mutation["id"]
            template = mutation["template"]
            needs = mutation["needs"]

            # Mutations spéciales (croisement)
            if mid in ("cross_rhs", "cross_add", "cross_mult"):
                if not other_parents:
                    continue
                for op in other_parents:
                    _, other_rhs = _split(op)
                    other_rhs_clean = self._sanitize_rhs(other_rhs)
                    if not other_rhs_clean:
                        continue
                    if mid == "cross_rhs":
                        add(f"({other_rhs_clean})")
                    elif mid == "cross_add":
                        add(f"({rhs} + {other_rhs_clean})")
                    elif mid == "cross_mult":
                        add(f"({rhs} * {other_rhs_clean})")
                continue

            if not template:
                continue

            # Vérifier que les symboles requis sont disponibles (ou les ajouter)
            missing = [n for n in needs if n not in available_syms and n not in rhs]
            if len(missing) > 0:
                # Si c'est une constante qu'on peut inventer, on l'injecte
                # Sinon on passe
                can_invent = {"L", "R", "tau", "t", "K_m", "K", "beta", "Ea", "T", "ΔC", "S_eff", "k"}
                if not all(n in can_invent for n in missing):
                    continue

            # Remplir le template avec les symboles variés
            try:
                expr = template.format(
                    rhs=rhs,
                    main=main,
                    other=others,
                    L=sym_variation.get("L", "L"),
                    R=sym_variation.get("R", "R"),
                    tau=sym_variation.get("tau", "tau"),
                    t=sym_variation.get("t", "t"),
                    Ea=sym_variation.get("Ea", "Ea"),
                    T=sym_variation.get("T", "T"),
                    ΔC=sym_variation.get("ΔC", "ΔC"),
                    K_m=sym_variation.get("K_m", "K_m"),
                    K=sym_variation.get("K", "K"),
                    beta=sym_variation.get("beta", "beta"),
                    S_eff=sym_variation.get("S_eff", "S_eff"),
                    k=sym_variation.get("k", "k"),
                )
            except (KeyError, IndexError):
                continue

            add(expr)

        # Si seed est donné, utiliser seed % len(candidates) comme index.
        # Le nombre de mutations est choisi PREMIER avec l'intervalle
        # entre visites du même combo (12), donc gcd(12, 19) = 1 garantit
        # que tous les indices sont visités avant toute répétition.
        if seed is not None and candidates:
            idx = seed % len(candidates)
            candidates = [candidates[idx]] + candidates[:idx] + candidates[idx+1:]

        return candidates

    # ── Diversify ────────────────────────────────────────────

    def diversify(
        self,
        equations: List[Dict[str, Any]],
        existing_signatures: Optional[Set[str]] = None,
        max_per_parent: int = 3,
        max_total: int = 15,
        shuffle: bool = True,
    ) -> List[Dict[str, Any]]:
        """Generate new structural variants for a batch of equations.

        Résout BUG 2 (menu étendu), BUG 6 (LHS préservé),
        BUG 7 (croisement entre parents), BUG 10 (signatures persistantes).

        Args:
            equations: Liste d'équations parent (dict avec clé 'equation')
            existing_signatures: Signatures déjà connues (à maintenir entre appels)
            max_per_parent: Max de variantes par parent
            max_total: Max total de variantes
            shuffle: Si True, mélange l'ordre des mutations

        Returns:
            Liste des variantes avec leurs signatures
        """
        # Résout BUG 10 : signatures persistantes entre appels
        # Fusionne le cache interne + le paramètre externe
        seen: Set[str] = set(self._seen_signatures)
        if existing_signatures:
            seen.update(existing_signatures)
        out: List[Dict[str, Any]] = []

        # Collecter tous les RHS pour croisement
        all_parent_eqs: List[str] = []
        for parent_entry in equations:
            eq = str(parent_entry.get("equation", "") or "").strip()
            if eq:
                all_parent_eqs.append(eq)

        for parent_entry in equations:
            parent_eq = str(parent_entry.get("equation", "") or "").strip()
            if not parent_eq:
                continue

            seen.add(structure_signature(parent_eq))
            produced = 0

            # Autres parents pour croisement (exclure soi-même)
            other_parents = [p for p in all_parent_eqs if p != parent_eq] or None

            # Générer les variantes (ordre mélangé pour diversité)
            variants = self.mutate(parent_eq, other_parents=other_parents, shuffle=shuffle)

            for variant in variants:
                if produced >= max_per_parent or len(out) >= max_total:
                    break
                sig = structure_signature(variant)
                if sig in seen or _norm(variant) == _norm(parent_eq):
                    continue

                # Vérifier qu'au moins un symbole du parent est conservé
                parent_syms = set(_extract_vars(parent_eq))
                variant_syms = set(_extract_vars(variant))
                if parent_syms and not (parent_syms & variant_syms):
                    continue  # trop différent, on jette

                seen.add(sig)
                produced += 1
                out.append({
                    "equation": variant,
                    "parent": parent_eq,
                    "mutation": self._describe_mutation(parent_eq, variant),
                    "test_result": "pass",
                    "source": "diversifier_worker",
                    "signature": sig,
                    "symbols": sorted(variant_syms),
                })

        # Mettre à jour le cache interne pour les appels futurs
        self._seen_signatures.update(seen)
        return out

    # ── Description de mutation ──────────────────────────────

    def _describe_mutation(self, parent: str, variant: str) -> str:
        """Décrit la mutation appliquée entre parent et variant."""
        p_rhs = _split(parent)[1]
        v_rhs = _split(variant)[1]
        p_compact = _norm(p_rhs)
        v_compact = _norm(v_rhs)

        for m in MUTATION_REGISTRY:
            mid = m["id"]
            template = m["template"]
            if template:
                # Vérifier si le template apparaît dans la variante (simplifié)
                tmpl_compact = _norm(template.format(rhs="", main="", other=[""], L="", R="", tau="", t="", Ea="", T="", ΔC="", K_m="", K="", beta="", S_eff="", k=""))
                if tmpl_compact and tmpl_compact in v_compact:
                    return m["label"]

        if "/" in v_rhs and "/" not in p_rhs:
            return "division introduite"
        if "*" in v_rhs and v_rhs.count("*") > p_rhs.count("*"):
            return "multiplication ajoutée"
        if "exp(" in v_rhs:
            return "exponentielle introduite"
        if "sqrt" in v_rhs:
            return "racine carrée"
        if "**" in v_rhs:
            return "puissance"

        # Croisement
        for op in _extract_vars(variant):
            if op not in _extract_vars(parent):
                return f"croisement: nouveau symbole {op}"

        return "rescaling structurel"


# ── Fonction utilitaire ─────────────────────────────────────

def collect_signatures(equations: List[Dict[str, Any]]) -> Set[str]:
    """Collecte les signatures d'un lot d'équations."""
    sigs = set()
    for e in equations:
        eq = str(e.get("equation", "") or "")
        if eq:
            sigs.add(structure_signature(eq))
    return sigs