"""DomainClassifierWorker — Analyse l'architecture des équations pour déterminer
leur domaine d'appartenance et valider la diversité des classifications.

Détermine à quel domaine scientifique une équation appartient en analysant :
- Les opérateurs utilisés (dérivée, intégrale, somme → calcul moderne ;
  proportion, ratio → ancien)
- Les constantes et symboles (G, c, h → physique ; φ, π → mathématique)
- La structure syntaxique (équations différentielles, systèmes linéaires, etc.)
- Le pattern des variables (x,y,z → géométrie ; A,B,k → cinétique)

Valide aussi la diversité des classifications pour éviter que toutes les
équations soient classées dans le même domaine.

Message contract (bus):
    request : Message(type="task", destination="domain_classifier", payload={
                  "task_id": str,
                  "expressions": list[str],      # équations à classifier
                  "mode": "classify"|"diversity", # classify par défaut
              })
    response: Message(type="response", destination="captn", payload={
                  "task_id", "current_step_plugin": "domain_classifier",
                  "valid": bool, "classifications": list[dict],
                  "diversity_score": float (en mode diversity),
                  "error_message": str (when invalid)
              })
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("DomainClassifierWorker")


# ──────────────────────────────────────────────────────────────
# DOMAIN_SIGNATURES — motifs structurels → domaine
# ──────────────────────────────────────────────────────────────
DOMAIN_SIGNATURES: Dict[str, Dict[str, Any]] = {
    # ── Mathématiques pures ──────────────────────────────────
    "mathematics_pure": {
        "operators": {"+", "-", "*", "/", "**", "%", "//"},
        "patterns": [
            r"\bphi\b|\bφ\b",              # nombre d'or
            r"\bpi\b|\bπ\b",                # pi
            r"\bfibonacc",                  # suite de Fibonacci
            r"\bfactoriel\b|!",             # factorielle
            r"\bbinomial\b|C\(",            # coefficients binomiaux
            r"\bmod\b|\bmodulo\b",          # modulo
            r"\bgcd\b|\bpgcd\b|\blcm\b|\bppcm\b",  # arithmétique
            r"\bprime\b|\bpremier\b|\bpremiers?\b",  # nombres premiers
            r"\bpermutat",                  # permutations
            r"\bcombinat",                  # combinaisons
        ],
        "symbols": {"n", "k", "i", "j", "x", "y", "z", "a", "b", "c"},
        "description": "Mathématiques pures — algèbre, arithmétique, combinatoire",
    },
    "mathematics_geometry": {
        "operators": {"+", "-", "*", "/", "**"},
        "patterns": [
            r"\bcos\b|\bsin\b|\btan\b|\bcot\b",  # trigonométrie
            r"\bhypot",                     # hypoténuse
            r"\bpythagore",                 # Pythagore
            r"\bthales?\b",                  # Thalès
            r"\bangle\b|\bdegre\b|\brad\b",  # angles
            r"\barea\b|\bair\be\b|\bsurface\b|\bvolume\b",  # géométrie
            r"\bperimeter\b|\bperimetre\b|\bpérimètre\b",
            r"\bcirconference\b|\bcircumfer",
            r"\brays?\b|\blines?\b|\bpoints?\b|\bplane\b",  # géométrie dans l'espace
            r"\btriangle\b|\bcarré\b|\bcube\b|\bsphere\b|\bcylindre\b",
        ],
        "symbols": {"r", "R", "θ", "α", "β", "γ", "d", "A", "S", "V"},
        "description": "Géométrie — trigonométrie, mesures, formes",
    },
    "mathematics_calculus": {
        "operators": {"+", "-", "*", "/", "**"},
        "patterns": [
            r"\bd[xy]/dt\b|\bdx\b|\bdy\b|\bdt\b",  # dérivées
            r"\bderiv",                      # dérivée
            r"\bintegral\b|\bint\b|\b∫\b",   # intégrale
            r"\blimit\b|\blim\b|\b→∞\b|\b→0\b",  # limites
            r"\bsumm?ation\b|\bsum\b|\b∑\b",  # sommes
            r"\bgradient\b|\bgrad\b|\b∇\b",   # gradient
            r"\bdivergence\b|\bdiv\b|\bcurl\b|\brot\b",  # analyse vectorielle
            r"\blaplacian\b|\blaplace\b|\bΔ\b",  # laplacien
            r"\binfini",                     # infini
            r"\bconverg",                    # convergence
            r"\bseries?\b",                  # séries
        ],
        "symbols": {"x", "y", "z", "t", "h", "ε", "δ", "f", "g"},
        "description": "Analyse — calcul différentiel et intégral",
    },
    # ── Mathématiques anciennes ────────────────────────────────
    "mathematics_ancient": {
        "operators": {"+", "-", "*", "/"},
        "patterns": [
            r"\bφ\b|\bphi\b|\bgolden.ratio\b",  # nombre d'or (math ancienne)
            r"\bpythagore",                  # Pythagore / École pythagoricienne
            r"\barchimede",                  # Archimède
            r"\beuclid",                     # Euclide
            r"\btriple.pythagor",            # triplets pythagoriciens
            r"\bquadratique?",
            r"\bproportion\b|\brapport\b|\bratio\b",
            r"\bmoyenne.proportionnelle\b",
            r"\bexhaustion\b",              # méthode d'exhaustion
            r"\bpapyrus\b|\bRhind\b|\bMoscou\b",  # mathématiques égyptiennes
            r"\bbabylonien",                # mathématiques babyloniennes
            r"\bbase.60\b|\bsexagesim",
            r"\bcorde\b|\barc\b|\bcercle\b",  # géométrie ancienne
            r"\bnombre.polygonal",          # nombres polygonaux
            r"\bnombre.parfait",            # nombres parfaits
            r"\bamicable\b",                # nombres amicaux
            r"\bsuite.arith",
            r"\bcheng\b|\bTseng\b|\bchinois\b|\bsuan\b",  # mathématiques chinoises
            r"\blems?ure\b|\bmesure\b|\bterre\b",  # géométrie pratique
        ],
        "symbols": {"a", "b", "c", "d", "r", "n", "m", "p", "q"},
        "description": "Mathématiques anciennes — Pythagore, Euclide, Archimède, Égypte, Babylone",
    },
    # ── Physique ────────────────────────────────────────────────
    "physics_classical": {
        "operators": {"+", "-", "*", "/", "**"},
        "patterns": [
            r"\bF\s*=?\s*m\s*a\b",          # F=ma
            r"\bv\s*=?\s*d\s*[/]?\s*t\b",   # v = d/t
            r"\bm\s*[va]\b",                # momentum
            r"\bg\s*=\s*9[.]?81\b",         # g = 9.81
            r"\bG\b.*\bNewton\b",            # gravitation
            r"\bgravit",                    # gravité
            r"\bmass[ea]?\b",               # masse
            r"\bvelocity\b|\bvitesse\b|\bacceler",  # cinématique
            r"\bmomentum\b|\bquantité.mouv",  # quantité de mouvement
            r"\benergie?[gy]?\b|\benergy\b",  # énergie
            r"\btravail\b|\bwork\b|\bpuissance\b|\bpower\b",  # travail/puissance
            r"\bnewton\b",                  # Newton
        ],
        "symbols": {"F", "m", "a", "v", "d", "t", "g", "G", "E", "p", "W", "P"},
        "description": "Physique classique — mécanique newtonienne",
    },
    "physics_modern": {
        "operators": {"+", "-", "*", "/", "**"},
        "patterns": [
            r"\bE\s*=?\s*m\s*c\b",          # E=mc²
            r"\bc\s*=\s*λ\b",               # c = λf
            r"\bh\b.*\bPlanck\b",           # h de Planck
            r"\bquantum\b|\bquanta\b",       # quantique
            r"\brelativit",                 # relativité
            r"\blorentz\b",                 # Lorentz
            r"\bSchrödinger|\bSchrodinger\b", # Schrödinger
            r"\bwave[\s.]*function\b|\bfonction.onde\b",
            r"\bHeisenberg\b|\bincertitude\b",  # Heisenberg
            r"\bspin\b",                    # spin
            r"\bfeynman\b",                 # Feynman
            r"\bDirac\b",                   # Dirac
            r"\btensor",                    # tenseur
            r"\bchamp\b|\bfield\b",         # champ
            r"\bMaxwell\b",                 # Maxwell
        ],
        "symbols": {"E", "m", "c", "h", "ħ", "λ", "f", "ν", "ψ", "Φ", "∇"},
        "description": "Physique moderne — relativité, quantique, électromagnétisme",
    },
    "physics_thermo": {
        "operators": {"+", "-", "*", "/", "**"},
        "patterns": [
            r"\bPV\b|\bp\s+V\s*=?\b",       # PV = nRT
            r"\bthermodynam",               # thermodynamique
            r"\bentrop",                    # entropie
            r"\benthalp",                   # enthalpie
            r"\b[Tt]emperature\b|\bKelvin\b|\bCelsius\b",
            r"\bchaleur\b|\bheat\b",        # chaleur
            r"\bCarnot\b|\bClausius\b",     # Carnot, Clausius
            r"\bBoltzmann\b",               # Boltzmann
            r"\bk_B\b",                     # constante de Boltzmann
            r"\bgaz\b|\bgas\b",             # gaz
            r"\bpression\b|\bpressure\b",   # pression
        ],
        "symbols": {"P", "V", "T", "S", "n", "R", "k", "Q", "U", "H", "G"},
        "description": "Thermodynamique — chaleur, entropie, gaz",
    },
    # ── Chimie ──────────────────────────────────────────────────
    "chemistry_general": {
        "operators": {"+", "-", "*", "/"},
        "patterns": [
            r"\bconcentr",                  # concentration
            r"\bmol[^e]?\b|\bmolar\b",      # mole/molar
            r"\bpH\b|\bpOH\b",              # pH
            r"\bréact\b|\breact",            # réactif
            r"\bproduit\b|\bproduct\b",      # produit
            r"\bcataly",                    # catalyse
            r"\bvitesse.réac|\brate.const",  # vitesse de réaction
            r"\bArrhenius\b",               # Arrhenius
            r"\béquilibre\b|\bequilibrium\b|\bKc\b|\bKp\b",  # équilibre
            r"\boxyd",                      # oxydation
            r"\bréduct?\b|\breduct",        # réduction
            r"\bélectroly",                 # électrolyse
        ],
        "symbols": {"C", "K", "k", "t", "[", "]", "A", "B", "Δ"},
        "description": "Chimie générale — réactions, concentrations, équilibres",
    },
    "chemistry_physical": {
        "operators": {"+", "-", "*", "/", "**"},
        "patterns": [
            r"\bGibbs\b|\bΔG\b",            # énergie libre de Gibbs
            r"\bΔH\b",                      # enthalpie de réaction
            r"\bΔS\b",                      # entropie de réaction
            r"\bthermochim",                # thermochimie
            r"\bcinétique\b|\bkinetic\b",    # cinétique chimique
            r"\bN\.?dot\b|\bNdot\b",         # notation cinétique
            r"\bd\[A\]/dt\b|\bd\[B\]/dt\b",  # loi de vitesse
            r"\bk\b.*\b[A-Z]\b.*\btemp",     # constante de vitesse
            r"\bL\.?J\.?\b|\bLennard.Jones\b", # potentiel intermoléculaire
        ],
        "symbols": {"k", "Ea", "R", "T", "A", "B", "N", "Ndot", "K", "Δ"},
        "description": "Chimie physique — cinétique, thermochimie, potentiels",
    },
    # ── Alchimie ────────────────────────────────────────────────
    "alchemy": {
        "operators": {"+", "-", "*", "/", "→", "→"},
        "patterns": [
            r"\btransmut",                  # transmutation
            r"\bphilosopher.stone\b|\bpierre.philo",  # pierre philosophale
            r"\bélixir\b|\belixir\b",        # élixir
            r"\bhermetic",                  # hermétique
            r"\bprima.materia",             # matière première
            r"\balambic\b|\balembic\b",      # alambic
            r"\bquintessence\b",            # quintessence
            r"\bmercure\b.*\bsoufre\b",     # mercure et soufre
            r"\bazoth\b|\balkahest\b",      # termes alchimiques
            r"\bParacelse\b|\bParacelsus\b", # Paracelse
            r"\bZosime\b|\bZosimos\b",      # Zosime
            r"\bJabir\b|\bGeber\b",          # Jabir ibn Hayyan
            r"\balchim",                    # alchimie
            r"\b🔮\b|\b⚗️\b|\b🧪\b",         # emoji alchimie
            r"\bspagyri",                   # spagyrie
            r"\bmagnum.opus\b",             # grand œuvre
            r"\bnigredo\b|\balbedo\b|\bcitrinitas\b|\brubedo\b",  # phases
            r"\bcorps\b|\bâme\b|\besprit\b",  # trinité alchimique
        ],
        "symbols": {"☿", "🜍", "🜔", "🜕", "🜖", "🜗", "🜘", "🜙", "Hg", "S", "Sb"},
        "description": "Alchimie — transmutation, hermétisme, spagyrie",
    },
    # ── Biologie ────────────────────────────────────────────────
    "biology": {
        "operators": {"+", "-", "*", "/"},
        "patterns": [
            r"\bcroissance\b|\bgrowth\b",     # croissance
            r"\bpopulation\b",               # population
            r"\blogistiq",                   # logistique
            r"\bLotka\b|\bVolterra\b",        # Lotka-Volterra
            r"\bMendel\b",                   # Mendel
            r"\bdoubl\b.*\btemps\b",          # temps de doublement
            r"\bmortali\b|\bmortalit",       # mortalité
            r"\bnatalit",                   # natalité
            r"\bprédat\b|\bpredat",          # prédation
            r"\bgénétiq\b|\bgenetic",        # génétique
            r"\bHardy.Weinberg\b",           # Hardy-Weinberg
            r"\benzym",                      # enzyme
            r"\bMichaelis.Menten\b",         # Michaelis-Menten
        ],
        "symbols": {"N", "r", "K", "t", "P", "p", "q", "Vmax", "Km"},
        "description": "Biologie — dynamique des populations, génétique, enzymologie",
    },
    # ── Sciences de l'information ────────────────────────────────
    "computation": {
        "operators": {"+", "-", "*", "/", "//", "%", "**", "<<", ">>", "&", "|", "^"},
        "patterns": [
            r"\bcomplexit",                 # complexité
            r"\bO\(n\b|\bO\(log\b|\bO\(n\^",  # notation grand O
            r"\balgorithm",                 # algorithme
            r"\brecursi",                   # récursion
            r"\biteration",                 # itération
            r"\bTuring\b",                  # Turing
            r"\bcomputat",                  # computation
            r"\bcomplexité\b|\bcomplexity\b",
            r"\bNP\b|\bNP.complet\b",        # NP-complétude
            r"\bentrop\b.*\binfo",          # entropie informationnelle
            r"\bShannon\b",                 # Shannon
            r"\bbits?\b|\bbytes?\b",         # bits/bytes
        ],
        "symbols": {"n", "N", "m", "k", "i", "O", "Ω", "Θ"},
        "description": "Informatique — algorithmes, complexité, information",
    },
}


# ──────────────────────────────────────────────────────────────
# Analyse structurelle d'une équation
# ──────────────────────────────────────────────────────────────

def _extract_operators(expression: str) -> set[str]:
    """Extrait les opérateurs mathématiques d'une expression."""
    # Opérateurs multi-caractères d'abord
    multi_ops = re.findall(r"\*\*|//|<<|>>|==|!=|<=|>=|&&|\|\|", expression)
    single_ops = re.findall(r"[+\-*/%^&|~<>=]", expression)
    return set(multi_ops + single_ops)


def _extract_symbols(expression: str) -> set[str]:
    """Extrait les symboles (variables, constantes) d'une expression."""
    tokens = re.findall(r"[A-Za-z_α-ωΑ-ΩφθΦπΔ∇Σ∫][A-Za-z0-9_α-ωΑ-ΩφθΦπΔ∇Σ∫]*", expression)
    reserved = {"math", "sin", "cos", "tan", "cot", "sqrt", "log", "exp",
                "abs", "int", "float", "str", "list", "set", "dict", "tuple",
                "bool", "max", "min", "len", "sum", "pow", "round", "range",
                "sorted", "reversed", "enumerate", "zip", "map", "filter",
                "all", "any", "divmod", "hex", "oct", "ord", "chr", "repr",
                "type", "isinstance", "slice", "print", "open", "input"}
    return {t for t in tokens if t not in reserved}


def _pattern_score(expression: str, signature: Dict[str, Any]) -> float:
    """Score de correspondance entre une expression et une signature de domaine."""
    low_expr = expression.lower().strip()
    score = 0.0

    # 1. Patterns regex
    for pat in signature.get("patterns", []):
        if re.search(pat, low_expr):
            score += 4.0

    # 2. Opérateurs
    expr_ops = _extract_operators(expression)
    op_overlap = len(expr_ops & signature.get("operators", set()))
    score += op_overlap * 0.5

    # 3. Symboles
    expr_syms = _extract_symbols(expression)
    sym_overlap = len(expr_syms & signature.get("symbols", set()))
    score += sym_overlap * 0.3

    return round(score, 3)


# ──────────────────────────────────────────────────────────────
# Mots-clés supplémentaires pour détection de domaine
# ──────────────────────────────────────────────────────────────
_DOMAIN_TAGS: Dict[str, List[str]] = {
    "mathematics_pure":    ["math", "algebra", "arithmetic", "number", "combinatoric",
                            "algebre", "arithmetique", "nombre", "combinatoire"],
    "mathematics_geometry": ["geometry", "geometrie", "triangle", "circle", "square",
                             "trigonometry", "trigonometrie", "angle"],
    "mathematics_calculus": ["calculus", "analyse", "derivative", "derivee", "integral",
                             "integrale", "limit", "limite", "differential"],
    "mathematics_ancient":  ["ancient", "ancien", "pythagore", "euclide", "archimede",
                             "egyptian", "babylonian", "greek", "grecque"],
    "physics_classical":    ["physics", "physique", "mechanics", "mecanique", "newton",
                             "force", "mass", "masse", "motion", "mouvement"],
    "physics_modern":       ["quantum", "relativity", "relativite", "quantique",
                             "maxwell", "schrodinger", "dirac", "feynman"],
    "physics_thermo":       ["thermo", "heat", "chaleur", "entropy", "entropie"],
    "chemistry_general":    ["chemistry", "chimie", "reaction", "pH", "mole", "molar"],
    "chemistry_physical":   ["kinetic", "cinetique", "Ndot", "gibbs", "enthalpy"],
    "alchemy":              ["alchemy", "alchimie", "transmutation", "hermetic",
                             "spagyric", "philosopher", "quintessence"],
    "biology":              ["biology", "biologie", "population", "logistic", "growth",
                             "croissance", "mendel", "lotka"],
    "computation":          ["algorithm", "algorithme", "complexity", "complexite",
                             "turing", "computational", "shannon"],
}


class DomainClassifierWorker:
    """Analyse l'architecture d'une équation pour déterminer son domaine
    scientifique d'appartenance.

    Utilise :
    - Des signatures structurelles (opérateurs, patterns regex, symboles)
    - Des mots-clés de domaine
    - L'analyse de diversité des classifications
    """

    name = "domain_classifier"

    def __init__(self, bus=None):
        self.bus = bus
        self._classification_history: List[Dict[str, Any]] = []

    def initialize(self) -> bool:
        logger.info(f"[{self.name}] Classifieur de domaine initialisé : "
                    f"{len(DOMAIN_SIGNATURES)} signatures, "
                    f"{len(_DOMAIN_TAGS)} domaines.")
        return True

    def shutdown(self) -> bool:
        return True

    # ── Classification ────────────────────────────────────────

    def classify_expression(self, expression: str) -> Dict[str, Any]:
        """Classifie une équation dans un domaine scientifique.

        Analyse l'expression et retourne le domaine principal + les scores
        de tous les domaines candidats.

        Returns:
            Dict avec domain, score, all_scores, architecture
        """
        low_expr = expression.lower().strip()
        scores: Dict[str, float] = {}

        # Score basé sur les signatures structurelles
        for domain_key, sig in DOMAIN_SIGNATURES.items():
            scores[domain_key] = _pattern_score(expression, sig)

        # Score basé sur les tags de domaine
        for domain_key, tags in _DOMAIN_TAGS.items():
            for tag in tags:
                if tag in low_expr:
                    scores[domain_key] = scores.get(domain_key, 0.0) + 3.0

        # Bonus pour indicateurs évidents
        # Si contient '=' → formule/équation (plus physique/chimie)
        if "=" in expression:
            for d in ["physics_classical", "physics_modern", "chemistry_general",
                       "chemistry_physical", "mathematics_pure"]:
                scores[d] = scores.get(d, 0.0) + 0.5

        # Si contient 'd/dt' ou dérivée → calcul moderne
        if re.search(r"\bd[^a-z]/dt\b|\bddt\b|\bderiv", low_expr):
            scores["mathematics_calculus"] = scores.get("mathematics_calculus", 0.0) + 3.0

        # Si contient φ ou golden → math ancienne
        if re.search(r"\bφ\b|\bphi\b", low_expr):
            scores["mathematics_ancient"] = scores.get("mathematics_ancient", 0.0) + 3.0
            scores["mathematics_pure"] = scores.get("mathematics_pure", 0.0) + 1.0

        # Si contient emoji alchimique
        alch_emoji = {"🔮", "⚗️", "🧪"}
        if any(e in expression for e in alch_emoji):
            scores["alchemy"] = scores.get("alchemy", 0.0) + 5.0

        # Trier par score descendant
        ranked = sorted(scores.items(), key=lambda x: -x[1])

        best_domain = ranked[0][0] if ranked else "unknown"
        best_score = ranked[0][1] if ranked else 0.0

        # Architecture details
        architecture = {
            "operators": sorted(_extract_operators(expression)),
            "symbols": sorted(_extract_symbols(expression)),
            "has_equation": "=" in expression,
            "has_derivative": bool(re.search(r"\bd[^a-z]/dt\b|\bderiv", low_expr)),
            "has_integral": "int" in low_expr or "integral" in low_expr or "∑" in expression,
            "length": len(expression),
        }

        result = {
            "expression": expression,
            "domain": best_domain,
            "domain_label": DOMAIN_SIGNATURES.get(best_domain, {}).get(
                "description", best_domain),
            "score": best_score,
            "all_scores": dict(ranked[:6]),  # top 6
            "architecture": architecture,
            "valid": best_score > 0,
        }

        self._classification_history.append(result)
        return result

    # ── Classification par lot ────────────────────────────────

    def classify_batch(self, expressions: List[str]) -> List[Dict[str, Any]]:
        """Classe une liste d'équations."""
        return [self.classify_expression(e) for e in expressions]

    # ── Analyse de diversité ───────────────────────────────────

    def check_diversity(self, expressions: List[str]) -> Dict[str, Any]:
        """Analyse la diversité des classifications d'un lot d'équations.

        Vérifie :
        - Distribution des domaines (trop concentrée ?)
        - Domination d'un seul domaine
        - Suggestion de domaines sous-représentés
        """
        classifications = self.classify_batch(expressions)
        domain_counts: Counter = Counter()
        for c in classifications:
            domain_counts[c["domain"]] += 1

        total = len(classifications)
        violations: List[str] = []
        suggestions: List[str] = []

        if total >= 2:
            for domain, count in domain_counts.most_common(1):
                ratio = count / total
                if ratio > 0.7:
                    violations.append(
                        f"Domination du domaine '{domain}': {ratio:.0%} "
                        f"des équations ({count}/{total}).")
                    # Suggérer un domaine différent
                    for other_domain in DOMAIN_SIGNATURES:
                        if other_domain != domain:
                            suggestions.append(
                                f"Ajouter des équations du domaine: {other_domain} "
                                f"({DOMAIN_SIGNATURES[other_domain]['description']})")
                            break

        if total >= 3 and len(domain_counts) < 2:
            violations.append(
                f"Toutes les équations dans le même domaine. "
                f"Diversité nulle.")

        # Score de diversité
        if total > 0:
            n_domains = len(domain_counts)
            max_domains = len(DOMAIN_SIGNATURES)
            # Entropie normalisée
            from math import log2
            entropy = 0.0
            for count in domain_counts.values():
                p = count / total
                if p > 0:
                    entropy -= p * log2(p)
            max_entropy = log2(min(total, max_domains))
            diversity_score = round((entropy / max_entropy * 100) if max_entropy > 0 else 0, 1)
        else:
            diversity_score = 0.0

        return {
            "valid": diversity_score >= 40.0,
            "diversity_score": diversity_score,
            "domain_distribution": dict(domain_counts),
            "violations": violations,
            "suggestions": suggestions,
            "total_expressions": total,
            "classifications": classifications,
        }

    # ── Plugin bus interface ──────────────────────────────────

    def execute(self, message) -> None:
        from captn.runtime.base import Message
        task_id = message.payload.get("task_id")
        expressions = message.payload.get("expressions", [])
        mode = message.payload.get("mode", "classify")

        if isinstance(expressions, str):
            expressions = [expressions]

        logger.info(f"[{self.name}] Processing {mode} for task {task_id} "
                    f"({len(expressions)} expressions)")

        result = None
        error = None

        try:
            if mode == "classify":
                if len(expressions) == 1:
                    result = self.classify_expression(expressions[0])
                else:
                    result = self.classify_batch(expressions)
            elif mode == "diversity":
                result = self.check_diversity(expressions)
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
                "valid": error is None,
                "result": result,
                "error_message": error,
            },
        )
        if self.bus is not None:
            self.bus.publish(response)


# ── Standalone CLI helper ────────────────────────────────────
def quick_classify(expression: str) -> Dict[str, Any]:
    """Classification rapide d'une équation depuis la ligne de commande."""
    w = DomainClassifierWorker()
    return w.classify_expression(expression)