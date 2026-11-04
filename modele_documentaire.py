"""
SAÉ 5.02 — Écrivain modèle intermédiaire -> modèle documentaire MongoDB (collection unique).

Entrée  : un modèle intermédiaire (dossier de 4 fichiers .jsonl + manifest.json),
          par défaut intermediaire/xml/ produit par xml_vers_intermediaire.py
Sorties : stack_exchange_documentaire.json  la collection unique stack_exchange,
            un document par ligne, distingués par type_document :
              question     la question + reponses[] (+ leurs commentaires[]) + commentaires[]
              utilisateur  le profil de l'utilisateur
              tag          le tag + extrait {} + wiki {}
              page         pages de wiki rattachées à aucun tag + pages d'aide (PostTypeId 7)
          exemples_documents.json  un document de chaque type, indenté (pour le rapport)

Choix de modélisation :
  - une seule collection (demande de l'enseignante) ; le champ type_document
    indique la nature du document ;
  - _id = "<type>_<Id>" car les Id XML se chevauchent d'un type à l'autre
    (ex. question 1 et utilisateur 1) ; l'Id d'origine est gardé dans id ;
  - imbrication de ce qui est toujours lu avec son parent et de taille bornée
    (réponses et commentaires dans la question, extrait et wiki dans le tag) ;
  - référence par identifiant pour ce qui est partagé (auteur.id -> id d'un
    document utilisateur, tags[] -> nom d'un document tag).
Structure sur le modèle de l'exemple du cours (Chapitre 2, exercice 2) :
  - noms de champs en français, en minuscules ;
  - champs liés regroupés en sous-objets : auteur {}, dates {}, statistiques {} ;
  - données liées imbriquées en tableaux d'objets de même forme.

Règles de transformation IR -> MongoDB :
  - valeur null dans l'IR  -> champ absent du document ("" reste "") ;
  - date ISO de l'IR       -> date MongoDB, écrite {"$date": "...Z"} (UTC) ;
  - entiers et liste des tags inchangés.

Usage : python modele_documentaire.py [dossier_IR] [fichier_de_sortie]
Le modèle intermédiaire n'est jamais modifié. Le script vérifie à la fin que l'on
retrouve exactement le modèle intermédiaire à partir des documents (aucune perte).
"""

import os
import sys
import json
from collections import Counter
from datetime import datetime

from intermediaire import ENTITES, date_ir, lire_ir

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_IR = os.path.join(DOSSIER, "intermediaire", "xml")
FICHIER_SORTIE = os.path.join(DOSSIER, "stack_exchange_documentaire.json")
FICHIER_EXEMPLES = os.path.join(DOSSIER, "exemples_documents.json")

QUESTION, REPONSE, EXTRAIT, WIKI = 1, 2, 4, 5
DATES = {ir for spec in ENTITES.values() for _, ir, t, _ in spec["champs"] if t == "date"}


# ---------------------------------------------------------------
# Correspondance champ IR -> chemin dans le document imbriqué
# (l'ordre de la liste donne l'ordre des champs dans le document)
# ---------------------------------------------------------------
CHAMPS_POST = [
    ("id",                       "id"),
    ("post_type_id",             "type_publication"),
    ("title",                    "titre"),
    ("tags",                     "tags"),
    ("body",                     "corps"),
    ("owner_user_id",            "auteur.id"),
    ("owner_display_name",       "auteur.nom"),
    ("last_editor_user_id",      "dernier_editeur.id"),
    ("last_editor_display_name", "dernier_editeur.nom"),
    ("creation_date",            "dates.creation"),
    ("last_edit_date",           "dates.derniere_modification"),
    ("last_activity_date",       "dates.derniere_activite"),
    ("closed_date",              "dates.fermeture"),
    ("score",                    "statistiques.score"),
    ("view_count",               "statistiques.vues"),
    ("answer_count",             "statistiques.nb_reponses"),
    ("comment_count",            "statistiques.nb_commentaires"),
    ("favorite_count",           "statistiques.nb_favoris"),
    ("accepted_answer_id",       "reponse_acceptee_id"),
]

CHAMPS_COMMENTAIRE = [
    ("id",                "id"),
    ("text",              "texte"),
    ("user_id",           "auteur.id"),
    ("user_display_name", "auteur.nom"),
    ("creation_date",     "date"),
    ("score",             "score"),
]

CHAMPS_USER = [
    ("id",                "id"),
    ("account_id",        "compte_id"),
    ("display_name",      "nom"),
    ("age",               "age"),
    ("location",          "localisation"),
    ("about_me",          "a_propos"),
    ("website_url",       "liens.site_web"),
    ("profile_image_url", "liens.photo"),
    ("creation_date",     "dates.creation"),
    ("last_access_date",  "dates.dernier_acces"),
    ("reputation",        "statistiques.reputation"),
    ("views",             "statistiques.vues"),
    ("up_votes",          "statistiques.votes.positifs"),
    ("down_votes",        "statistiques.votes.negatifs"),
]

CHAMPS_TAG = [
    ("id",       "id"),
    ("tag_name", "nom"),
    ("count",    "nb_questions"),
]


# ---------------------------------------------------------------
# Conversion d'une valeur, dans les deux sens
# ---------------------------------------------------------------
def vers_mongo(cle, valeur):
    """Date ISO de l'IR -> date MongoDB en JSON étendu (UTC)."""
    return {"$date": valeur + "Z"} if cle in DATES else valeur


def vers_ir(valeur):
    """Valeur lue dans un document -> valeur de l'IR.
    Accepte le JSON étendu {"$date": "...Z"} et les dates lues depuis MongoDB."""
    if isinstance(valeur, dict) and "$date" in valeur:
        return valeur["$date"].removesuffix("Z")
    if isinstance(valeur, datetime):
        return date_ir(valeur)
    return valeur


def restructurer(enregistrement, correspondance, *ignores):
    """Ligne de l'IR -> document imbriqué aux noms français. Les null de l'IR ne
    créent aucun champ. Refuse tout champ non prévu (aucune perte silencieuse)."""
    connus = {cle for cle, _ in correspondance} | set(ignores)
    inconnus = set(enregistrement) - connus
    if inconnus:
        raise ValueError(f"id {enregistrement.get('id')} : champs non prévus {sorted(inconnus)}")
    doc = {}
    for cle, chemin in correspondance:
        if cle in ignores or enregistrement.get(cle) is None:
            continue
        *parents, feuille = chemin.split(".")
        noeud = doc
        for p in parents:
            noeud = noeud.setdefault(p, {})
        noeud[feuille] = vers_mongo(cle, enregistrement[cle])
    return doc


def racine(type_document, doc):
    """Document de premier niveau de la collection unique : _id préfixé par le
    type (les Id XML se chevauchent d'un type à l'autre), Id d'origine gardé dans id."""
    return {"_id": f"{type_document}_{doc['id']}", "type_document": type_document, **doc}


def aplatir(doc, correspondance):
    """Opération inverse de restructurer : document imbriqué -> champs de l'IR."""
    plat = {}
    for cle, chemin in correspondance:
        noeud = doc
        for p in chemin.split("."):
            if not isinstance(noeud, dict) or p not in noeud:
                break
            noeud = noeud[p]
        else:
            plat[cle] = vers_ir(noeud)
    return plat


# ---------------------------------------------------------------
# ÉTAPE 1 — Construire les documents de la collection unique
# ---------------------------------------------------------------
def ordre_chrono(e):
    return (e["creation_date"], e["id"])


def construire(ir):
    posts = {p["id"]: p for p in ir["posts"]}
    documents = []

    # Commentaires regroupés par post (post_id devient implicite)
    commentaires_par_post = {}
    for c in sorted(ir["comments"], key=ordre_chrono):
        commentaires_par_post.setdefault(c["post_id"], []).append(
            restructurer(c, CHAMPS_COMMENTAIRE, "post_id"))

    # Réponses regroupées par question (parent_id et post_type_id deviennent implicites)
    reponses_par_question = {}
    for p in sorted(ir["posts"], key=ordre_chrono):
        if p["post_type_id"] == REPONSE:
            reponse = restructurer(p, CHAMPS_POST, "post_type_id", "parent_id")
            reponse["commentaires"] = commentaires_par_post.get(p["id"], [])
            reponses_par_question.setdefault(p["parent_id"], []).append(reponse)

    # Questions
    for p in ir["posts"]:
        if p["post_type_id"] == QUESTION:
            doc = racine("question", restructurer(p, CHAMPS_POST, "post_type_id", "parent_id"))
            doc["commentaires"] = commentaires_par_post.get(p["id"], [])
            doc["reponses"] = reponses_par_question.get(p["id"], [])
            documents.append(doc)

    # Utilisateurs (référencés par auteur.id, jamais imbriqués)
    for u in ir["users"]:
        documents.append(racine("utilisateur", restructurer(u, CHAMPS_USER)))

    # Tags avec leur extrait et leur wiki imbriqués
    pages_rattachees = set()
    for t in ir["tags"]:
        doc = racine("tag", restructurer(t, CHAMPS_TAG, "excerpt_post_id", "wiki_post_id"))
        if t["excerpt_post_id"] is not None:
            doc["extrait"] = restructurer(posts[t["excerpt_post_id"]], CHAMPS_POST, "post_type_id", "parent_id")
            pages_rattachees.add(t["excerpt_post_id"])
        if t["wiki_post_id"] is not None:
            doc["wiki"] = restructurer(posts[t["wiki_post_id"]], CHAMPS_POST, "post_type_id", "parent_id")
            pages_rattachees.add(t["wiki_post_id"])
        documents.append(doc)

    # Pages : tout post qui n'est ni question, ni réponse, ni rattaché à un tag
    for p in ir["posts"]:
        if p["post_type_id"] not in (QUESTION, REPONSE) and p["id"] not in pages_rattachees:
            documents.append(racine("page", restructurer(p, CHAMPS_POST, "parent_id")))

    return documents


# ---------------------------------------------------------------
# ÉTAPE 2 — Écrire la collection (un document par ligne) et les exemples
# ---------------------------------------------------------------
def ecrire(documents, chemin):
    with open(chemin, "w", encoding="utf-8") as f:
        f.write("[\n")
        f.write(",\n".join(json.dumps(d, ensure_ascii=False) for d in documents))
        f.write("\n]\n")
    print(f"  {chemin} ({len(documents)} documents, {os.path.getsize(chemin) / 1e6:.1f} Mo)")


def choisir_exemples(documents):
    """Le plus petit document de chaque type qui montre toute la structure
    (réponses, commentaires imbriqués, réponse acceptée, extrait et wiki)."""
    def complet(d):
        t = d["type_document"]
        if t == "question":
            return (len(d["reponses"]) >= 2 and d["commentaires"]
                    and any(r["commentaires"] for r in d["reponses"])
                    and "reponse_acceptee_id" in d)
        if t == "utilisateur":
            return (d.get("localisation") and d.get("a_propos")
                    and d.get("liens", {}).get("site_web") and "age" in d)
        if t == "tag":
            return "extrait" in d and "wiki" in d and d["wiki"].get("corps")
        return True

    exemples = {}
    for d in sorted(documents, key=lambda d: len(json.dumps(d, ensure_ascii=False))):
        if d["type_document"] not in exemples and complet(d):
            exemples[d["type_document"]] = d
    return [exemples[t] for t in ("question", "utilisateur", "tag", "page") if t in exemples]


def ecrire_exemples(exemples, chemin):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(exemples, f, ensure_ascii=False, indent=2)
    print(f"  {chemin} ({len(exemples)} documents d'exemple)")


# ---------------------------------------------------------------
# ÉTAPE 3 — Opération inverse : documents -> modèle intermédiaire
# (sert à la validation, et au lecteur MongoDB -> IR)
# ---------------------------------------------------------------
def deplier(documents):
    """Documents de la collection (lus dans un fichier ou dans MongoDB) ->
    lignes de l'IR {entité: [enregistrements complets]}, null compris."""
    ir = {e: [] for e in ENTITES}

    def ajouter(entite, champs):
        ligne = {ir_: None for _, ir_, _, _ in ENTITES[entite]["champs"]}
        ligne.update(champs)
        ir[entite].append(ligne)

    def deplier_commentaires(liste, post_id):
        for c in liste:
            ajouter("comments", {**aplatir(c, CHAMPS_COMMENTAIRE), "post_id": post_id})

    for d in documents:
        t = d["type_document"]
        if t == "question":
            ajouter("posts", {**aplatir(d, CHAMPS_POST), "post_type_id": QUESTION})
            deplier_commentaires(d["commentaires"], d["id"])
            for r in d["reponses"]:
                ajouter("posts", {**aplatir(r, CHAMPS_POST),
                                  "post_type_id": REPONSE, "parent_id": d["id"]})
                deplier_commentaires(r["commentaires"], r["id"])
        elif t == "utilisateur":
            ajouter("users", aplatir(d, CHAMPS_USER))
        elif t == "tag":
            champs = aplatir(d, CHAMPS_TAG)
            if "extrait" in d:
                champs["excerpt_post_id"] = d["extrait"]["id"]
                ajouter("posts", {**aplatir(d["extrait"], CHAMPS_POST), "post_type_id": EXTRAIT})
            if "wiki" in d:
                champs["wiki_post_id"] = d["wiki"]["id"]
                ajouter("posts", {**aplatir(d["wiki"], CHAMPS_POST), "post_type_id": WIKI})
            ajouter("tags", champs)
        else:
            ajouter("posts", aplatir(d, CHAMPS_POST))
    return ir


def comparer_ir(attendu, obtenu, afficher=print):
    """Compare deux IR entité par entité, ligne par ligne (par id)."""
    ok = True
    for entite in ENTITES:
        a = {e["id"]: e for e in attendu[entite]}
        o = {e["id"]: e for e in obtenu[entite]}
        manquants = [k for k in a if k not in o]
        en_trop = [k for k in o if k not in a]
        differents = [k for k in a if k in o and a[k] != o[k]]
        statut = "OK" if not (manquants or en_trop or differents) else "ÉCART"
        ok &= statut == "OK"
        afficher(f"  {entite:9} attendues={len(a):6}  retrouvées={len(o):6}  manquantes={len(manquants)}"
                 f"  en trop={len(en_trop)}  différentes={len(differents)} {differents[:5]}  {statut}")
    return ok


def valider(ir, documents):
    ok = True
    print("\nValidation :")
    # 3a. Effectifs par type et unicité de _id dans la collection
    for t, n in Counter(d["type_document"] for d in documents).items():
        print(f"  {t:12} {n:6} documents")
    doublons = len(documents) - len({d["_id"] for d in documents})
    print(f"  TOTAL        {len(documents):6} documents, _id en double : {doublons}")
    ok &= doublons == 0

    # 3b. Aller-retour : les documents redonnent exactement le modèle intermédiaire
    print("Modèle intermédiaire retrouvé à partir des documents :")
    ok &= comparer_ir(ir, deplier(documents))

    # 3c. Taille des documents (limite MongoDB : 16 Mo)
    taille, _id = max((len(json.dumps(d, ensure_ascii=False).encode("utf-8")), d["_id"])
                      for d in documents)
    print(f"  Plus gros document : _id={_id} ({taille / 1024:.1f} Ko)")
    ok &= taille < 16 * 1024 * 1024

    print("MODÈLE VALIDÉ : aucune perte d'information." if ok else "ÉCART : voir ci-dessus.")
    return ok


def main():
    dossier_ir = sys.argv[1] if len(sys.argv) > 1 else DOSSIER_IR
    sortie = sys.argv[2] if len(sys.argv) > 2 else FICHIER_SORTIE
    if not os.path.isfile(os.path.join(dossier_ir, "manifest.json")):
        sys.exit(f"Modèle intermédiaire introuvable : {dossier_ir} (lancer d'abord xml_vers_intermediaire.py)")
    manifest, ir = lire_ir(dossier_ir)
    print(f"Lu : modèle intermédiaire « {manifest['source']} » {dossier_ir}  "
          f"{ {e: len(v) for e, v in ir.items()} }")
    documents = construire(ir)
    print("Écrit :")
    ecrire(documents, sortie)
    ecrire_exemples(choisir_exemples(documents), FICHIER_EXEMPLES)
    sys.exit(0 if valider(ir, documents) else 1)


if __name__ == "__main__":
    main()
