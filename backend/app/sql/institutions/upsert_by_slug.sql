INSERT INTO institutions (name, slug, pluggy_connector_id, kind)
VALUES (%s, %s, NULL, 'bank')
ON CONFLICT (slug) DO UPDATE SET
    updated_at = NOW()
RETURNING id;
