"""
SAÉ 5.02 — Extraction XML -> modèle intermédiaire (IR).

Entrée : Users.xml, Posts.xml, Comments.xml, Tags.xml (dossier de ce script par défaut)
Sortie : intermediaire/xml/  users.jsonl, posts.jsonl, comments.jsonl, tags.jsonl, manifest.json

Règles de transformation (une valeur XML est toujours du texte) :
  - attribut absent          -> null ;  attribut vide ""  -> "" (conservé)
  - champ numérique          -> entier JSON
  - date                     -> texte canonique AAAA-MM-JJTHH:MM:SS.mmm
  - Tags "<a><b>" ou "|a|b|" -> liste ordonnée ["a", "b"]
  - attribut inconnu du dictionnaire -> erreur (aucune perte silencieuse)

Validation :
  1. aller-retour : chaque ligne XML est reconstituée à l'identique depuis l'IR ;
  2. validation complète de l'IR (intermediaire.valider_ir).

Usage : python xml_vers_intermediaire.py [dossier_des_xml] [dossier_de_sortie]
"""

import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

from intermediaire import ENTITES, FORMAT_DATE, ecrire_ir, ecrire_schemas, sha256_fichier, valider_ir

DOSSIER = os.path.dirname(os.path.abspath(__file__))
DOSSIER_IR_DEFAUT = os.path.join(DOSSIER, "intermediaire", "xml")


# ---------------------------------------------------------------
# ÉTAPE 1 — Conversion d'une valeur XML (texte) vers l'IR, et retour
# ---------------------------------------------------------------
def vers_ir(type_champ, texte):
    if texte is None:
        return None
    if type_champ in ("entier", "compteur"):
        return int(texte)
    if type_champ == "date":
        for format_date in (FORMAT_DATE, "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(texte, format_date).strftime(FORMAT_DATE)[:-3]
            except ValueError:
                pass
        raise ValueError(f"Date illisible : {texte!r}")
    if type_champ == "tags":
        morceaux = texte.split("|") if texte.startswith("|") else texte.strip("<>").split("><")
        return [t for t in morceaux if t]
    return texte


def vers_xml(type_champ, valeur):
    """Opération inverse, utilisée pour prouver l'absence de perte."""
    if valeur is None:
        return None
    if type_champ == "tags":
        return "".join(f"<{t}>" for t in valeur)
    return str(valeur)


# ---------------------------------------------------------------
# ÉTAPE 2 — Lecture d'un fichier XML
# ---------------------------------------------------------------
def lire_xml(entite, dossier_xml=DOSSIER):
    spec = ENTITES[entite]
    connus = {c[0] for c in spec["champs"]}
    lignes_xml, enregistrements = [], []
    for _, element in ET.iterparse(os.path.join(dossier_xml, spec["xml"]), events=("end",)):
        if element.tag != "row":
            continue
        attributs = dict(element.attrib)
        inconnus = set(attributs) - connus
        if inconnus:
            raise ValueError(f"{spec['xml']} Id={attributs.get('Id')} : attributs non prévus {sorted(inconnus)}")
        lignes_xml.append(attributs)
        enregistrements.append({ir: vers_ir(t, attributs.get(x)) for x, ir, t, _ in spec["champs"]})
        element.clear()
    return lignes_xml, enregistrements


# ---------------------------------------------------------------
# ÉTAPE 3 — Aller-retour : IR -> attributs XML == XML d'origine
# ---------------------------------------------------------------
def verifier_aller_retour(entite, lignes_xml, enregistrements, afficher=print):
    champs = ENTITES[entite]["champs"]
    differentes = []
    for attributs, e in zip(lignes_xml, enregistrements):
        reconstitue = {x: vers_xml(t, e[ir]) for x, ir, t, _ in champs if e[ir] is not None}
        if reconstitue != attributs:
            differentes.append(attributs["Id"])
    afficher(f"  {entite:9} {len(lignes_xml):6} lignes XML, reconstituées à l'identique : "
             f"{len(lignes_xml) - len(differentes):6}  {'OK' if not differentes else 'ÉCART ' + str(differentes[:5])}")
    return not differentes


def lire_xml_tout(dossier_xml=DOSSIER, afficher=print):
    """Lecteur XML -> IR en mémoire. Renvoie (ir, infos_source, aller_retour_ok)."""
    afficher(f"Lecture des XML dans {dossier_xml}")
    donnees, infos, ok = {}, {}, True
    for entite, spec in ENTITES.items():
        lignes_xml, enregistrements = lire_xml(entite, dossier_xml)
        donnees[entite] = enregistrements
        infos[spec["xml"]] = {"rows": len(lignes_xml),
                              "sha256": sha256_fichier(os.path.join(dossier_xml, spec["xml"]))}
        ok &= verifier_aller_retour(entite, lignes_xml, enregistrements, afficher)
    return donnees, infos, ok


def main():
    dossier_xml = sys.argv[1] if len(sys.argv) > 1 else DOSSIER
    dossier_ir = sys.argv[2] if len(sys.argv) > 2 else DOSSIER_IR_DEFAUT
    donnees, infos, ok = lire_xml_tout(dossier_xml)

    manifest = ecrire_ir(dossier_ir, "xml", donnees, infos)
    print(f"\nIR écrite dans {dossier_ir}")
    for entite, m in manifest["entities"].items():
        print(f"  {m['file']:15} {m['count']:6} lignes  sha256 {m['sha256'][:16]}…")
    print("\nSchémas JSON :")
    ecrire_schemas()
    print()
    ok &= valider_ir(dossier_ir)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
