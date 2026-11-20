SELECT pluggy_item_id, connector_id, connector_name
FROM pluggy_items
WHERE institution_id IS NULL
  AND (connector_id IS NOT NULL OR connector_name IS NOT NULL);
