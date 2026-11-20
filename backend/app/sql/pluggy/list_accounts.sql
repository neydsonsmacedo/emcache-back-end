SELECT a.pluggy_account_id, a.pluggy_item_id, i.user_id, i.connector_name,
       a.type, a.subtype, a.name, a.number,
       a.balance_cents, a.currency_code,
       a.credit_limit_cents, a.available_credit_limit_cents,
       a.updated_at
FROM pluggy_accounts a
JOIN pluggy_items i ON i.pluggy_item_id = a.pluggy_item_id
WHERE (%(item_id)s::varchar IS NULL OR a.pluggy_item_id = %(item_id)s::varchar)
  AND (%(user_id)s::uuid IS NULL OR i.user_id = %(user_id)s::uuid)
ORDER BY i.connector_name, a.name;
