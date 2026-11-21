SELECT pt.pluggy_transaction_id,
       pt.pluggy_account_id,
       pt.pluggy_item_id,
       pi.user_id,
       pi.institution_id,
       COALESCE(inst.name, pi.connector_name) AS institution_name,
       pa.name AS account_name,
       pt.description,
       pt.description_raw,
       pt.merchant_name,
       ABS(pt.amount_cents)::BIGINT AS amount_cents,
       pt.date::DATE AS transaction_date,
       pt.date AS occurred_at,
       pt.type,
       pt.category,
       pt.payment_method
FROM pluggy_transactions pt
JOIN pluggy_accounts pa ON pa.pluggy_account_id = pt.pluggy_account_id
JOIN pluggy_items pi ON pi.pluggy_item_id = pt.pluggy_item_id
LEFT JOIN institutions inst ON inst.id = pi.institution_id
WHERE pi.user_id = %(user_id)s::uuid
  AND NOT EXISTS (
      SELECT 1
      FROM manual_transactions mt
      WHERE mt.matched_pluggy_transaction_id = pt.pluggy_transaction_id
  )
ORDER BY pt.date DESC;
