INSERT INTO pluggy_items (
    pluggy_item_id, user_id, institution_id, connector_id, connector_name, status, execution_status,
    client_user_id, last_updated_at, next_auto_sync_at, raw_payload, updated_at
) VALUES (
    %(pluggy_item_id)s, %(user_id)s, %(institution_id)s, %(connector_id)s, %(connector_name)s, %(status)s, %(execution_status)s,
    %(client_user_id)s, %(last_updated_at)s, %(next_auto_sync_at)s, %(raw_payload)s, NOW()
)
ON CONFLICT (pluggy_item_id) DO UPDATE SET
    user_id = COALESCE(EXCLUDED.user_id, pluggy_items.user_id),
    institution_id = COALESCE(EXCLUDED.institution_id, pluggy_items.institution_id),
    connector_id = EXCLUDED.connector_id,
    connector_name = EXCLUDED.connector_name,
    status = EXCLUDED.status,
    execution_status = EXCLUDED.execution_status,
    client_user_id = COALESCE(EXCLUDED.client_user_id, pluggy_items.client_user_id),
    last_updated_at = EXCLUDED.last_updated_at,
    next_auto_sync_at = EXCLUDED.next_auto_sync_at,
    raw_payload = EXCLUDED.raw_payload,
    updated_at = NOW();
