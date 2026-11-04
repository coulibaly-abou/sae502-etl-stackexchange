# SAÉ 5.02 : mini-ETL Stack Exchange entre XML, PostgreSQL et MongoDB

Projet de BUT 3 Science des données (parcours EMS), IUT de Carcassonne, année 2026-2027.
Encadrante : Mme Mouna Kamel.
Auteurs : Aboubakar COULIBALY, Victor MARIN, Timothée VINAZZA.

Le projet migre les données publiques d'un site Stack Exchange (4 fichiers XML, 20 515 enregistrements)
vers PostgreSQL (modèle relationnel) et MongoDB (modèle documentaire). Toutes les migrations passent
par un **modèle intermédiaire** (format pivot JSON Lines), indépendant des deux SGBD : chaque source a
un lecteur qui produit ce modèle, et chaque cible un écrivain qui le consomme.

```
XML ─────────┐                           ┌──► PostgreSQL  (postgres_etl.py charger)
PostgreSQL ──┼──►  modèle intermédiaire ──┤
MongoDB ─────┘     intermediaire/<source> └──► MongoDB     (import_3_collections.py)
```

Ce dépôt correspond au **livrable 1** (missions 1, 2 et 3 du sujet) : modélisation, modèle d'échange
commun et problèmes de migration vers PostgreSQL. Le rapport complet est dans
[`docs/rapport/Rapport_SAE502_Livrable1.pdf`](docs/rapport/Rapport_SAE502_Livrable1.pdf).

## Les trois missions

| Mission | Résultat | Où le trouver |
|---|---|---|
| 1. Modélisation | MCD corrigé (7 entités), MLD et schéma PostgreSQL de 8 tables ; modèle MongoDB en 3 collections (users, posts, tags) | `docs/modelisation/`, `schema.sql`, `modele_documentaire_3_collections/` |
| 2. Modèle d'échange commun | 4 fichiers JSON Lines + un manifeste (effectifs, empreintes SHA-256), décrits par 4 JSON Schema | `intermediaire/xml/`, `schemas/`, `intermediaire.py` |
| 3. Problèmes de migration vers PostgreSQL | 27 problèmes recensés, traduits en 11 règles de transformation | rapport, section 3 ; `schema.sql`, `postgres_etl.py` |

## Contenu du dépôt

| Chemin | Rôle |
|---|---|
| `Users.xml`, `Posts.xml`, `Comments.xml`, `Tags.xml` | données sources (jamais modifiées) |
| `intermediaire.py` | définition du modèle intermédiaire (46 champs), JSON Schema, écriture, lecture, validation |
| `xml_vers_intermediaire.py` | XML → modèle intermédiaire, avec vérification de l'aller-retour |
| `modele_documentaire.py` | fonctions communes de restructuration et de comparaison |
| `modele_3_collections.py` | modèle MongoDB en 3 collections : construction, opération inverse, validateurs, index |
| `import_3_collections.py` | modèle intermédiaire → MongoDB (base `stack_exchange_3col`) |
| `schema.sql` | création des 8 tables PostgreSQL |
| `postgres_etl.py` | `inspecter` la base, `charger` modèle intermédiaire → PostgreSQL, `exporter` PostgreSQL → modèle intermédiaire |
| `verification.sql` | requêtes de contrôle après import PostgreSQL (lecture seule) |
| `schemas/` | JSON Schema des 4 entités du modèle intermédiaire |
| `intermediaire/xml/` | modèle intermédiaire produit à partir des XML |
| `modele_documentaire_3_collections/` | format détaillé du modèle MongoDB (`format.json`) et un document exemple par collection |
| `journaux/` | journaux d'exécution de référence (PostgreSQL et MongoDB, 29/10/2026) |
| `docs/` | rapport (docx et PDF), sujet, MCD et MLD (Looping), schémas des modèles, captures MongoDB Compass |

## Prérequis

- Python 3.10 ou plus récent (testé avec 3.14)
- MongoDB en local sur `localhost:27017` (testé avec 8.3)
- PostgreSQL avec une base `Stackexchange` sur `localhost:5434` (testé avec PostgreSQL 18)
- Bibliothèques : `pip install -r requirements.txt` (pymongo, psycopg2-binary, jsonschema)

## Configuration

**PostgreSQL** : hôte `localhost`, port `5434`, base `Stackexchange`, utilisateur `postgres`
(fixés dans `postgres_etl.py`). Le mot de passe n'est écrit dans aucun fichier : il est lu dans la
variable d'environnement `STACKEXCHANGE_DB_PASSWORD`.

```powershell
# PowerShell, pour la session en cours
$env:STACKEXCHANGE_DB_PASSWORD = "votre_mot_de_passe"
```

**MongoDB** : `mongodb://localhost:27017`, sans authentification (fixé dans `import_3_collections.py`).

## Commandes, dans l'ordre

```powershell
pip install -r requirements.txt

# 1. XML -> modèle intermédiaire (intermediaire/xml/), puis validation
python xml_vers_intermediaire.py

# 2. Modèle intermédiaire -> MongoDB (3 collections), puis validation
python import_3_collections.py

# 3. Modèle intermédiaire -> PostgreSQL (après avoir défini STACKEXCHANGE_DB_PASSWORD)
python postgres_etl.py inspecter          # examine la base, lecture seule
python postgres_etl.py charger            # crée les tables absentes, importe, contrôle
psql -h localhost -p 5434 -U postgres -d Stackexchange -f verification.sql
python postgres_etl.py exporter intermediaire/postgresql   # PostgreSQL -> modèle intermédiaire
```

Tous les scripts peuvent être relancés : un enregistrement déjà présent n'est jamais remplacé ni dupliqué,
et aucune base, table ou collection n'est supprimée ni vidée.

## Contrôles effectués par les scripts

- effectifs par fichier, table ou collection, comparés aux XML ;
- comparaison champ par champ ;
- références entre entités (aucune orpheline) ;
- **aller-retour** : le modèle intermédiaire relu depuis la destination doit être identique à celui des XML ;
- contraintes et validateurs : des enregistrements invalides doivent être refusés.

## Résultats observés

| Étape | Résultat |
|---|---|
| XML → modèle intermédiaire | 20 515 lignes (11 961 utilisateurs, 3 900 posts, 4 440 commentaires, 214 tags), toutes reconstituées à l'identique ; 0 référence orpheline |
| Modèle intermédiaire → MongoDB | users 11 961, posts 1 717, tags 214 ; 0 écart document par document ; modèle intermédiaire retrouvé à l'identique ; relance sans doublon |
| Modèle intermédiaire → PostgreSQL | 8 tables : utilisateur 11 961, account 11 960, tag 214, post 3 900, question 1 444, reponse 2 183, commentaire 4 440, etiqueter 3 471 ; modèle intermédiaire relu à l'identique ; 4 insertions invalides refusées ; relance sans doublon |
| PostgreSQL → modèle intermédiaire | empreintes SHA-256 identiques à celles de `intermediaire/xml/` pour les 4 fichiers |

## Limites connues

- Les dates du XML n'ont pas de fuseau horaire : elles sont considérées en UTC.
- Tout est traité en mémoire : c'est adapté à ce jeu de données (environ 14 Mo), pas à un dump complet de Stack Overflow.
- Les migrations MongoDB → PostgreSQL et PostgreSQL → MongoDB passent par le modèle intermédiaire, mais ne sont
  pas encore enchaînées par une commande unique (mission 4, livrable 2).

## Données

Les fichiers XML proviennent du dump public de Stack Exchange, publié sous licence
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
