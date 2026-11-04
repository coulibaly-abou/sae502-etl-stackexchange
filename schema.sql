-- =====================================================================
-- SAÉ 5.02 — Modèle relationnel PostgreSQL (traduction du MCD corrigé)
-- Base : Stackexchange
--
-- Les types et contraintes appliquent les règles de transformation de la
-- mission 3 (numéros des problèmes entre crochets) :
--   TEXT au lieu de VARCHAR(50) [1], TIMESTAMP(3) au lieu de DATETIME [2],
--   identifiants d'origine sans SERIAL [16], aucune contrainte que les données
--   ne vérifient pas (signe des scores, bornes de l'âge, unicité du nom) [21, 27, 28].
-- Script rejouable : les tables ne sont créées que si elles n'existent pas.
-- =====================================================================

CREATE TABLE IF NOT EXISTS utilisateur (
    user_id             INTEGER      PRIMARY KEY,          -- -1 = compte système Community [15]
    display_name        TEXT         NOT NULL,             -- non unique : 214 noms partagés [21]
    reputation          INTEGER      NOT NULL CHECK (reputation >= 0),
    user_creation_date  TIMESTAMP(3) NOT NULL,
    last_access_date    TIMESTAMP(3) NOT NULL,
    location            TEXT,
    about_me            TEXT,
    views               INTEGER      NOT NULL CHECK (views >= 0),
    up_votes            INTEGER      NOT NULL CHECK (up_votes >= 0),
    down_votes          INTEGER      NOT NULL CHECK (down_votes >= 0),
    age                 INTEGER,                           -- valeurs atypiques conservées [27]
    profile_image_url   TEXT,
    website_url         TEXT
);

-- Association ASSOCIE : un compte réseau, au plus un utilisateur de ce site
CREATE TABLE IF NOT EXISTS account (
    id_account  INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL UNIQUE REFERENCES utilisateur (user_id)
);

CREATE TABLE IF NOT EXISTS tag (
    tag_id     INTEGER PRIMARY KEY,
    tag_name   TEXT    NOT NULL UNIQUE,                    -- lien depuis les questions par le nom [8]
    tag_count  INTEGER NOT NULL CHECK (tag_count >= 0)
);

CREATE TABLE IF NOT EXISTS post (
    post_id                   INTEGER      PRIMARY KEY,
    body                      TEXT         NOT NULL,       -- '' conservé (69 posts) [12]
    post_score                INTEGER      NOT NULL,       -- peut être négatif [28]
    comment_count             INTEGER      NOT NULL CHECK (comment_count >= 0),
    post_creation_date        TIMESTAMP(3) NOT NULL,
    last_edit_date            TIMESTAMP(3),
    last_activity_date        TIMESTAMP(3) NOT NULL,
    last_editor_display_name  TEXT,
    owner_display_name        TEXT,
    post_type_id              INTEGER      NOT NULL CHECK (post_type_id BETWEEN 1 AND 8),      -- attribut du MCD : 1 question, 2 réponse, 4 extrait, 5 wiki, 7 page d'aide
    user_id                   INTEGER      REFERENCES utilisateur (user_id),                    -- REDIGER, facultatif [13]
    editor_user_id            INTEGER      REFERENCES utilisateur (user_id),                    -- EDITER EN DERNIER
    extrait_de_tag_id         INTEGER      UNIQUE REFERENCES tag (tag_id),                      -- DECRIRE(extrait) [20]
    wiki_de_tag_id            INTEGER      UNIQUE REFERENCES tag (tag_id),                      -- DECRIRE(wiki) [20]
    CONSTRAINT auteur_id_ou_nom CHECK (user_id IS NULL OR owner_display_name IS NULL)        -- X1 [13]
    -- pas de contrainte sur le couple éditeur : posts 2619 et 3580 [14]
);

-- Spécialisation QUESTION (type 1)
CREATE TABLE IF NOT EXISTS question (
    post_id             INTEGER      PRIMARY KEY REFERENCES post (post_id),
    title               TEXT         NOT NULL,
    view_count          INTEGER      NOT NULL CHECK (view_count >= 0),
    answer_count        INTEGER      NOT NULL CHECK (answer_count >= 0),
    favorite_count      INTEGER      CHECK (favorite_count >= 0),
    closed_date         TIMESTAMP(3),
    accepted_answer_id  INTEGER                                                         -- ACCEPTER
);

-- Spécialisation REPONSE (type 2), association REPONDRE_A
CREATE TABLE IF NOT EXISTS reponse (
    post_id      INTEGER PRIMARY KEY REFERENCES post (post_id),
    question_id  INTEGER NOT NULL REFERENCES question (post_id),
    UNIQUE (post_id, question_id)
);

-- ACCEPTER : la réponse acceptée est une réponse de CETTE question (contrainte I1).
-- Référence circulaire question <-> réponse [18] : vérification différée à la fin
-- de la transaction de chargement.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'reponse_acceptee_de_la_question') THEN
        ALTER TABLE question ADD CONSTRAINT reponse_acceptee_de_la_question
            FOREIGN KEY (accepted_answer_id, post_id) REFERENCES reponse (post_id, question_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS commentaire (
    comment_id             INTEGER      PRIMARY KEY,
    comment_text           TEXT         NOT NULL,
    comment_score          INTEGER      NOT NULL,
    comment_creation_date  TIMESTAMP(3) NOT NULL,
    user_display_name      TEXT,
    post_id                INTEGER      NOT NULL REFERENCES post (post_id),          -- CONCERNER
    user_id                INTEGER      REFERENCES utilisateur (user_id),            -- ECRIRE, facultatif
    CONSTRAINT auteur_id_ou_nom_exclusif CHECK ((user_id IS NULL) <> (user_display_name IS NULL))  -- XT1
);

-- ETIQUETER, avec la position qui conserve l'ordre des tags [7]
CREATE TABLE IF NOT EXISTS etiqueter (
    tag_id    INTEGER NOT NULL REFERENCES tag (tag_id),
    post_id   INTEGER NOT NULL REFERENCES question (post_id),
    position  INTEGER NOT NULL CHECK (position BETWEEN 1 AND 5),
    PRIMARY KEY (post_id, tag_id),
    UNIQUE (post_id, position)
);

-- Index sur les clés étrangères les plus utilisées
CREATE INDEX IF NOT EXISTS post_par_auteur        ON post (user_id);
CREATE INDEX IF NOT EXISTS post_par_type          ON post (post_type_id);
CREATE INDEX IF NOT EXISTS reponse_par_question   ON reponse (question_id);
CREATE INDEX IF NOT EXISTS commentaire_par_post   ON commentaire (post_id);
CREATE INDEX IF NOT EXISTS etiqueter_par_tag      ON etiqueter (tag_id);
