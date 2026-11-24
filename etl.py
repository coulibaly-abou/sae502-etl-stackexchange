"""
SAÉ 5.02 — Mission 4 : outil de migration Stack Exchange (XML, PostgreSQL, MongoDB).

Usage :
    python etl.py xml postgresql          XML -> PostgreSQL
    python etl.py xml mongodb             XML -> MongoDB
    python etl.py mongodb postgresql      MongoDB -> PostgreSQL
    python etl.py postgresql mongodb      PostgreSQL -> MongoDB
    python etl.py valider <dossier_IR>    valide un modèle intermédiaire existant

Options :
    --base-pg NOM      base PostgreSQL, source ou cible selon le sens de la migration
    --base-mongo NOM   base MongoDB, source ou cible selon le sens de la migration
    --dossier-xml D    dossier des fichiers XML (défaut : dossier de ce script)
    --ir D             dossier où écrire le modèle intermédiaire (défaut : intermediaire/<source>)
    --reference D      modèle intermédiaire de référence (défaut : intermediaire/xml)

Bases par défaut :
    PostgreSQL : Stackexchange comme source ou comme cible depuis XML ;
                 stackexchange_depuis_mongodb comme cible de MongoDB -> PostgreSQL.
    MongoDB    : stack_exchange_3col comme source ou comme cible depuis XML ;
                 stack_exchange_depuis_postgresql comme cible de PostgreSQL -> MongoDB.
    Une base cible absente est créée ; aucune base, table ou collection n'est vidée ni supprimée,
    et un enregistrement déjà présent n'est jamais remplacé (relance sans doublon).

Déroulement de chaque migration : toutes passent par le modèle intermédiaire (IR).
    1. Extraction  : le lecteur de la source écrit l'IR dans le dossier --ir ;
    2. Validation  : l'IR est validée (manifeste, JSON Schema, identifiants, références) ;
    3. Chargement  : l'écrivain de la cible charge l'IR et effectue ses propres contrôles ;
    4. Relecture   : la cible est relue avec son lecteur et reconvertie en IR ;
    5. Bilan       : les six critères de validation ci-dessous.

Critères de validation :
    V1 Complétude  : même nombre d'enregistrements par entité dans l'IR extraite et dans la cible relue.
    V2 Conformité  : l'IR extraite est valide (manifeste, JSON Schema, identifiants uniques, 0 référence orpheline).
    V3 Cible       : contrôles de l'écrivain réussis (effectifs par table ou collection, comparaison
                     ligne par ligne ou document par document, contraintes ou validateur qui refusent
                     des données invalides, aucun rejet).
    V4 Fidélité    : l'IR relue depuis la cible est identique, champ par champ, à l'IR extraite.
    V5 Empreintes  : les empreintes SHA-256 des quatre entités sont identiques avant et après migration.
    V6 Bout en bout: l'IR relue depuis la cible est identique à l'IR de référence produite depuis les XML.
La migration est validée si les six critères sont satisfaits. Le déroulé est ajouté à journal_etl.txt.

Prérequis : pip install -r requirements.txt ; MongoDB sur localhost:27017 ; PostgreSQL sur
localhost:5434, mot de passe dans la variable d'environnement STACKEXCHANGE_DB_PASSWORD.
"""

import argparse
import hashlib
import os
import sys
from datetime import datetime

from intermediaire import ENTITES, ecrire_ir, ligne_json, lire_ir, valider_ir
from modele_documentaire import comparer_ir

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_JOURNAL = os.path.join(DOSSIER, "journal_etl.txt")
SYSTEMES = ("xml", "postgresql", "mongodb")
MIGRATIONS = {("xml", "postgresql"), ("xml", "mongodb"), ("mongodb", "postgresql"), ("postgresql", "mongodb")}
BASES = {   # (système, rôle) -> base par défaut
    ("postgresql", "source"): "Stackexchange", ("postgresql", "cible_xml"): "Stackexchange",
    ("postgresql", "cible"): "stackexchange_depuis_mongodb",
    ("mongodb", "source"): "stack_exchange_3col", ("mongodb", "cible_xml"): "stack_exchange_3col",
    ("mongodb", "cible"): "stack_exchange_depuis_postgresql",
}


class Journal:
    """Affiche à l'écran et garde une copie de tout ce qui est affiché, pour journal_etl.txt."""
    def __init__(self, flux):
        self.flux, self.lignes = flux, []

    def write(self, texte):
        self.flux.write(texte)
        self.lignes.append(texte)

    def flush(self):
        self.flux.flush()


# ---------------------------------------------------------------
# Empreinte d'une entité, calculée comme le fichier .jsonl de l'IR
# ---------------------------------------------------------------
def empreinte(ir, entite):
    noms = [c[1] for c in ENTITES[entite]["champs"]]
    h = hashlib.sha256()
    for e in sorted(ir[entite], key=lambda e: e["id"]):
        h.update((ligne_json({n: e[n] for n in noms}) + "\n").encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------
# Lecteurs (source -> IR) et écrivains (IR -> cible)
# ---------------------------------------------------------------
def extraire(source, base, args):
    """Renvoie (ir, infos_source, ok_extraction)."""
    if source == "xml":
        from xml_vers_intermediaire import lire_xml_tout
        return lire_xml_tout(args.dossier_xml)
    if source == "postgresql":
        import postgres_etl as pg
        connexion = pg.connecter(base)
        ir = pg.lire_pg(connexion)
        connexion.close()
        return ir, {"base": base}, True
    import import_3_collections as mg
    client = mg.connecter()
    ir = mg.lire_mongo(client[base])
    client.close()
    return ir, {"base": base}, True


def charger_et_relire(cible, base, ir):
    """Renvoie (ok_controles_ecrivain, ir_relue_depuis_la_cible)."""
    if cible == "postgresql":
        import postgres_etl as pg
        connexion = pg.connecter(base, creer=True)
        T = pg.charger(connexion, ir)
        ok = pg.verifier(connexion, ir, T)
        relue = pg.lire_pg(connexion)
        connexion.close()
        return ok, relue
    import import_3_collections as mg
    client = mg.connecter()
    ok = mg.charger(client[base], ir)
    relue = mg.lire_mongo(client[base])
    client.close()
    return ok, relue


# ---------------------------------------------------------------
# Une migration complète
# ---------------------------------------------------------------
def migrer(source, cible, args):
    base_source = None if source == "xml" else (
        args.base_pg if source == "postgresql" else args.base_mongo) or BASES[(source, "source")]
    role = "cible_xml" if source == "xml" else "cible"
    base_cible = (args.base_pg if cible == "postgresql" else args.base_mongo) or BASES[(cible, role)]
    dossier_ir = args.ir or os.path.join(DOSSIER, "intermediaire", source)
    if "postgresql" in (source, cible) and not os.environ.get("STACKEXCHANGE_DB_PASSWORD"):
        sys.exit("La variable STACKEXCHANGE_DB_PASSWORD n'est pas définie : rien n'a été lu ni modifié.\n"
                 "  PowerShell (session en cours) : $env:STACKEXCHANGE_DB_PASSWORD = \"votre_mot_de_passe\"")

    print(f"=== Migration {source} -> {cible} ({datetime.now():%d/%m/%Y %H:%M:%S}) ===")
    print(f"Source : {source}" + (f" (base {base_source})" if base_source else f" ({args.dossier_xml})"))
    print(f"Cible  : {cible} (base {base_cible})")
    print(f"Modèle intermédiaire : {dossier_ir}")

    print("\n--- 1. Extraction : source -> modèle intermédiaire ---")
    ir_brute, infos, ok_extraction = extraire(source, base_source, args)
    ecrire_ir(dossier_ir, source, ir_brute, infos)
    manifest, ir = lire_ir(dossier_ir)                 # on repart du fichier écrit, comme toute cible
    print("Effectifs extraits : " + ", ".join(f"{e} {len(ir[e])}" for e in ENTITES))

    print("\n--- 2. Validation du modèle intermédiaire ---")
    ok_ir = valider_ir(dossier_ir) and ok_extraction

    if not ok_ir:
        print("\nARRÊT : le modèle intermédiaire n'est pas valide, la cible n'a pas été modifiée.")
        return False

    print(f"\n--- 3. Chargement : modèle intermédiaire -> {cible} ---")
    ok_cible, relue = charger_et_relire(cible, base_cible, ir)

    print(f"\n--- 4. Relecture de la cible et comparaison avec le modèle intermédiaire ---")
    ok_fidelite = comparer_ir(ir, relue)

    reference = None
    if os.path.isfile(os.path.join(args.reference, "manifest.json")):
        reference = lire_ir(args.reference)[1]

    criteres = [
        ("V1 Complétude", all(len(ir[e]) == len(relue[e]) for e in ENTITES),
         ", ".join(f"{e} {len(relue[e])}/{len(ir[e])}" for e in ENTITES)),
        ("V2 Conformité de l'IR", ok_ir, "manifeste, JSON Schema, identifiants, références"),
        (f"V3 Contrôles de la cible", ok_cible, f"écrivain {cible}"),
        ("V4 Fidélité (aller-retour)", ok_fidelite, "IR relue = IR extraite, champ par champ"),
        ("V5 Empreintes SHA-256", all(empreinte(ir, e) == empreinte(relue, e) for e in ENTITES),
         ", ".join(f"{e} {empreinte(relue, e)[:12]}…" for e in ENTITES)),
        ("V6 Bout en bout", reference is not None and comparer_ir(reference, relue, afficher=lambda *_: None),
         f"IR relue = IR de référence ({os.path.relpath(args.reference, DOSSIER)})" if reference
         else "pas d'IR de référence"),
    ]
    print(f"\n--- 5. Bilan de la migration {source} -> {cible} ---")
    for nom, reussi, detail in criteres:
        print(f"  [{'OK   ' if reussi else 'ÉCHEC'}] {nom:28} {detail}")
    ok = all(r for _, r, _ in criteres)
    print(f"\nMIGRATION {source.upper()} -> {cible.upper()} VALIDÉE." if ok
          else f"\nMIGRATION {source.upper()} -> {cible.upper()} À VÉRIFIER : voir les critères en échec.")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Migrations Stack Exchange entre XML, PostgreSQL et MongoDB.",
                                     usage="python etl.py SOURCE CIBLE [options]  |  python etl.py valider DOSSIER_IR")
    parser.add_argument("source", help="xml, postgresql, mongodb, ou valider")
    parser.add_argument("cible", help="postgresql ou mongodb (ou le dossier de l'IR pour valider)")
    parser.add_argument("--base-pg")
    parser.add_argument("--base-mongo")
    parser.add_argument("--dossier-xml", default=DOSSIER)
    parser.add_argument("--ir")
    parser.add_argument("--reference", default=os.path.join(DOSSIER, "intermediaire", "xml"))
    args = parser.parse_args()

    journal = Journal(sys.stdout)
    sys.stdout = journal
    try:
        if args.source == "valider":
            ok = valider_ir(args.cible)
        elif (args.source, args.cible) in MIGRATIONS:
            ok = migrer(args.source, args.cible, args)
        else:
            sys.stdout = journal.flux
            parser.error("migrations possibles : xml postgresql, xml mongodb, mongodb postgresql, "
                         "postgresql mongodb (ou : valider DOSSIER_IR)")
    finally:
        sys.stdout = journal.flux
        with open(FICHIER_JOURNAL, "a", encoding="utf-8") as f:
            f.write("".join(journal.lignes) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
