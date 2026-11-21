SELECT
    a.pluggy_account_id,
    a.pluggy_item_id,
    i.institution_id,
    COALESCE(inst.name, i.connector_name) AS institution_name,
    a.type,
    a.subtype,
    a.name,
    a.balance_cents,
    a.currency_code,
    a.credit_limit_cents,
    a.available_credit_limit_cents
FROM pluggy_accounts a
JOIN pluggy_items i ON i.pluggy_item_id = a.pluggy_item_id
LEFT JOIN institutions inst ON inst.id = i.institution_id
WHERE (%(user_id)s::uuid IS NULL OR i.user_id = %(user_id)s::uuid)
ORDER BY institution_name NULLS LAST, a.name;
