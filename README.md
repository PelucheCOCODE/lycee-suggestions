# Boîte à Idées — Plateforme de suggestions pour lycée

Plateforme web participative permettant aux élèves de proposer des idées pour améliorer le lycée, et au CVL / administration de les gérer.

## Fonctionnalités

- **Interface élèves mobile-first** : proposer une idée, soutenir une suggestion existante
- **Reformulation par IA locale** : chaque suggestion est reformulée par un LLM local (Ollama) avant publication
- **Détection de doublons intelligente** : mots-clés + TF-IDF + vérification LLM — si une suggestion existe, l'élève ne peut que la soutenir
- **Détection automatique des lieux** : reconnaît les abréviations (ex: "BAT C" ↔ "Bâtiment C")
- **Filtrage de contenu** : insultes, spam, contournements leetspeak (c0nnard, c@nnard...)
- **Page display TV** : affichage en direct des suggestions avec pins de couleur (rouge = populaire)
- **Tableau de bord CVL** : statistiques, graphiques, gestion des statuts et des lieux
- **Animations fluides** : transitions, skeleton loading, effets de vote

## Installation

```bash
cd lycee-suggestions
pip install -r requirements.txt
```

### Ollama (reformulation IA)

Installer Ollama : https://ollama.com

```bash
ollama pull mistral
```

Si Ollama n'est pas installé, le système utilise un reformulateur algorithmique en fallback.

Modèle configurable via variable d'environnement :

```bash
set OLLAMA_MODEL=mistral
```

Modèles recommandés (légers) : `mistral`, `llama3.2`, `phi3`

## Lancement

```bash
python app.py
```

Le site sera accessible sur `http://localhost:5000`

### Déploiement sur un VPS (production)

Un tutoriel pas à pas (Ubuntu, Nginx, HTTPS, Gunicorn, systemd, Ollama optionnel) est disponible dans **[docs/TUTORIEL_VPS.md](docs/TUTORIEL_VPS.md)**.

## Pages

| URL | Description |
|---|---|
| `/` | Interface élèves (mobile-first) |
| `/display` | Affichage TV en direct |
| `/admin` | Tableau de bord CVL/administration |

## Accès administration

- URL : `http://localhost:5000/admin`
- Mot de passe par défaut : `cvl2026`
- Configurable : `set ADMIN_PASSWORD=votre_mot_de_passe`

## Flow de soumission

1. L'élève écrit sa suggestion
2. Le système vérifie le contenu (filtre insultes/spam)
3. Le système cherche les doublons (mots-clés + similarité)
4. Si doublon → l'élève ne peut que soutenir la suggestion existante
5. Si nouveau → le LLM reformule, l'élève voit un aperçu et confirme
6. La suggestion est publiée avec catégorie et lieu auto-détectés

## Stack technique

- **Backend** : Python / Flask / SQLAlchemy / SQLite
- **IA locale** : Ollama (Mistral) + fallback scikit-learn (TF-IDF)
- **Frontend** : HTML / CSS / JS vanilla (mobile-first)
- **Graphiques** : Chart.js (CDN)
