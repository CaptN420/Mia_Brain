# Outils déterministes — `tools/`

26 outils Python **sans LLM**. Chacun est importé directement par `summon_agents.py` selon la commande.

| Fichier | Commande | Rôle |
|---------|----------|------|
| `equation_tools.py` | `equation` | Solveurs math/physique/chimie (quadratique, masse molaire, cinématique…) |
| `equation_workbench.py` | `workbench` | Validation, score, simulation, dédup d'équations |
| `codebase_map.py` | `codebase-map` | Cartographie statique AST d'un projet Python |
| `dataset_tools.py` | `dataset` | Stats, recherche, dédup, filtrage JSONL |
| `pipeline_trace.py` | `trace` | Parse les logs en timeline structurée |
| `corpus_tools.py` | `corpus` | Recherche et patterns dans le corpus code |
| `depgraph.py` | `depgraph` | Graphe de dépendances + détection cycles |
| `testgen.py` | `testgen` | Génération de stubs pytest |
| `deadscout.py` | `deadscout` | Détection de code mort par analyse AST |
| `apigen.py` | `apigen` | Documentation API depuis docstrings |
| `healthcheck.py` | `healthcheck` | Score de santé /100 du projet |
| `eqsolve.py` | `eqsolve` | Solveur symbolique SymPy (dérivée, intégrale, etc.) |
| `chemsym.py` | `chemsym` | Chimie : masse molaire, balancement, rendement |
| `changelog.py` | `changelog` | Release notes depuis git |
| `nl2eq.py` | `nl2eq` | Langage naturel → équation |
| `loganomaly.py` | `loganomaly` | Détection d'anomalies dans les logs |
| `codechunk.py` | `codechunk` | Découpage AST d'un fichier en chunks fonction/classe |
| `imports_tool.py` | `imports` | Analyse et optimisation des imports (détection inutilisés, fix) |
| `context_ai.py` | `context` | Extraction du contexte local autour d'une ligne/fonction |
| `lint_tool.py` | `lint` | Linter structuré (docstrings, imports, style) — 0 LLM |
| `diff_ast.py` | `diff-ast` | Diff structurel AST (ignore formatage, compare signatures) |
| `alchimie_library_manager.py` | *(importé par thinker/mirror)* | Gestion de la librairie alchimie |
| `alchimie_dashboard.py` | *(standalone)* | Dashboard Flask de l'alchimie |
| `alchimie_dashboard_cli.py` | *(standalone)* | CLI pour le dashboard alchimie |
| `crawler_cli.py` | *(standalone)* | Crawler de code |
| `raw2json_cli.py` | *(standalone)* | Convertisseur dossier → JSONL |
| `scanner.py` | *(standalone)* | Scanner de projet |
| `scan_secrets.py` | *(standalone)* | Détection de secrets dans le code |
| `math_validator.py` | *(importé par workbench)* | Validateur de formules mathématiques |
| `update_alchimie.py` | *(standalone)* | Mise à jour de la bibliothèque alchimie |

## Principe

Chaque outil est :

- **déterministe** : mêmes entrées → mêmes sorties
- **standalone** : importable directement depuis `tools/`
- **testable** : pytest sur chaque module
- **léger** : aucune dépendance LLM

```python
# Exemple d'utilisation directe (sans summon_agents)
from tools.healthcheck import HealthChecker
checker = HealthChecker("/mon/projet")
report = checker.check()
print(f"Score: {report.score}/100")
```