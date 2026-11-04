"""
SAÉ 5.02 — Modèle documentaire MongoDB en 3 collections : users, posts, tags.

  users  un document par utilisateur (Users.xml)
  posts  un document par post de premier niveau : questions (type 1), avec leurs
         réponses (type 2) et leurs commentaires imbriqués, et pages (types 4, 5, 7)
  tags   un document par tag ; l'extrait et le wiki sont des posts référencés

Ce module définit, à partir du modèle intermédiaire (IR) :
  - les correspondances champ IR -> chemin dans le document ;
  - construire(ir) : IR -> {"users": [...], "posts": [...], "tags": [...]} ;
  - deplier(col)   : opération inverse, documents -> IR (lecteur MongoDB -> IR) ;
  - VALIDATEURS ($jsonSchema) et INDEX de chaque collection.
Règles : _id = Id d'origine ; null de l'IR -> champ absent ; dates -> Date MongoDB.
Le format détaillé est décrit dans modele_documentaire_3_collections/format.json.
"""

from intermediaire import ENTITES
from modele_documentaire import restructurer, aplatir

# ---------------------------------------------------------------- correspondances champ IR -> chemin
USER = [("id", "_id"), ("account_id", "account_id"), ("display_name", "display_name"),
        ("reputation", "reputation"), ("views", "views"), ("up_votes", "up_votes"), ("down_votes", "down_votes"),
        ("creation_date", "creation_date"), ("last_access_date", "last_access_date"),
        ("location", "location"), ("age", "age"), ("about_me", "about_me"),
        ("website_url", "website_url"), ("profile_image_url", "profile_image_url")]
POST = [("id", "_id"), ("post_type_id", "post_type_id"), ("title", "title"), ("body", "body"),
        ("tags", "tags"), ("score", "score"), ("view_count", "view_count"), ("answer_count", "answer_count"),
        ("comment_count", "comment_count"), ("favorite_count", "favorite_count"),
        ("owner_user_id", "owner.user_id"), ("owner_display_name", "owner.display_name"),
        ("last_editor_user_id", "last_editor.user_id"), ("last_editor_display_name", "last_editor.display_name"),
        ("creation_date", "creation_date"), ("last_edit_date", "last_edit_date"),
        ("last_activity_date", "last_activity_date"), ("closed_date", "closed_date"),
        ("accepted_answer_id", "accepted_answer_id")]
ANSWER = [("id", "answer_id") if c == "id" else (c, d) for c, d in POST]
COMMENT = [("id", "comment_id"), ("score", "score"), ("creation_date", "creation_date"), ("text", "text"),
           ("user_id", "user.user_id"), ("user_display_name", "user.display_name")]
TAG = [("id", "_id"), ("tag_name", "tag_name"), ("count", "count"),
       ("excerpt_post_id", "excerpt_post_id"), ("wiki_post_id", "wiki_post_id")]


def construire(ir):
    chrono = lambda e: (e["creation_date"], e["id"])
    com, rep = {}, {}
    for c in sorted(ir["comments"], key=chrono):
        com.setdefault(c["post_id"], []).append(restructurer(c, COMMENT, "post_id"))
    for p in sorted(ir["posts"], key=chrono):
        if p["post_type_id"] == 2:
            a = restructurer(p, ANSWER, "post_type_id", "parent_id")
            a["comments"] = com.get(p["id"], [])
            rep.setdefault(p["parent_id"], []).append(a)
    posts = []
    for p in ir["posts"]:
        if p["post_type_id"] == 2:
            continue
        d = restructurer(p, POST, "parent_id")
        d["comments"] = com.get(p["id"], [])
        if p["post_type_id"] == 1:
            d["answers"] = rep.get(p["id"], [])
        posts.append(d)
    return {"users": [restructurer(u, USER) for u in ir["users"]],
            "posts": posts,
            "tags": [restructurer(t, TAG) for t in ir["tags"]]}


def deplier(col):
    ir = {e: [] for e in ENTITES}
    def ajout(e, champs):
        l = {c[1]: None for c in ENTITES[e]["champs"]}; l.update(champs); ir[e].append(l)
    def coms(liste, pid):
        for c in liste: ajout("comments", {**aplatir(c, COMMENT), "post_id": pid})
    for u in col["users"]: ajout("users", aplatir(u, USER))
    for t in col["tags"]: ajout("tags", aplatir(t, TAG))
    for p in col["posts"]:
        ajout("posts", aplatir(p, POST)); coms(p["comments"], p["_id"])
        for a in p.get("answers", []):
            ajout("posts", {**aplatir(a, ANSWER), "post_type_id": 2, "parent_id": p["_id"]})
            coms(a["comments"], a["answer_id"])
    return ir


# ---------------------------------------------------------------- validateurs $jsonSchema
I = {"bsonType": ["int", "long"]}
S = {"bsonType": "string"}
DT = {"bsonType": "date"}
def obj(req=(), **props):
    o = {"bsonType": "object", "additionalProperties": False, "properties": props}
    if req: o["required"] = list(req)
    return o
PERSONNE = obj(user_id=I, display_name=S)
COMMENTAIRE = obj(["comment_id", "score", "creation_date", "text", "user"],
                  comment_id=I, score=I, creation_date=DT, text=S, user=PERSONNE)
CHAMPS_POST = dict(post_type_id={"enum": [1, 4, 5, 7]}, title=S, body=S,
                   tags={"bsonType": "array", "minItems": 1, "maxItems": 5, "uniqueItems": True, "items": S},
                   score=I, view_count=I, answer_count=I, comment_count=I, favorite_count=I,
                   owner=PERSONNE, last_editor=PERSONNE, creation_date=DT, last_edit_date=DT,
                   last_activity_date=DT, closed_date=DT, accepted_answer_id=I)
REPONSE = obj(["answer_id", "body", "score", "comment_count", "creation_date", "last_activity_date", "comments"],
              answer_id=I, body=S, score=I, comment_count=I, owner=PERSONNE, last_editor=PERSONNE,
              creation_date=DT, last_edit_date=DT, last_activity_date=DT,
              comments={"bsonType": "array", "items": COMMENTAIRE})
VALIDATEURS = {
    "users": {"$jsonSchema": obj(["_id", "display_name", "reputation", "views", "up_votes", "down_votes",
                                  "creation_date", "last_access_date"],
                                 _id=I, account_id=I, display_name=S, reputation=I, views=I, up_votes=I,
                                 down_votes=I, creation_date=DT, last_access_date=DT, location=S, age=I,
                                 about_me=S, website_url=S, profile_image_url=S)},
    "posts": {"$jsonSchema": {**obj(["_id", "post_type_id", "body", "score", "comment_count", "creation_date",
                                     "last_activity_date", "comments"],
                                    _id=I, **CHAMPS_POST, comments={"bsonType": "array", "items": COMMENTAIRE},
                                    answers={"bsonType": "array", "items": REPONSE}),
                              "oneOf": [
                                  {"properties": {"post_type_id": {"enum": [1]}},
                                   "required": ["title", "tags", "view_count", "answer_count", "answers"]},
                                  {"properties": {"post_type_id": {"enum": [4, 5, 7]}},
                                   "not": {"anyOf": [{"required": [c]} for c in
                                                     ("title", "tags", "view_count", "answer_count", "answers",
                                                      "accepted_answer_id", "closed_date")]}}]}},
    "tags": {"$jsonSchema": obj(["_id", "tag_name", "count"], _id=I, tag_name=S, count=I,
                                excerpt_post_id=I, wiki_post_id=I)},
}
INDEX = {
    "users": [],
    "posts": [{"cles": {"tags": 1}, "nom": "questions_par_tag"},
              {"cles": {"owner.user_id": 1}, "nom": "posts_par_auteur"},
              {"cles": {"answers.owner.user_id": 1}, "nom": "reponses_par_auteur"},
              {"cles": {"post_type_id": 1}, "nom": "posts_par_type"}],
    "tags": [{"cles": {"tag_name": 1}, "nom": "tag_name_unique", "unique": True}],
}
