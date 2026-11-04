-- =====================================================================
-- SAÉ 5.02 — Contrôles après importation dans PostgreSQL (base Stackexchange)
-- Lecture seule : aucune requête ne modifie les données.
-- Valeurs attendues = comptages observés dans les fichiers XML sources.
-- Exécution : psql -h localhost -p 5434 -U postgres -d Stackexchange -f verification.sql
-- =====================================================================

-- 1. Effectifs par table, comparés aux fichiers XML
SELECT 'utilisateur' AS table_, count(*) AS obtenu, 11961 AS attendu, 'Users.xml : 11 961 lignes' AS source FROM utilisateur
UNION ALL SELECT 'account',     count(*), 11960, 'Users.xml : AccountId renseigné (1 absent)' FROM account
UNION ALL SELECT 'tag',         count(*),   214, 'Tags.xml : 214 lignes' FROM tag
UNION ALL SELECT 'post',        count(*),  3900, 'Posts.xml : 3 900 lignes' FROM post
UNION ALL SELECT 'question',    count(*),  1444, 'Posts.xml : PostTypeId = 1' FROM question
UNION ALL SELECT 'reponse',     count(*),  2183, 'Posts.xml : PostTypeId = 2' FROM reponse
UNION ALL SELECT 'commentaire', count(*),  4440, 'Comments.xml : 4 440 lignes' FROM commentaire
UNION ALL SELECT 'etiqueter',   count(*),  3471, 'Posts.xml : tags découpés (1 à 5 par question)' FROM etiqueter;

-- 2. Répartition des posts par type (attendu : 1 -> 1444, 2 -> 2183, 4 -> 135, 5 -> 135, 7 -> 3)
SELECT post_type_id, count(*) AS nombre
FROM post
GROUP BY post_type_id ORDER BY post_type_id;

-- 3. Cohérence type / spécialisation (attendu : 0 ligne)
SELECT 'question dont le post n''est pas de type 1' AS anomalie, q.post_id
FROM question q JOIN post p USING (post_id) WHERE p.post_type_id <> 1
UNION ALL
SELECT 'réponse dont le post n''est pas de type 2', r.post_id
FROM reponse r JOIN post p USING (post_id) WHERE p.post_type_id <> 2
UNION ALL
SELECT 'post de type 1 sans ligne question', p.post_id
FROM post p LEFT JOIN question q USING (post_id) WHERE p.post_type_id = 1 AND q.post_id IS NULL
UNION ALL
SELECT 'post de type 2 sans ligne reponse', p.post_id
FROM post p LEFT JOIN reponse r USING (post_id) WHERE p.post_type_id = 2 AND r.post_id IS NULL;

-- 4. Références orphelines (garanties par les clés étrangères ; attendu : 0 partout)
SELECT 'post -> auteur inexistant' AS controle, count(*) AS nombre
FROM post p LEFT JOIN utilisateur u ON u.user_id = p.user_id WHERE p.user_id IS NOT NULL AND u.user_id IS NULL
UNION ALL SELECT 'post -> éditeur inexistant', count(*)
FROM post p LEFT JOIN utilisateur u ON u.user_id = p.editor_user_id WHERE p.editor_user_id IS NOT NULL AND u.user_id IS NULL
UNION ALL SELECT 'commentaire -> post inexistant', count(*)
FROM commentaire c LEFT JOIN post p USING (post_id) WHERE p.post_id IS NULL
UNION ALL SELECT 'commentaire -> auteur inexistant', count(*)
FROM commentaire c LEFT JOIN utilisateur u ON u.user_id = c.user_id WHERE c.user_id IS NOT NULL AND u.user_id IS NULL
UNION ALL SELECT 'réponse -> question inexistante', count(*)
FROM reponse r LEFT JOIN question q ON q.post_id = r.question_id WHERE q.post_id IS NULL
UNION ALL SELECT 'réponse acceptée n''appartenant pas à sa question', count(*)
FROM question q LEFT JOIN reponse r ON r.post_id = q.accepted_answer_id
WHERE q.accepted_answer_id IS NOT NULL AND (r.post_id IS NULL OR r.question_id <> q.post_id)
UNION ALL SELECT 'etiqueter -> tag inexistant', count(*)
FROM etiqueter e LEFT JOIN tag t USING (tag_id) WHERE t.tag_id IS NULL;

-- 5. Questions sans tag (attendu : 0, chaque question a 1 à 5 tags)
SELECT q.post_id FROM question q LEFT JOIN etiqueter e USING (post_id) WHERE e.post_id IS NULL;

-- 6. Positions des tags : de 1 à n sans trou (attendu : 0 ligne)
SELECT post_id, count(*) AS nb_tags, max(position) AS position_max
FROM etiqueter GROUP BY post_id HAVING max(position) <> count(*);

-- 7. Valeurs absentes (NULL) et chaînes vides, comparées aux XML
SELECT 'post.user_id NULL (auteur non identifié)' AS controle, count(*) AS obtenu, 11 AS attendu FROM post WHERE user_id IS NULL
UNION ALL SELECT 'post : auteur connu seulement par son nom', count(*), 9 FROM post WHERE user_id IS NULL AND owner_display_name IS NOT NULL
UNION ALL SELECT 'commentaire.user_id NULL', count(*), 10 FROM commentaire WHERE user_id IS NULL
UNION ALL SELECT 'utilisateur.age NULL', count(*), 8461 FROM utilisateur WHERE age IS NULL
UNION ALL SELECT 'utilisateur.location NULL', count(*), 5961 FROM utilisateur WHERE location IS NULL
UNION ALL SELECT 'utilisateur.about_me = '''' (vide, distinct de NULL)', count(*), 91 FROM utilisateur WHERE about_me = ''
UNION ALL SELECT 'utilisateur.website_url = ''''', count(*), 159 FROM utilisateur WHERE website_url = ''
UNION ALL SELECT 'post.body = ''''', count(*), 69 FROM post WHERE body = ''
UNION ALL SELECT 'question avec réponse acceptée', count(*), 563 FROM question WHERE accepted_answer_id IS NOT NULL
UNION ALL SELECT 'question fermée', count(*), 84 FROM question WHERE closed_date IS NOT NULL;

-- 8. Compteurs conservés comparés aux valeurs recalculées (attendu : 0 écart)
SELECT 'answer_count' AS compteur, count(*) AS ecarts
FROM question q LEFT JOIN (SELECT question_id, count(*) n FROM reponse GROUP BY question_id) r ON r.question_id = q.post_id
WHERE q.answer_count <> coalesce(r.n, 0)
UNION ALL SELECT 'comment_count', count(*)
FROM post p LEFT JOIN (SELECT post_id, count(*) n FROM commentaire GROUP BY post_id) c USING (post_id)
WHERE p.comment_count <> coalesce(c.n, 0)
UNION ALL SELECT 'tag_count', count(*)
FROM tag t LEFT JOIN (SELECT tag_id, count(*) n FROM etiqueter GROUP BY tag_id) e USING (tag_id)
WHERE t.tag_count <> coalesce(e.n, 0);

-- 9. Identifiants particuliers conservés (attendu : 1 ligne, utilisateur -1 « Community », 63 posts)
SELECT u.user_id, u.display_name, count(p.post_id) AS nb_posts
FROM utilisateur u LEFT JOIN post p ON p.user_id = u.user_id
WHERE u.user_id = -1 GROUP BY u.user_id, u.display_name;

-- 10. Type des extraits et des wikis (attendu : 0 partout)
SELECT 'extrait dont le post n''est pas de type 4' AS controle, count(*) AS nombre
FROM post WHERE extrait_de_tag_id IS NOT NULL AND post_type_id <> 4
UNION ALL
SELECT 'wiki dont le post n''est pas de type 5', count(*)
FROM post WHERE wiki_de_tag_id IS NOT NULL AND post_type_id <> 5;
