# Architecture CaptN-BRAIN

```
┌─────────────────────────────────────────────────────┐
│                    summon_agents.py                  │
│               Point d'entrée CLI (23 commandes)     │
└──────────┬────────────────────────────────┬──────────┘
           │                                │
    ┌──────▼──────┐                 ┌───────▼────────┐
    │   captn/     │                 │    tools/       │
    │   Runtime    │                 │  Déterministes  │
    │  (orchestra- │                 │  (26 outils)    │
    │   teur +     │                 │                 │
    │   workers)   │                 │ eqsolve,        │
    │              │                 │ chemsym,        │
    │ Thinker      │                 │ healthcheck,    │
    │ MirrorAgent  │                 │ deadscout…      │
    │ Pipeline     │                 │                 │
    └──────┬───────┘                 └─────────────────┘
           │
    ┌──────▼──────────────────────────────────┐
    │  Déterministe d'abord → LLM si échec    │
    │  cfg.llm_as_fallback = True             │
    └─────────────────────────────────────────┘
```

## Flux d'exécution

1. **CLI** (`summon_agents.py`) parse la commande et aiguille vers le handler
2. **Handler** importe le module (`tools/` ou `captn/`) via import lazy (économie de tokens)
3. **Déterministe** : l'outil s'exécute sans LLM — AST parsing, sympy, regex, graphes
4. **Fallback** (optionnel) : si le déterministe échoue, `autogen` appelle Ollama
5. **Résultat** : stdout ou fichier JSON

## Règles

| Principe | Description |
|----------|-------------|
| **Déterministe first** | Tout ce qui peut être fait sans LLM l'est |
| **Lazy imports** | Les modules sont importés au moment de l'appel, pas au démarrage |
| **Zero circular** | Aucune dépendance circulaire entre modules |
| **Token economy** | Le LLM est un fallback coûteux, pas le chemin principal |

## Sous-systèmes

| Sub | Rôle |
|-----|------|
| `captn/runtime/` | Orchestrateur, bus, state store, Thinker, MirrorAgent |
| `captn/workers/` | Workers spécialisés (code_generation, data_ingestion…) |
| `mia/` | Multi-agent intelligence alchemy (équations symboliques) |
| `wd-40/` | Watchdog, sandbox, quarantaine |
| `alchimie/` | Librairie de transformations et règles |
| `Code_base/` | Corpus de code source pour référence |