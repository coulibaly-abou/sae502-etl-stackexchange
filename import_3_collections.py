"""
SAÉ 5.02 — Migration modèle intermédiaire -> MongoDB (modèle en 3 collections).

Base        : stack_exchange_3col
Collections : users, posts, tags (structure : modele_3_collections.py)

Usage : python import_3_collections.py [dossier_IR]
        (par défaut : intermediaire/xml, produit par xml_vers_intermediaire.py)
Prérequis : pip install pymongo  +  serveur MongoDB lancé sur localhost:27017

Sécurité :
  - le modèle intermédiaire est seulement lu ;
  - aucune base ni collection n'est supprimée ou vidée ;
  - un document déjà présent (même _id) n'est JAMAIS remplacé : relancer le
    script ne crée aucun doublon.

Validation après import :
  - effectifs par collection ;
  - comparaison document par document (attendu <-> MongoDB) ;
  - références entre collections (aucune orpheline) ;
  - le modèle intermédiaire est retrouvé à l'identique à partir de la base ;
  - un document invalide est refusé par le validateur.
"""

import os
import sys
import json
from datetime import datetime

from bson import json_util
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ServerSelectionTimeoutError, BulkWriteError, WriteError

from intermediaire import lire_ir
from modele_documentaire import comparer_ir
from modele_3_collections import construire, deplier, VALIDATEURS, INDEX

URI_MONGO = "mongodb://localhost:27017"
NOM_BASE = "stack_exchange_3col"
DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_JOURNAL = os.path.join(DOSSIER, "journal_import_3_collections.txt")
TAILLE_LOT = 500

journal = []


def afficher(message=""):
    print(message)
    journal.append(message)


def connecter():
    client = MongoClient(URI_MONGO, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except ServerSelectionTimeoutError:
        sys.exit(f"Impossible de joindre MongoDB sur {URI_MONGO} : le serveur est-il lancé ?")
    return client


def preparer(base, nom):
    if nom not in base.list_collection_names():
        base.create_collection(nom, validator=VALIDATEURS[nom],
                               validationLevel="strict", validationAction="error")
        afficher(f"  {nom:6} créée avec son validateur $jsonSchema")
    else:
        afficher(f"  {nom:6} existe déjà ({base[nom].count_documents({})} documents) : ni vidée ni modifiée")
    for index in INDEX[nom]:
        base[nom].create_index([(c, ASCENDING) for c in index["cles"]], name=index["nom"],
                               unique=index.get("unique", False))
    return base[nom]


def inserer(collection, documents):
    inseres = deja = 0
    rejetes = []
    for debut in range(0, len(documents), TAILLE_LOT):
        lot = documents[debut:debut + TAILLE_LOT]
        try:
            collection.insert_many(lot, ordered=False)
            echecs = {}
        except BulkWriteError as erreur:
            echecs = {e["index"]: e for e in erreur.details["writeErrors"]}
        for position, doc in enumerate(lot):
            if position not in echecs:
                inseres += 1
            elif echecs[position]["code"] == 11000:      # même _id déjà en base
                deja += 1
            else:                                        # refus du validateur
                rejetes.append((doc["_id"], echecs[position]["errmsg"][:200]))
    return inseres, deja, rejetes


def verifier(base, attendu, ir):
    ok = True
    en_base = {n: list(base[n].find({})) for n in attendu}

    afficher("\nEffectifs :")
    for n, docs in attendu.items():
        nb = base[n].count_documents({})
        statut = "OK" if nb == len(docs) else "ÉCART"
        ok &= statut == "OK"
        afficher(f"  {n:6} attendus={len(docs):6}  MongoDB={nb:6}  {statut}")

    afficher("\nComparaison document par document (attendu <-> MongoDB) :")
    for n, docs in attendu.items():
        b = {d["_id"]: d for d in en_base[n]}
        manquants = [d["_id"] for d in docs if d["_id"] not in b]
        differents = [d["_id"] for d in docs if d["_id"] in b and b[d["_id"]] != d]
        en_trop = len(b) - (len(docs) - len(manquants))
        statut = "OK" if not (manquants or differents or en_trop) else "ÉCART"
        ok &= statut == "OK"
        afficher(f"  {n:6} absents={len(manquants)}  différents={len(differents)} {differents[:5]}  en trop={en_trop}  {statut}")

    afficher("\nRéférences entre collections :")
    users = {d["_id"] for d in en_base["users"]}
    posts = {d["_id"] for d in en_base["posts"]}
    tags = {d["tag_name"] for d in en_base["tags"]}
    personnes = []
    for p in en_base["posts"]:
        elements = [p] + p.get("answers", []) + p["comments"] + [c for a in p.get("answers", []) for c in a["comments"]]
        personnes += [e[k]["user_id"] for e in elements for k in ("owner", "last_editor", "user")
                      if "user_id" in e.get(k, {})]
    controles = {
        "auteurs, éditeurs -> users._id": [u for u in personnes if u not in users],
        "posts.tags[] -> tags.tag_name": [t for p in en_base["posts"] for t in p.get("tags", []) if t not in tags],
        "tags extrait / wiki -> posts._id": [x for t in en_base["tags"] for x in (t.get("excerpt_post_id"), t.get("wiki_post_id"))
                                            if x is not None and x not in posts],
        "accepted_answer_id -> answers[]": [p["_id"] for p in en_base["posts"] if "accepted_answer_id" in p
                                           and p["accepted_answer_id"] not in {a["answer_id"] for a in p["answers"]}],
    }
    for nom, orphelins in controles.items():
        ok &= not orphelins
        afficher(f"  {nom:36} orphelines : {len(orphelins)}  {'OK' if not orphelins else 'ÉCART'}")

    afficher("\nModèle intermédiaire retrouvé à partir de MongoDB :")
    ok &= comparer_ir(ir, deplier(en_base), afficher)

    try:
        base["posts"].insert_one({"_id": -999, "post_type_id": 4, "title": "une page n'a pas de titre",
                                  "body": "", "score": 0, "comment_count": 0, "comments": [],
                                  "creation_date": datetime(2020, 1, 1), "last_activity_date": datetime(2020, 1, 1)})
        base["posts"].delete_one({"_id": -999})
        afficher("\nTest du validateur : ÉCHEC (document invalide accepté puis supprimé)")
        ok = False
    except WriteError:
        afficher("\nTest du validateur : OK (document invalide refusé)")
    return ok


def charger(base, ir):
    """Écrivain : IR -> collections users, posts, tags de `base`, puis validation."""
    # dates {"$date": ...} -> datetime, comme les relira MongoDB
    attendu = {n: json_util.loads(json.dumps(docs, ensure_ascii=False)) for n, docs in construire(ir).items()}
    afficher("Documents construits : " + ", ".join(f"{n} {len(d)}" for n, d in attendu.items()))

    afficher(f"\nBase {base.name} :")
    collections = {n: preparer(base, n) for n in attendu}

    afficher("\nInsertion :")
    tout_rejete = []
    for n, docs in attendu.items():
        inseres, deja, rejetes = inserer(collections[n], docs)
        tout_rejete += rejetes
        afficher(f"  {n:6} insérés={inseres:6}  déjà présents={deja:6}  rejetés={len(rejetes)}")
        for _id, message in rejetes[:5]:
            afficher(f"    - {_id} : {message}")

    return verifier(base, attendu, ir) and not tout_rejete


def lire_mongo(base):
    """Lecteur : collections users, posts, tags de `base` -> IR (opération inverse de construire)."""
    manquantes = [n for n in ("users", "posts", "tags") if n not in base.list_collection_names()]
    if manquantes:
        sys.exit(f"Base MongoDB {base.name} : collection(s) absente(s) {manquantes}")
    return deplier({n: list(base[n].find({})) for n in ("users", "posts", "tags")})


def main():
    dossier_ir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(DOSSIER, "intermediaire", "xml")
    afficher(f"=== Import du {datetime.now():%d/%m/%Y %H:%M:%S} ===")
    manifest, ir = lire_ir(dossier_ir)
    afficher(f"Modèle intermédiaire : {dossier_ir} (source : {manifest['source']}, "
             f"{sum(len(v) for v in ir.values())} lignes)")
    client = connecter()
    ok = charger(client[NOM_BASE], ir)
    afficher("\nIMPORT VALIDÉ." if ok else "\nIMPORT À VÉRIFIER : voir les écarts ci-dessus.")
    client.close()
    with open(FICHIER_JOURNAL, "a", encoding="utf-8") as f:
        f.write("\n".join(journal) + "\n\n")
    print(f"(journal ajouté à {FICHIER_JOURNAL})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
