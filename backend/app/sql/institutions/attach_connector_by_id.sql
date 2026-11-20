UPDATE institutions
SET pluggy_connector_id = %s,
    name = %s,
    updated_at = NOW()
WHERE id = %s;
