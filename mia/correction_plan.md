# Plan de correction — MIA Evolution Loop, Sessions, Mémoire et Equation Parent

## Objectif

Corriger le pipeline d'évolution de MIA afin qu'une exécution en mode `loop` :

1. crée une seule session au démarrage lorsque `--create-new-session` est demandé ;
2. réutilise cette même session pendant tous les cycles ;
3. conserve correctement la mémoire et les états validés ;
4. transforme réellement une équation approuvée en `parent_equation` ;
5. permette à Mutation & Repair de travailler sur ce parent ;
6. évite les boucles stériles où chaque cycle recommence sans héritage ;
7. empêche la création de dizaines de dossiers de session pour une seule exécution.

---

## PHASE 1 : Fondations et Gestion des Sessions (Priorité Haute)

### 1. Diagnostiquer précisément la création de sessions
* [ ] Inspecter `launcher.py` et toutes les fonctions appelées par le mode `loop`.
* [ ] Identifier exactement où `--create-new-session` est lu.
* [ ] Identifier où `create_new_session` est transformé en création physique de dossier.
* [ ] Vérifier si cette logique est exécutée une fois au démarrage ou à chaque cycle.
* [ ] Identifier la cause de la création de ~73 dossiers pour une seule exécution.
* [ ] Ajouter des logs explicites : `SESSION_CREATE`, `SESSION_REUSE`, `SESSION_ID`, `SESSION_PATH`.
* [ ] Vérifier que le même `session_id` reste actif pendant toute l'exécution du loop.

### 2. Séparer "nouvelle session" et "nouveau cycle"
* [ ] Une nouvelle session doit représenter une nouvelle expérience/contexte.
* [ ] Un cycle d'évolution ne doit jamais créer une nouvelle session.
* [ ] Un `Evolution Cycle N` doit rester dans la session courante.
* [ ] Les générations doivent être représentées comme des états/versionnements à l'intérieur de la session.
* [ ] Ne pas utiliser le mot `session` pour désigner une génération.

### 10. Corriger la distinction "mémoire" vs "état d'exécution"
* [ ] Ne pas utiliser la mémoire documentaire comme seule source de vérité.
* [ ] Définir `session_state` (id, génération, approved_variables, approved_equations, parent_equation_id, pending_repairs, evolution_status).
* [ ] Définir `memory` (historique : rejected_candidates, accepted_candidates, repair_notes, validation_history, lineage).
* [ ] `session_state` doit permettre de reprendre l'exécution.

---

## PHASE 2 : Structures de Données Canoniques

### 3. Construire une mémoire canonique
* [ ] Identifier tous les mécanismes qui écrivent dans la mémoire.
* [ ] Définir une structure canonique pour les variables et les équations.
* [ ] Définir un statut explicite pour chaque objet (DISCOVERED, PENDING, VALIDATED, CONSOLIDATED, APPROVED, REJECTED, REPAIR_PENDING, MUTATED, ARCHIVED).
* [ ] Le statut interne doit être porté par une structure de données fiable, pas par du texte libre LLM.

### 4. Créer un objet canonique `EquationCandidate`
* [ ] Représentation interne unique : `id`, `equation`, `calculated_object`, `law_type`, `architecture`, `variables`, `causal_links`, `validation_status`, `parent_id`, `generation`.
* [ ] Chaque équation possède un `id` et un `parent_id`.
* [ ] Les liens causaux sont stockés sous forme structurée.

### 12. Normaliser les décisions des agents
* [ ] Convertir les réponses des agents dans un schéma JSON standard : `{"status": "APPROVED", "equation": "...", "calculated_object": "...", "variables": [], "causal_links": [], "action": "CONSOLIDATE"}`.
* [ ] Le parser doit échouer proprement si les champs obligatoires sont absents.

---

## PHASE 3 : Pipeline d'Évolution Core

### 5. Corriger le handoff FinalValidator → Parent
* [ ] Localiser la sortie réelle de `FinalValidator`.
* [ ] Localiser le code qui sélectionne `parent_equation`.
* [ ] Après approbation, appeler explicitement une fonction de consolidation.
* [ ] Persister la référence `parent_equation` dans l'état de la session.
* [ ] Vérifier que `parent_equation` survit au changement de phase et de cycle.

### 6. Définir une règle de consolidation explicite
* [ ] Créer `consolidate_equation()` et `select_parent_equation()`.
* [ ] Critères de parent : `APPROVED` + `equation` + `calculated_object` + `len(causal_links) >= 2`.
* [ ] Empêcher une équation partiellement remplie de devenir parent.

### 7. Corriger Mutation & Repair
* [ ] Mutation doit recevoir explicitement `parent_equation`.
* [ ] Repair doit recevoir soit le parent, soit une candidate mutée.
* [ ] Ne jamais lancer Repair avec une entrée `None`.
* [ ] Afficher des erreurs explicites en cas d'absence de parent (`NO_PARENT_IN_STATE`, etc.).

### 8. Ajouter la filiation des équations
* [ ] Chaque mutation/réparation doit conserver son `parent_id`.
* [ ] Enregistrer la génération (`generation`).
* [ ] Permettre de retrouver l'ancêtre d'une équation.

---

## PHASE 4 : Robustesse et Logique de Contrôle

### 9. Empêcher les répétitions stériles
* [ ] Ajouter une détection de cycle stérile.
* [ ] Si `parent_equation is None` pendant N cycles consécutifs, arrêter ou passer en mode diagnostic.
* [ ] Ajouter un fingerprint/hash de l'équation candidate.
* [ ] Détecter les équations identiques ou quasi-identiques.

### 11. Vérifier les erreurs de validation scientifique
* [ ] Registre global des variables verrouillées.
* [ ] Empêcher un agent de redéfinir une variable verrouillée.
* [ ] Vérifier l'équation contre le registre des variables avant FinalValidator.
* [ ] Vérifier automatiquement les dimensions.

### 13. Ajouter des assertions de cohérence
* [ ] Ajouter des points de contrôle (EVOLUTION_GATE) avant Mutation & Repair :
  * `assert session.parent_equation is not None`
  * `assert session.parent_equation.validation_status == "APPROVED"`

---

## PHASE 5 : Validation et Diagnostics

### 14. Tests indispensables
* [ ] Test A : `--create-new-session` (1 session créée).
* [ ] Test B : `loop 5` (1 session, 5 cycles, 0 nouvelles sessions).
* [ ] Test C : `FinalValidator -> APPROVED` (parent_equation mis à jour).
* [ ] Test D : `Mutation` (parent_equation transmis, mutation autorisée).
* [ ] Test E : `Repair` (reçoit eq_y).
* [ ] Test F : Reprise de session (état restauré).
* [ ] Test G : Aucun parent (erreur explicite, pas de boucle infinie).

### 15. Instrumentation finale
* [ ] Résumé à chaque cycle :
  * `SESSION ID & PATH`
  * `Cycle & Generation`
  * `Variables & Equations (Validated/Approved)`
  * `Parent ID & Status`
  * `Mutation/Repair Status`
