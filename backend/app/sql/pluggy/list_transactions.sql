SELECT t.pluggy_transaction_id, t.pluggy_account_id, t.pluggy_item_id,
       i.user_id, i.connector_name, a.name AS account_name,
       t.description, t.amount_cents, t.currency_code, t.date,
       t.type, t.status, t.category, t.payment_method, t.merchant_name
FROM pluggy_transactions t
JOIN pluggy_accounts a ON a.pluggy_account_id = t.pluggy_account_id
JOIN pluggy_items i ON i.pluggy_item_id = t.pluggy_item_id
WHERE (%(account_id)s::varchar IS NULL OR t.pluggy_account_id = %(account_id)s::varchar)
  AND (%(item_id)s::varchar IS NULL OR t.pluggy_item_id = %(item_id)s::varchar)
  AND (%(user_id)s::uuid IS NULL OR i.user_id = %(user_id)s::uuid)
ORDER BY t.date DESC
LIMIT %(limit)s OFFSET %(offset)s;
