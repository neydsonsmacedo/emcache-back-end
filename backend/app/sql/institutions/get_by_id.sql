SELECT id, name, slug, pluggy_connector_id, kind
FROM institutions
WHERE id = %(id)s::uuid
LIMIT 1;
