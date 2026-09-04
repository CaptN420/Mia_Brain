# Commandes CLI — `python summon_agents.py <commande>`

Toutes les commandes sont déterministes sauf mention explicite.

---

### 1. `thinker` — Synthétise des rapports

Analyse les résultats de plusieurs workers et produit un rapport structuré.

```bash
python summon_agents.py thinker --findings test_findings.json -o rapport.json
python summon_agents.py thinker --findings - < resultats.jsonl
```

Options : `--alchimie-version` `--output`

---

### 2. `mirror` — Raisonnement miroir

Prend une hypothèse et génère son opposé structurel (opposition par symétrie additive↔multiplicative, etc.).

```bash
python summon_agents.py mirror -H "Cette fonction est thread-safe" -o mirror.json
python summon_agents.py mirror -H "N = k * A * B"
```

Options : `--task-id` `--output`

---

### 3. `deterministic` — Transformations AST

Applique des transformations purement syntaxiques sur du code source (docstrings, annotations, normalisation).

```bash
python summon_agents.py deterministic -c "def foo(x): return x+1" -m docstrings annotate
python summon_agents.py deterministic -f mon_code.py -o nettoye.py --write-dir ./out
```

Modes : `docstrings`, `annotate`, `normalize`

---

### 4. `autogen` — Génération avec fallback LLM

Boucle d'itérations génération → validation → correction. Utilise un LLM si le déterministe échoue.

```bash
python summon_agents.py autogen -d mirror/dataset.jsonl -i 3 -s 4
python summon_agents.py autogen --model qwen2:1.5b --no-llm-fallback
```

---

### 5. `fixgen` — Template de correctif

Génère un squelette de correctif à partir d'une description de problème.

```bash
python summon_agents.py fixgen --task-id BUG-42 --pgm "La fonction foo() lève une exception si x est None"
```

---

### 6. `pipeline` — Orchestrateur complet

Lance un pipeline CaptN complet (workers → analyse → rapport).

```bash
python summon_agents.py pipeline -p /chemin/vers/projet -w 15
```

---

### 7. `raw2json` — Conversion dossier → JSONL

Convertit une arborescence de fichiers en dataset JSONL pour l'entraînement.

```bash
python summon_agents.py raw2json -p /chemin/projet
```

---

### 8. `equation` — Outils mathématiques

Exécute des solveurs mathématiques, physiques et chimiques.

```bash
# Lister les outils disponibles
python summon_agents.py equation list --domain math

# Résoudre une équation quadratique
python summon_agents.py equation run solve_quadratic --kwargs '{"a":1,"b":-3,"c":2}'

# Calculer une masse molaire
python summon_agents.py equation run molecular_weight --kwargs '{"formula":"H2O"}'

# Chute libre
python summon_agents.py equation run kinematics_free_fall --kwargs '{"height":10}'
```

---

### 9. `workbench` — Atelier d'équations

Validation, score, comparaison, déduplication, simulation d'équations.

```bash
python summon_agents.py workbench run validate --kwargs '{"equation":"Ndot = k * A * B"}'
python summon_agents.py workbench run score    --kwargs '{"equation":"Ndot = k * A * B"}'
python summon_agents.py workbench run compare  --kwargs '{"eq1":"Ndot=k*A","eq2":"Ndot=k*A*B"}'
python summon_agents.py workbench run simulate --kwargs '{"equation":"Ndot=k*A*B","variable_ranges":{"k":[0.1,2],"A":[0.5,5],"B":[0.5,5]}}'
```

---

### 10. `codebase-map` — Cartographie de projet

Analyse statique d'un projet Python : fichiers, classes, fonctions, imports.

```bash
python summon_agents.py codebase-map /chemin/projet -o manifest.json
python summon_agents.py codebase-map /chemin/projet --full
```

---

### 11. `dataset` — Manipulation de JSONL

Stats, recherche, déduplication, filtrage de datasets.

```bash
python summon_agents.py dataset stats --file dataset.jsonl
python summon_agents.py dataset search --file dataset.jsonl --query "async"
python summon_agents.py dataset sample --file dataset.jsonl -n 10
python summon_agents.py dataset dedup --file dataset.jsonl -o deduped.jsonl
python summon_agents.py dataset fields --file dataset.jsonl
python summon_agents.py dataset filter --file dataset.jsonl --min-lines 5 -o filtered.jsonl
```

---

### 12. `trace` — Analyse de logs pipeline

Parse `runtime.log` et produit une timeline, erreurs, ou messages bus.

```bash
python summon_agents.py trace --log runtime.log --timeline
python summon_agents.py trace --log runtime.log --errors-only
python summon_agents.py trace --log runtime.log --bus
python summon_agents.py trace --log runtime.log -o trace.json
```

---

### 13. `corpus` — Exploration du code corpus

Recherche, stats, extraction de patterns dans `Code_base/`.

```bash
python summon_agents.py corpus search --query "async def" --dir Code_base
python summon_agents.py corpus stats --dir Code_base
python summon_agents.py corpus patterns --min-frequency 5
python summon_agents.py corpus list-files
python summon_agents.py corpus sample -n 3
```

---

### 14. `depgraph` — Graphe de dépendances

Analyse les dépendances entre modules et détecte les cycles.

```bash
python summon_agents.py depgraph /chemin/projet
python summon_agents.py depgraph /chemin/projet --format dot -o graph.dot
python summon_agents.py depgraph /chemin/projet --circular-only
python summon_agents.py depgraph /chemin/projet --format mermaid
```

---

### 15. `testgen` — Stubs pytest

Génère des squelettes de tests à partir d'un fichier Python.

```bash
python summon_agents.py testgen tools/healthcheck.py -o tests/test_healthcheck.py
python summon_agents.py testgen tools/healthcheck.py --strategy hypothesis --with-hypothesis
```

Stratégies : `parametrize` (défaut), `hypothesis`, `fixture`

---

### 16. `deadscout` — Code mort

Détecte les fonctions, classes et variables non utilisées.

```bash
python summon_agents.py deadscout /chemin/projet
python summon_agents.py deadscout /chemin/projet --detailed
python summon_agents.py deadscout /chemin/projet --deadscout/mon-projet.json
```

---

### 17. `apigen` — Documentation API

Génère une documentation depuis les docstrings.

```bash
python summon_agents.py apigen /chemin/projet -o API.md
python summon_agents.py apigen /chemin/projet --format json
python summon_agents.py apigen /chemin/projet --format mkdocs
```

---

### 18. `healthcheck` — Score de santé

Note le projet sur 100 : couverture docstring, complexité, tests, CI, code mort.

```bash
python summon_agents.py healthcheck /chemin/projet
python summon_agents.py healthcheck /chemin/projet --json
```

---

### 19. `eqsolve` — Solveur symbolique (SymPy)

Dérivée, intégrale, résolution, Taylor, limite, simplification.

```bash
python summon_agents.py eqsolve derive "k*A*B" -v A
python summon_agents.py eqsolve integrate "x**2" -v x
python summon_agents.py eqsolve solve "x**2 - 4" -v x
python summon_agents.py eqsolve simplify "(x+1)*(x-1)"
python summon_agents.py eqsolve expand "(x+1)**2"
python summon_agents.py eqsolve factor "x**2 - 4"
python summon_agents.py eqsolve taylor "sin(x)" -v x -n 3
python summon_agents.py eqsolve limit "1/x" -v x --to 0
```

---

### 20. `chemsym` — Chimie symbolique

Masse molaire, balancement d'équations, rendement.

```bash
python summon_agents.py chemsym molar-mass "H2O"
python summon_agents.py chemsym balance "H2 + O2 = H2O"
python summon_agents.py chemsym yield "Fe" --product "Fe2O3" --mass 100
python summon_agents.py chemsym parse "C6H12O6"
python summon_agents.py chemsym formula "glucose"
```

---

### 21. `changelog` — Release notes git

Génère un changelog depuis l'historique git.

```bash
python summon_agents.py changelog
python summon_agents.py changelog --from v0.1.0 --to HEAD
python summon_agents.py changelog --format json -o changelog.json
python summon_agents.py changelog --append -o CHANGELOG.md
```

---

### 22. `nl2eq` — Langage naturel → équation

Convertit une phrase en équation mathématique structurée.

```bash
python summon_agents.py nl2eq "the rate of change of N is proportional to k times A"
python summon_agents.py nl2eq --check "Ndot = k*A*B"
python summon_agents.py nl2eq --list
```

---

### 23. `loganomaly` — Anomalies dans les logs

Analyse les logs et détecte outliers, tendances, erreurs.

```bash
python summon_agents.py loganomaly runtime.log
python summon_agents.py loganomaly runtime.log --outliers
python summon_agents.py loganomaly runtime.log --errors
python summon_agents.py loganomaly runtime.log --trends
python summon_agents.py loganomaly runtime.log --json
```

---

### 24. `codechunk` — Découpage de fichier (économiseur de tokens)

Découpe un fichier Python en chunks par fonction/classe. Envoyez **seulement** le chunk pertinent au LLM au lieu du fichier entier.

```bash
python summon_agents.py codechunk mon_fichier.py
python summon_agents.py codechunk mon_fichier.py -f foo          # chunk de la fonction foo
python summon_agents.py codechunk mon_fichier.py -l 42           # chunk contenant la ligne 42
python summon_agents.py codechunk mon_fichier.py -l 42 -c 5      # +5 lignes de contexte
python summon_agents.py codechunk mon_fichier.py --json
```

---

### 25. `imports` — Analyse d'imports (économiseur de tokens)

Détecte les imports inutilisés, catégorise stdlib vs third-party, et peut les supprimer automatiquement.

```bash
python summon_agents.py imports mon_fichier.py
python summon_agents.py imports mon_fichier.py -u                # seulement les inutilisés
python summon_agents.py imports /chemin/projet                   # scan récursif
python summon_agents.py imports mon_fichier.py --fix              # suppression auto
python summon_agents.py imports mon_fichier.py --fix -o nettoye.py
python summon_agents.py imports mon_fichier.py --json
```

---

### 26. `context` — Contexte local (économiseur de tokens)

Extrait le contexte complet autour d'une ligne ou d'une fonction : code englobant, paramètres, docstring, type de retour.

```bash
python summon_agents.py context mon_fichier.py -l 42             # contexte de la ligne 42
python summon_agents.py context mon_fichier.py -f foo            # contexte de la fonction foo
python summon_agents.py context mon_fichier.py --list            # liste des symboles
python summon_agents.py context mon_fichier.py --json
python summon_agents.py context mon_fichier.py -s 5              # 5 lignes de contexte (défaut: 3)
```

---

### 27. `lint` — Linter structuré (économiseur de tokens)

Vérifie docstrings manquantes, `except:` nus, `== None`, `print()`, lignes longues. Utilise pyflakes si dispo.

```bash
python summon_agents.py lint mon_fichier.py
python summon_agents.py lint /chemin/projet
python summon_agents.py lint mon_fichier.py --errors-only
python summon_agents.py lint mon_fichier.py --json
```

---

### 28. `diff-ast` — Diff structurel (économiseur de tokens)

Compare la **structure** de deux codes Python (signatures, classes, méthodes, imports) en ignorant le formatage.

```bash
python summon_agents.py diff-ast ancien.py nouveau.py
python summon_agents.py diff-ast -c "def foo(x): return x" -C "def foo(x, y): return x+y"
python summon_agents.py diff-ast ancien.py nouveau.py --json
```