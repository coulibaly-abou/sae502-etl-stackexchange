"""
SAÉ 5.02 — Modèle intermédiaire (IR) : format pivot commun à toutes les migrations.

    XML ────────┐                    ┌──► PostgreSQL
    PostgreSQL ─┼──►  IR (JSONL)  ───┤
    MongoDB ────┘                    └──► MongoDB

Ce module est la SEULE définition du format. Il contient :
  - le dictionnaire des 46 champs (nom XML -> nom IR, type, signification) ;
  - la construction des JSON Schema (un par entité) ;
  - l'écriture d'une IR (4 fichiers .jsonl + manifest.json) ;
  - la lecture et la validation complète d'une IR, quelle que soit sa source.

Format (une IR = un dossier) :
    users.jsonl  posts.jsonl  comments.jsonl  tags.jsonl  manifest.json
  - JSON Lines UTF-8 : un enregistrement par ligne, lignes triées par id ;
  - structure plate, une entité par fichier, calquée sur les fichiers XML ;
  - noms en snake_case ; TOUS les champs sont présents, null = absent de la
    source ("" reste une chaîne vide : absent et vide sont distingués) ;
  - entiers JSON pour les nombres ; dates en texte ISO 8601 sans fuseau,
    toujours au format AAAA-MM-JJTHH:MM:SS.mmm (heure UTC du dump) ;
  - seule exception à la forme plate : posts.tags est une liste ordonnée.

Usage :
    python intermediaire.py schemas           écrit schemas/<entité>.schema.json
    python intermediaire.py valider <dossier> valide une IR (code de sortie 0 si OK)
Prérequis : pip install jsonschema
"""

import os
import sys
import json
import hashlib
from collections import Counter
from datetime import datetime, timezone

VERSION_SCHEMA = "1.0"
DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_SCHEMAS = os.path.join(DOSSIER, "schemas")
FORMAT_DATE = "%Y-%m-%dT%H:%M:%S.%f"            # écrit avec 3 chiffres de millisecondes
MOTIF_DATE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$"

# ---------------------------------------------------------------
# Dictionnaire des données : (nom XML, nom IR, type, signification)
# types : entier | compteur (entier >= 0) | texte | date | tags
# L'ordre de la liste est l'ordre des champs dans chaque ligne.
# ---------------------------------------------------------------
ENTITES = {
    "users": {
        "xml": "Users.xml",
        "champs": [
            ("Id", "id", "entier", "Identifiant de l'utilisateur sur le site (-1 = compte système Community)"),
            ("AccountId", "account_id", "entier", "Identifiant global du compte sur tout le réseau Stack Exchange"),
            ("DisplayName", "display_name", "texte", "Nom affiché"),
            ("Reputation", "reputation", "compteur", "Score de réputation"),
            ("CreationDate", "creation_date", "date", "Date de création du compte"),
            ("LastAccessDate", "last_access_date", "date", "Dernier chargement d'une page du site"),
            ("Location", "location", "texte", "Localisation déclarée"),
            ("AboutMe", "about_me", "texte", "Présentation du profil (HTML)"),
            ("Views", "views", "compteur", "Nombre de consultations du profil"),
            ("UpVotes", "up_votes", "compteur", "Nombre de votes positifs émis"),
            ("DownVotes", "down_votes", "compteur", "Nombre de votes négatifs émis"),
            ("Age", "age", "entier", "Âge déclaré"),
            ("ProfileImageUrl", "profile_image_url", "texte", "URL de l'image de profil"),
            ("WebsiteUrl", "website_url", "texte", "URL du site web du profil"),
        ],
    },
    "posts": {
        "xml": "Posts.xml",
        "champs": [
            ("Id", "id", "entier", "Identifiant du post"),
            ("PostTypeId", "post_type_id", "entier", "Type : 1 question, 2 réponse, 4 extrait de tag, 5 wiki de tag, 7 page d'aide"),
            ("ParentId", "parent_id", "entier", "Pour une réponse : id de la question"),
            ("AcceptedAnswerId", "accepted_answer_id", "entier", "Pour une question : id de la réponse acceptée"),
            ("OwnerUserId", "owner_user_id", "entier", "Auteur (users.id) ; null si le compte a été supprimé"),
            ("OwnerDisplayName", "owner_display_name", "texte", "Nom de l'auteur quand owner_user_id est absent"),
            ("LastEditorUserId", "last_editor_user_id", "entier", "Dernier éditeur (users.id)"),
            ("LastEditorDisplayName", "last_editor_display_name", "texte", "Nom du dernier éditeur quand il n'est pas identifié"),
            ("Title", "title", "texte", "Titre (questions uniquement)"),
            ("Body", "body", "texte", "Contenu en HTML rendu"),
            ("Tags", "tags", "tags", "Tags de la question, liste ordonnée (XML : \"<a><b>\")"),
            ("Score", "score", "entier", "Score (votes positifs - votes négatifs)"),
            ("ViewCount", "view_count", "compteur", "Nombre de vues (questions uniquement)"),
            ("AnswerCount", "answer_count", "compteur", "Nombre de réponses (questions uniquement)"),
            ("CommentCount", "comment_count", "compteur", "Nombre de commentaires"),
            ("FavoriteCount", "favorite_count", "compteur", "Ancien compteur de favoris"),
            ("CreationDate", "creation_date", "date", "Date de création"),
            ("LastEditDate", "last_edit_date", "date", "Date de la dernière modification du contenu"),
            ("LastActivityDate", "last_activity_date", "date", "Date de la dernière activité"),
            ("ClosedDate", "closed_date", "date", "Date de fermeture de la question"),
        ],
    },
    "comments": {
        "xml": "Comments.xml",
        "champs": [
            ("Id", "id", "entier", "Identifiant du commentaire"),
            ("PostId", "post_id", "entier", "Post commenté (posts.id)"),
            ("UserId", "user_id", "entier", "Auteur (users.id) ; null si le compte a été supprimé"),
            ("UserDisplayName", "user_display_name", "texte", "Nom de l'auteur quand user_id est absent"),
            ("Score", "score", "entier", "Score du commentaire"),
            ("Text", "text", "texte", "Texte du commentaire"),
            ("CreationDate", "creation_date", "date", "Date de création"),
        ],
    },
    "tags": {
        "xml": "Tags.xml",
        "champs": [
            ("Id", "id", "entier", "Identifiant du tag"),
            ("TagName", "tag_name", "texte", "Nom du tag (unique)"),
            ("Count", "count", "compteur", "Nombre de questions utilisant le tag"),
            ("ExcerptPostId", "excerpt_post_id", "entier", "Post contenant l'extrait du tag (posts.id)"),
            ("WikiPostId", "wiki_post_id", "entier", "Post contenant le wiki du tag (posts.id)"),
        ],
    },
}

# Champs toujours renseignés (jamais null) : présents dans 100 % des lignes XML
OBLIGATOIRES = {
    "users": {"id", "display_name", "reputation", "creation_date", "last_access_date",
              "views", "up_votes", "down_votes"},
    "posts": {"id", "post_type_id", "body", "score", "comment_count",
              "creation_date", "last_activity_date"},
    "comments": {"id", "post_id", "score", "text", "creation_date"},
    "tags": {"id", "tag_name", "count"},
}


# ---------------------------------------------------------------
# JSON Schema (draft 2020-12) d'une ligne de chaque entité
# ---------------------------------------------------------------
def _type_json(type_champ):
    if type_champ == "entier":
        return {"type": "integer"}
    if type_champ == "compteur":
        return {"type": "integer", "minimum": 0}
    if type_champ == "texte":
        return {"type": "string"}
    if type_champ == "date":
        return {"type": "string", "pattern": MOTIF_DATE}
    if type_champ == "tags":
        return {"type": "array", "minItems": 1, "maxItems": 5, "uniqueItems": True,
                "items": {"type": "string", "minLength": 1, "pattern": "^[^<>|]+$"}}
    raise ValueError(type_champ)


def _nul(*champs):
    return {"properties": {c: {"type": "null"} for c in champs}}


def _non_nul(*champs):
    return {"properties": {c: {"not": {"type": "null"}} for c in champs}}


def _pas_les_deux(champ_id, champ_nom):
    """Un auteur est identifié soit par son id, soit par son nom, jamais les deux."""
    return {"if": {"properties": {champ_id: {"type": "integer"}}},
            "then": _nul(champ_nom)}


# Règles entre champs d'une même ligne (vérifiées sur les données)
REGLES = {
    "posts": [
        {"if": {"properties": {"post_type_id": {"const": 1}}},
         "then": _non_nul("title", "tags", "view_count", "answer_count")},
        {"if": {"properties": {"post_type_id": {"not": {"const": 1}}}},
         "then": _nul("title", "tags", "view_count", "answer_count",
                      "accepted_answer_id", "closed_date")},
        {"if": {"properties": {"post_type_id": {"const": 2}}},
         "then": _non_nul("parent_id"), "else": _nul("parent_id")},
        _pas_les_deux("owner_user_id", "owner_display_name"),
        # pas de règle pour le dernier éditeur : les posts 2619 et 3580 ont
        # à la fois last_editor_user_id et last_editor_display_name dans le XML
    ],
    "comments": [
        _pas_les_deux("user_id", "user_display_name"),
    ],
}


def schema_json(entite):
    proprietes = {}
    for nom_xml, nom_ir, type_champ, sens in ENTITES[entite]["champs"]:
        t = _type_json(type_champ)
        if nom_ir not in OBLIGATOIRES[entite]:
            t = {"anyOf": [t, {"type": "null"}]}
        proprietes[nom_ir] = {"description": f"{sens} — XML : {ENTITES[entite]['xml']} / {nom_xml}", **t}
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://sae502.local/schemas/{entite}.schema.json",
        "title": f"Modèle intermédiaire — une ligne de {entite}.jsonl (version {VERSION_SCHEMA})",
        "type": "object",
        "required": [c[1] for c in ENTITES[entite]["champs"]],   # tous présents (null autorisé)
        "additionalProperties": False,
        "properties": proprietes,
    }
    if entite == "posts":
        proprietes["post_type_id"]["enum"] = [1, 2, 3, 4, 5, 6, 7, 8]
    if REGLES.get(entite):
        schema["allOf"] = REGLES[entite]
    return schema


def ecrire_schemas(dossier=DOSSIER_SCHEMAS):
    os.makedirs(dossier, exist_ok=True)
    for entite in ENTITES:
        chemin = os.path.join(dossier, f"{entite}.schema.json")
        with open(chemin, "w", encoding="utf-8") as f:
            json.dump(schema_json(entite), f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  {chemin}")


# ---------------------------------------------------------------
# Écriture et lecture d'une IR
# ---------------------------------------------------------------
def date_ir(valeur):
    """datetime (naïf UTC ou avec fuseau) -> texte canonique AAAA-MM-JJTHH:MM:SS.mmm"""
    if valeur.tzinfo is not None:
        valeur = valeur.astimezone(timezone.utc).replace(tzinfo=None)
    return valeur.strftime(FORMAT_DATE)[:-3]


def ligne_json(enregistrement):
    return json.dumps(enregistrement, ensure_ascii=False)


def sha256_fichier(chemin):
    h = hashlib.sha256()
    with open(chemin, "rb") as f:
        for bloc in iter(lambda: f.read(1 << 20), b""):
            h.update(bloc)
    return h.hexdigest()


def ecrire_ir(dossier, source, donnees, infos_source=None):
    """donnees : {entité: [enregistrements]} -> 4 fichiers .jsonl + manifest.json.
    Chaque enregistrement est remis dans l'ordre du dictionnaire et trié par id."""
    os.makedirs(dossier, exist_ok=True)
    manifest = {
        "schema_version": VERSION_SCHEMA,
        "source": source,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "format": "JSON Lines UTF-8, un enregistrement par ligne, trié par id croissant",
        "source_details": infos_source or {},
        "entities": {},
    }
    for entite, spec in ENTITES.items():
        noms = [c[1] for c in spec["champs"]]
        lignes = sorted(donnees[entite], key=lambda e: e["id"])
        fichier = f"{entite}.jsonl"
        chemin = os.path.join(dossier, fichier)
        with open(chemin, "w", encoding="utf-8", newline="\n") as f:
            for e in lignes:
                f.write(ligne_json({n: e[n] for n in noms}) + "\n")
        manifest["entities"][entite] = {
            "file": fichier,
            "schema": f"schemas/{entite}.schema.json",
            "count": len(lignes),
            "sha256": sha256_fichier(chemin),
            "non_null": {n: sum(e[n] is not None for e in lignes) for n in noms},
        }
    with open(os.path.join(dossier, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return manifest


def lire_ir(dossier):
    with open(os.path.join(dossier, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    donnees = {}
    for entite in ENTITES:
        with open(os.path.join(dossier, f"{entite}.jsonl"), encoding="utf-8") as f:
            donnees[entite] = [json.loads(l) for l in f if l.strip()]
    return manifest, donnees


# ---------------------------------------------------------------
# Validation d'une IR (indépendante de sa source)
# ---------------------------------------------------------------
def valider_ir(dossier, afficher=print):
    from jsonschema import Draft202012Validator

    ok = True

    def controle(nom, reussi, detail=""):
        nonlocal ok
        ok &= bool(reussi)
        afficher(f"  [{'OK   ' if reussi else 'ÉCHEC'}] {nom}" + (f" — {detail}" if detail else ""))

    afficher(f"Validation de l'IR : {dossier}")
    manifest, donnees = lire_ir(dossier)
    controle("version du schéma", manifest.get("schema_version") == VERSION_SCHEMA,
             manifest.get("schema_version"))

    # 1. Fichiers conformes au manifest (effectifs, empreintes, valeurs non nulles)
    afficher("1. Fichiers et manifest")
    for entite, lignes in donnees.items():
        m = manifest["entities"][entite]
        sha = sha256_fichier(os.path.join(dossier, m["file"]))
        noms = [c[1] for c in ENTITES[entite]["champs"]]
        non_null = {n: sum(e.get(n) is not None for e in lignes) for n in noms}
        controle(f"{entite:9} effectif", len(lignes) == m["count"], f"{len(lignes)} lignes")
        controle(f"{entite:9} sha256", sha == m["sha256"], sha[:16] + "…")
        ecarts = [n for n in noms if non_null[n] != m["non_null"].get(n)]
        controle(f"{entite:9} valeurs non nulles par champ", not ecarts, ", ".join(ecarts))

    # 2. Chaque ligne respecte le JSON Schema de son entité
    afficher("2. JSON Schema (types, champs requis, valeurs nulles, règles entre champs)")
    for entite, lignes in donnees.items():
        validateur = Draft202012Validator(schema_json(entite))
        erreurs = [(e.get("id"), err.message) for e in lignes
                   for err in validateur.iter_errors(e)]
        controle(f"{entite:9} {len(lignes)} lignes conformes", not erreurs,
                 f"{len(erreurs)} erreur(s), ex. {erreurs[:3]}" if erreurs else "")

    # 3. Clés : id uniques et triés
    afficher("3. Identifiants")
    for entite, lignes in donnees.items():
        ids = [e["id"] for e in lignes]
        controle(f"{entite:9} id uniques et triés", ids == sorted(set(ids)))
    noms_tags = [t["tag_name"] for t in donnees["tags"]]
    controle("tags      tag_name unique", len(noms_tags) == len(set(noms_tags)))

    # 4. Intégrité référentielle (0 référence orpheline)
    afficher("4. Références entre entités")
    users = {u["id"] for u in donnees["users"]}
    posts = {p["id"]: p for p in donnees["posts"]}
    tags = set(noms_tags)

    def orphelins(nom, valeurs, cible):
        manquants = [v for v in valeurs if v is not None and v not in cible]
        controle(f"{nom} -> référence existante", not manquants,
                 f"{len(manquants)} orphelin(s) {manquants[:5]}" if manquants else "")

    orphelins("posts.owner_user_id", [p["owner_user_id"] for p in posts.values()], users)
    orphelins("posts.last_editor_user_id", [p["last_editor_user_id"] for p in posts.values()], users)
    orphelins("comments.user_id", [c["user_id"] for c in donnees["comments"]], users)
    orphelins("comments.post_id", [c["post_id"] for c in donnees["comments"]], posts)
    orphelins("tags.excerpt_post_id", [t["excerpt_post_id"] for t in donnees["tags"]], posts)
    orphelins("tags.wiki_post_id", [t["wiki_post_id"] for t in donnees["tags"]], posts)
    orphelins("posts.tags[]", [x for p in posts.values() for x in (p["tags"] or [])], tags)
    questions = {i for i, p in posts.items() if p["post_type_id"] == 1}
    orphelins("posts.parent_id (question)", [p["parent_id"] for p in posts.values()], questions)
    mauvaises = [p["id"] for p in posts.values() if p["accepted_answer_id"] is not None
                 and posts.get(p["accepted_answer_id"], {}).get("parent_id") != p["id"]]
    controle("posts.accepted_answer_id -> réponse de cette question", not mauvaises,
             f"{len(mauvaises)} écart(s) {mauvaises[:5]}" if mauvaises else "")

    afficher("IR VALIDE." if ok else "IR NON VALIDE : voir les échecs ci-dessus.")
    return ok


if __name__ == "__main__":
    commande = sys.argv[1] if len(sys.argv) > 1 else ""
    if commande == "schemas":
        ecrire_schemas()
    elif commande == "valider" and len(sys.argv) > 2:
        sys.exit(0 if valider_ir(sys.argv[2]) else 1)
    else:
        sys.exit(__doc__)
