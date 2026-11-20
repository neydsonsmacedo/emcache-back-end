INSERT INTO institutions (name, slug, pluggy_connector_id, kind)
VALUES (%s, %s, %s, 'bank')
ON CONFLICT (pluggy_connector_id) DO UPDATE SET
    name = EXCLUDED.name,
    updated_at = NOW()
RETURNING id;
