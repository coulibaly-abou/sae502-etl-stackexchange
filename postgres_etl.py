"""
SAÉ 5.02 — Migrations entre le modèle intermédiaire (IR) et PostgreSQL.

  python postgres_etl.py inspecter                  examine la base existante (lecture seule)
  python postgres_etl.py charger  [dossier_IR]     IR -> PostgreSQL (+ validation)
  python postgres_etl.py exporter dossier_sortie   PostgreSQL -> IR (lecteur)

Connexion à la base EXISTANTE (aucune base n'est créée) :
  hôte localhost, port 5434, base Stackexchange, utilisateur postgres ;
  mot de passe lu dans la variable d'environnement STACKEXCHANGE_DB_PASSWORD
  (jamais écrit dans le code). Sous PowerShell :
      $env:STACKEXCHANGE_DB_PASSWORD = "..."          (session en cours)
      setx STACKEXCHANGE_DB_PASSWORD "..."             (permanent, nouveau terminal)
Sécurité : si une table du schéma existe déjà avec une autre structure, le
chargement s'arrête sans rien modifier ; aucune table n'est supprimée ou vidée.
Prérequis : pip install -r requirements.txt

Règles appliquées (mission 3) : types et contraintes de schema.sql, identifiants
d'origine conservés, null -> NULL et '' conservé, tags découpés avec leur
position, réponse acceptée dérivée de AcceptedAnswerId, référence circulaire
question <-> réponse vérifiée en fin de transaction, chargement en une seule
transaction (tout ou rien), requêtes paramétrées, réexécution sans doublon
(INSERT ... ON CONFLICT DO NOTHING).
"""

import os
import sys
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

from intermediaire import ENTITES, FORMAT_DATE, date_ir, ecrire_ir, lire_ir, valider_ir
from modele_documentaire import comparer_ir

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_SCHEMA = os.path.join(DOSSIER, "schema.sql")
FICHIER_JOURNAL = os.path.join(DOSSIER, "journal_postgres.txt")
journal = []


def afficher(message=""):
    print(message)
    journal.append(message)


# ---------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------
CONNEXION = {"host": "localhost", "port": "5434", "database": "Stackexchange", "user": "postgres"}
VARIABLE_MOT_DE_PASSE = "STACKEXCHANGE_DB_PASSWORD"


def connecter(base=None, creer=False):
    """Connexion à la base `base` (par défaut : Stackexchange). Avec creer=True, la base
    est créée si elle n'existe pas encore ; une base existante n'est jamais modifiée ici."""
    mot_de_passe = os.environ.get(VARIABLE_MOT_DE_PASSE)
    if not mot_de_passe:
        sys.exit(f"La variable {VARIABLE_MOT_DE_PASSE} n'est pas définie.\n"
                 f"  PowerShell (session en cours) : $env:{VARIABLE_MOT_DE_PASSE} = \"votre_mot_de_passe\"\n"
                 f"  Permanent (nouveau terminal)  : setx {VARIABLE_MOT_DE_PASSE} \"votre_mot_de_passe\"")
    params = {**CONNEXION, "database": base or CONNEXION["database"]}
    try:
        if creer:
            creer_base_si_absente(params, mot_de_passe)
        connexion = psycopg2.connect(password=mot_de_passe, client_encoding="UTF8", **params)
    except psycopg2.OperationalError as erreur:
        # le message de psycopg2 ne contient pas le mot de passe
        sys.exit(f"Connexion impossible à {params['database']} "
                 f"({params['host']}:{params['port']}, {params['user']}) : {str(erreur).strip()}")
    afficher(f"Connecté à PostgreSQL {connexion.server_version // 10000} : base {params['database']} "
             f"({params['host']}:{params['port']}, utilisateur {params['user']})")
    return connexion


def creer_base_si_absente(params, mot_de_passe):
    from psycopg2 import sql
    admin = psycopg2.connect(password=mot_de_passe, **{**params, "database": "postgres"})
    admin.autocommit = True                       # CREATE DATABASE hors transaction
    with admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (params["database"],))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                        .format(sql.Identifier(params["database"])))
            afficher(f"Base {params['database']} créée (elle n'existait pas).")
    admin.close()


# ---------------------------------------------------------------
# Examen de la base existante (lecture seule)
# ---------------------------------------------------------------
COLONNES_ATTENDUES = {   # structure définie dans schema.sql
    "utilisateur": ["user_id", "display_name", "reputation", "user_creation_date", "last_access_date", "location",
                    "about_me", "views", "up_votes", "down_votes", "age", "profile_image_url", "website_url"],
    "account": ["id_account", "user_id"],
    "tag": ["tag_id", "tag_name", "tag_count"],
    "post": ["post_id", "body", "post_score", "comment_count", "post_creation_date", "last_edit_date",
             "last_activity_date", "last_editor_display_name", "owner_display_name", "post_type_id", "user_id",
             "editor_user_id", "extrait_de_tag_id", "wiki_de_tag_id"],
    "question": ["post_id", "title", "view_count", "answer_count", "favorite_count", "closed_date",
                 "accepted_answer_id"],
    "reponse": ["post_id", "question_id"],
    "commentaire": ["comment_id", "comment_text", "comment_score", "comment_creation_date", "user_display_name",
                    "post_id", "user_id"],
    "etiqueter": ["tag_id", "post_id", "position"],
}


def tables_existantes(cur):
    """{table: [colonnes]} pour toutes les tables du schéma public."""
    cur.execute("SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' ORDER BY table_name, ordinal_position")
    tables = {}
    for table, colonne in cur.fetchall():
        tables.setdefault(table, []).append(colonne)
    return tables


def inspecter(connexion):
    with connexion.cursor() as cur:
        tables = tables_existantes(cur)
        afficher(f"\nTables existantes dans le schéma public : {len(tables)}")
        for table, colonnes in tables.items():
            cur.execute(f'SELECT count(*) FROM public."{table}"')
            n = cur.fetchone()[0]
            cur.execute("SELECT conname, contype FROM pg_constraint WHERE conrelid = %s::regclass",
                        (f'public."{table}"',))
            contraintes = ", ".join(f"{nom} ({ {'p': 'PK', 'f': 'FK', 'u': 'UNIQUE', 'c': 'CHECK'}.get(t, t)})"
                                    for nom, t in cur.fetchall())
            afficher(f"  {table} : {n} lignes ; colonnes : {', '.join(colonnes)}")
            afficher(f"      contraintes : {contraintes or 'aucune'}")
        conflits = verifier_structure(tables)
        prevues = [t for t in COLONNES_ATTENDUES if t not in tables]
        afficher(f"\nTables du schéma à créer : {', '.join(prevues) or 'aucune'}")
        afficher("Structures incompatibles : " + ("aucune" if not conflits else "; ".join(conflits)))
    return not conflits


def verifier_structure(tables):
    """Tables du schéma déjà présentes avec des colonnes différentes."""
    return [f"{t} (existant : {', '.join(tables[t])})" for t, attendues in COLONNES_ATTENDUES.items()
            if t in tables and tables[t] != attendues]


# ---------------------------------------------------------------
# IR -> lignes des tables
# ---------------------------------------------------------------
def date_pg(texte):
    return datetime.strptime(texte, FORMAT_DATE) if texte is not None else None


def lignes_tables(ir):
    """Répartit l'IR dans les tables du modèle relationnel (schema.sql)."""
    tags_par_nom = {t["tag_name"]: t["id"] for t in ir["tags"]}
    extrait_de = {t["excerpt_post_id"]: t["id"] for t in ir["tags"] if t["excerpt_post_id"] is not None}
    wiki_de = {t["wiki_post_id"]: t["id"] for t in ir["tags"] if t["wiki_post_id"] is not None}
    T = {n: [] for n in ("utilisateur", "account", "tag", "post", "question", "reponse", "commentaire", "etiqueter")}
    for u in ir["users"]:
        T["utilisateur"].append((u["id"], u["display_name"], u["reputation"], date_pg(u["creation_date"]),
                                 date_pg(u["last_access_date"]), u["location"], u["about_me"], u["views"],
                                 u["up_votes"], u["down_votes"], u["age"], u["profile_image_url"], u["website_url"]))
        if u["account_id"] is not None:
            T["account"].append((u["account_id"], u["id"]))
    for t in ir["tags"]:
        T["tag"].append((t["id"], t["tag_name"], t["count"]))
    for p in ir["posts"]:
        T["post"].append((p["id"], p["body"], p["score"], p["comment_count"], date_pg(p["creation_date"]),
                          date_pg(p["last_edit_date"]), date_pg(p["last_activity_date"]),
                          p["last_editor_display_name"], p["owner_display_name"], p["post_type_id"],
                          p["owner_user_id"], p["last_editor_user_id"], extrait_de.get(p["id"]), wiki_de.get(p["id"])))
        if p["post_type_id"] == 1:
            T["question"].append((p["id"], p["title"], p["view_count"], p["answer_count"], p["favorite_count"],
                                  date_pg(p["closed_date"]), p["accepted_answer_id"]))
            for position, nom in enumerate(p["tags"], start=1):
                T["etiqueter"].append((tags_par_nom[nom], p["id"], position))
        elif p["post_type_id"] == 2:
            T["reponse"].append((p["id"], p["parent_id"]))
    for c in ir["comments"]:
        T["commentaire"].append((c["id"], c["text"], c["score"], date_pg(c["creation_date"]),
                                 c["user_display_name"], c["post_id"], c["user_id"]))
    return T


INSERTIONS = {   # ordre de chargement imposé par les clés étrangères [17]
    "utilisateur": "INSERT INTO utilisateur (user_id, display_name, reputation, user_creation_date, last_access_date, "
                   "location, about_me, views, up_votes, down_votes, age, profile_image_url, website_url) VALUES %s "
                   "ON CONFLICT (user_id) DO NOTHING",
    "account": "INSERT INTO account (id_account, user_id) VALUES %s ON CONFLICT (id_account) DO NOTHING",
    "tag": "INSERT INTO tag (tag_id, tag_name, tag_count) VALUES %s ON CONFLICT (tag_id) DO NOTHING",
    "post": "INSERT INTO post (post_id, body, post_score, comment_count, post_creation_date, last_edit_date, "
            "last_activity_date, last_editor_display_name, owner_display_name, post_type_id, user_id, editor_user_id, "
            "extrait_de_tag_id, wiki_de_tag_id) VALUES %s ON CONFLICT (post_id) DO NOTHING",
    "question": "INSERT INTO question (post_id, title, view_count, answer_count, favorite_count, closed_date, "
                "accepted_answer_id) VALUES %s ON CONFLICT (post_id) DO NOTHING",
    "reponse": "INSERT INTO reponse (post_id, question_id) VALUES %s ON CONFLICT (post_id) DO NOTHING",
    "commentaire": "INSERT INTO commentaire (comment_id, comment_text, comment_score, comment_creation_date, "
                   "user_display_name, post_id, user_id) VALUES %s ON CONFLICT (comment_id) DO NOTHING",
    "etiqueter": "INSERT INTO etiqueter (tag_id, post_id, position) VALUES %s ON CONFLICT (post_id, tag_id) DO NOTHING",
}


def charger(connexion, ir):
    """Crée les tables puis charge l'IR en UNE transaction : tout ou rien."""
    T = lignes_tables(ir)
    with connexion.cursor() as cur:
        conflits = verifier_structure(tables_existantes(cur))
    if conflits:
        afficher("ARRÊT : des tables existent déjà avec une autre structure ; rien n'a été modifié.")
        for c in conflits:
            afficher(f"  - {c}")
        sys.exit(1)
    with connexion:                                   # commit à la fin, rollback en cas d'erreur
        with connexion.cursor() as cur:
            cur.execute(open(FICHIER_SCHEMA, encoding="utf-8").read())
            afficher("Tables créées ou déjà présentes (schema.sql).")
            afficher("\nInsertion (une seule transaction) :")
            for table, requete in INSERTIONS.items():
                avant = compter(cur, table)
                execute_values(cur, requete, T[table], page_size=1000)
                apres = compter(cur, table)
                afficher(f"  {table:12} lignes à insérer={len(T[table]):6}  insérées={apres - avant:6}  "
                         f"déjà présentes={len(T[table]) - (apres - avant):6}")
    return T


def compter(cur, table):
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0]


# ---------------------------------------------------------------
# PostgreSQL -> IR (lecteur)
# ---------------------------------------------------------------
def lire_pg(connexion):
    d = lambda v: date_ir(v) if v is not None else None
    ir = {e: [] for e in ENTITES}
    with connexion.cursor() as cur:
        cur.execute("SELECT u.user_id, a.id_account, u.display_name, u.reputation, u.user_creation_date, "
                    "u.last_access_date, u.location, u.about_me, u.views, u.up_votes, u.down_votes, u.age, "
                    "u.profile_image_url, u.website_url FROM utilisateur u LEFT JOIN account a USING (user_id) "
                    "ORDER BY u.user_id")
        for r in cur.fetchall():
            ir["users"].append(dict(zip(["id", "account_id", "display_name", "reputation", "creation_date",
                                         "last_access_date", "location", "about_me", "views", "up_votes",
                                         "down_votes", "age", "profile_image_url", "website_url"],
                                        [r[0], r[1], r[2], r[3], d(r[4]), d(r[5]), *r[6:]])))
        cur.execute("SELECT post_id, array_agg(tag_name ORDER BY position) FROM etiqueter "
                    "JOIN tag USING (tag_id) GROUP BY post_id")
        tags = dict(cur.fetchall())
        cur.execute("SELECT p.post_id, p.post_type_id, r.question_id, q.accepted_answer_id, p.user_id, p.owner_display_name, "
                    "p.editor_user_id, p.last_editor_display_name, q.title, p.body, p.post_score, q.view_count, "
                    "q.answer_count, p.comment_count, q.favorite_count, p.post_creation_date, p.last_edit_date, "
                    "p.last_activity_date, q.closed_date FROM post p LEFT JOIN question q USING (post_id) "
                    "LEFT JOIN reponse r USING (post_id) ORDER BY p.post_id")
        for r in cur.fetchall():
            ir["posts"].append({"id": r[0], "post_type_id": r[1], "parent_id": r[2], "accepted_answer_id": r[3],
                                "owner_user_id": r[4], "owner_display_name": r[5], "last_editor_user_id": r[6],
                                "last_editor_display_name": r[7], "title": r[8], "body": r[9],
                                "tags": tags.get(r[0]), "score": r[10], "view_count": r[11],
                                "answer_count": r[12], "comment_count": r[13], "favorite_count": r[14],
                                "creation_date": d(r[15]), "last_edit_date": d(r[16]),
                                "last_activity_date": d(r[17]), "closed_date": d(r[18])})
        cur.execute("SELECT comment_id, post_id, user_id, user_display_name, comment_score, comment_text, "
                    "comment_creation_date FROM commentaire ORDER BY comment_id")
        for r in cur.fetchall():
            ir["comments"].append({"id": r[0], "post_id": r[1], "user_id": r[2], "user_display_name": r[3],
                                   "score": r[4], "text": r[5], "creation_date": d(r[6])})
        cur.execute("SELECT t.tag_id, t.tag_name, t.tag_count, e.post_id, w.post_id FROM tag t "
                    "LEFT JOIN post e ON e.extrait_de_tag_id = t.tag_id "
                    "LEFT JOIN post w ON w.wiki_de_tag_id = t.tag_id ORDER BY t.tag_id")
        for r in cur.fetchall():
            ir["tags"].append({"id": r[0], "tag_name": r[1], "count": r[2],
                               "excerpt_post_id": r[3], "wiki_post_id": r[4]})
    return ir


# ---------------------------------------------------------------
# Validation
# ---------------------------------------------------------------
def verifier(connexion, ir, T):
    ok = True
    afficher("\nEffectifs :")
    with connexion.cursor() as cur:
        for table, lignes in T.items():
            n = compter(cur, table)
            statut = "OK" if n == len(lignes) else "ÉCART"
            ok &= statut == "OK"
            afficher(f"  {table:12} attendues={len(lignes):6}  PostgreSQL={n:6}  {statut}")

    afficher("\nModèle intermédiaire retrouvé à partir de PostgreSQL :")
    ok &= comparer_ir(ir, lire_pg(connexion), afficher)

    afficher("\nContraintes (chaque tentative doit être refusée, puis annulée) :")
    essais = [
        ("commentaire avec identifiant ET nom d'auteur",
         "INSERT INTO commentaire VALUES (-1, 'x', 0, now(), 'nom', 1, 1)"),
        ("réponse acceptée qui n'appartient pas à la question",
         "UPDATE question SET accepted_answer_id = (SELECT post_id FROM reponse WHERE question_id <> 1 LIMIT 1) "
         "WHERE post_id = 1; SET CONSTRAINTS ALL IMMEDIATE"),
        ("tag en position 6", "INSERT INTO etiqueter VALUES (1, 1, 6)"),
        ("deuxième extrait pour le même tag",
         "UPDATE post SET extrait_de_tag_id = (SELECT extrait_de_tag_id FROM post WHERE extrait_de_tag_id IS NOT NULL LIMIT 1) "
         "WHERE post_id = 1"),
    ]
    for nom, sql in essais:
        try:
            with connexion:
                with connexion.cursor() as cur:
                    cur.execute(sql)
                    raise RuntimeError("accepté")
        except psycopg2.Error:
            afficher(f"  OK     {nom} : refusé")
        except RuntimeError:
            afficher(f"  ÉCHEC  {nom} : accepté (annulé)")
            ok = False
    return ok


def main():
    commande = sys.argv[1] if len(sys.argv) > 1 else ""
    if commande not in ("inspecter", "charger", "exporter"):
        sys.exit(__doc__)
    afficher(f"=== PostgreSQL — {commande} — {datetime.now():%d/%m/%Y %H:%M:%S} ===")
    connexion = connecter()
    if commande == "inspecter":
        ok = inspecter(connexion)
    elif commande == "charger":
        dossier_ir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DOSSIER, "intermediaire", "xml")
        manifest, ir = lire_ir(dossier_ir)
        afficher(f"Modèle intermédiaire lu : {dossier_ir} (source {manifest['source']})")
        T = charger(connexion, ir)
        ok = verifier(connexion, ir, T)
        afficher("\nMIGRATION VERS POSTGRESQL VALIDÉE." if ok else "\nMIGRATION À VÉRIFIER : voir les écarts.")
    else:
        sortie = sys.argv[2] if len(sys.argv) > 2 else os.path.join(DOSSIER, "intermediaire", "postgresql")
        ir = lire_pg(connexion)
        ecrire_ir(sortie, "postgresql", ir)
        afficher(f"Modèle intermédiaire écrit dans {sortie}")
        ok = valider_ir(sortie, afficher)
    connexion.close()
    with open(FICHIER_JOURNAL, "a", encoding="utf-8") as f:
        f.write("\n".join(journal) + "\n\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
