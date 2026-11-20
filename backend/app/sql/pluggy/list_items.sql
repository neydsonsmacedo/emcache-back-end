SELECT i.pluggy_item_id, i.user_id, i.institution_id, inst.name AS institution_name,
       i.connector_id, i.connector_name, i.status, i.execution_status,
       i.client_user_id, i.last_updated_at, i.next_auto_sync_at,
       i.created_at, i.updated_at
FROM pluggy_items i
LEFT JOIN institutions inst ON inst.id = i.institution_id
WHERE (%(user_id)s::uuid IS NULL OR i.user_id = %(user_id)s::uuid)
ORDER BY i.updated_at DESC;
