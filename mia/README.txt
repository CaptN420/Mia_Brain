# 🧪 MIA — Multi-Agent Intelligence Alchemy
**Architect: Célestin Martin**

> \"Quand une IA a une idée, une autre la critique, une troisième la répare, une quatrième la mute, et une cinquième l'archive pour la postérité.\"

Bienvenue dans **MIA**, un laboratoire expérimental où plusieurs IA collaborent (et se disputent parfois) pour générer, valider, réparer et faire évoluer des équations symboliques. 

Ce projet s'inscrit dans la vision de **Captn**, un "Deterministic Agent Runtime" conçu par **Célestin Martin**. L'idée centrale est la séparation entre l'**Orchestrateur (le Cerveau)** et les **Workers (les Mains)**.

---

## ✨ C'est quoi ce truc ?

MIA est un framework Python de recherche symbolique multi-agents. 
Au lieu de demander à une seule IA :
> \"Trouve-moi une équation\"

MIA organise un véritable conseil des sages numériques :

- 🧠 **Générateurs d'idées** (Aurelius, etc.)
- 🛡️ **Validateurs** (HermesValidator)
- 🔧 **Réparateurs** (Chymicus)
- 🧬 **Mutateurs** (MutationEquationOrchestrator)
- 📚 **Archivistes** (Archiviste)
- 👁️ **Sentinelles** (Sentinelle)

Chaque agent possède son rôle et participe à l'évolution des équations.

---

## 🔬 Ce que MIA fait

✅ **Génération de variables** : Définition de la base symbolique.
✅ **Synthèse d'équations** : Construction des relations mathématiques.
✅ **Validation de cohérence** : Vérification des unités et des rôles causaux.
✅ **Réparation automatique** : Correction des équations rejetées par les validateurs.
✅ **Mutation dirigée** : Évolution des équations prometteuses vers de nouvelles formes.
✅ **Mémoire persistante** : Archivage complet des découvertes (validées, rejetées, mutées).
✅ **Lignées d'équations** : Suivi de la généalogie des idées.
✅ **Modèles locaux** : Fonctionne avec Ollama pour une confidentialité et une flexibilité totales.

---

## 🧬 Cycle de vie d'une équation

```text
Génération
     ↓
Validation
     ↓
Acceptée ? ── Non ──► Réparation
     ↓                    ↓
    Oui                  Validation
     ↓                    ↓
 Archivage ◄──────────────┘
     ↓
 Mutation
     ↓
 Nouvelle génération
```

Une équation dans MIA peut avoir :
- des parents
- des enfants
- des mutations
- des réparations
- un historique complet

L'objectif est que le système apprenne progressivement de ses propres expériences.

---

## 🤖 Les Habitants du Laboratoire

- **Aurelius** : Génère de nouvelles idées. *"Et si on essayait ça ?"*
- **HermesValidator** : Cherche les erreurs. *"Non."*
- **Chymicus** : Répare les équations cassées. *"Attends, je peux arranger ça."*
- **Archiviste** : Range tout soigneusement. *"Je garde ça au cas où."*
- **Sentinelle** : Surveille le chaos. *"Je vous avais dit que ça allait casser."*

---

## 🛠️ Configuration & Installation

MIA peut fonctionner sur CPU avec des modèles légers.

**Configuration testée :**
- Python 3.x
- Ollama
- Gemma 2:2b (ou modèles plus puissants comme Llama 3.1/3.2, Qwen 2.5)

**Installation :**
```bash
git clone https://github.com/votre-compte/mia.git
cd mia
pip install -r requirements.txt
```

**Lancer MIA :**
```bash
python ui_launcher.py
```

---

## ⚠️ Important

MIA est un projet expérimental. 
Les équations produites :
- ne constituent pas des vérités scientifiques ;
- ne remplacent pas des expériences réelles ;
- peuvent être géniales ;
- peuvent être absurdes ;
- sont souvent les deux à la fois.

---

## 🛠️ Développé avec du Vibe Coding

Ce projet a été développé par **Célestin Martin** avec :
- Python
- beaucoup de café
- des modèles locaux
- de nombreuses expérimentations
- une quantité difficilement mesurable de \"tiens, et si...\"

L'IA a aidé à écrire du code. 
L'humain a survécu aux bugs.

---

## 📜 Licence

**MIT**
Parce que l'alchimie devrait être libre.

---
*Conçu et Architecturé par **Célestin Martin**.*
