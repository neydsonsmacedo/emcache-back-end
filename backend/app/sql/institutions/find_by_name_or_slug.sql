SELECT id, name, slug, pluggy_connector_id, kind
FROM institutions
WHERE LOWER(TRIM(name)) = LOWER(TRIM(%(query)s))
   OR LOWER(slug) = LOWER(TRIM(%(query)s))
LIMIT 1;
