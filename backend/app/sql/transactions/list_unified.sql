SELECT id, user_id, source, occurred_at, transaction_date, amount_cents, currency_code,
       type, status, description, category, payment_method, merchant_name,
       institution_id, institution_name, account_id, account_name,
       pluggy_transaction_id, created_at
FROM unified_transactions
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(institution_id)s::uuid IS NULL OR institution_id = %(institution_id)s::uuid)
  AND (%(source)s::varchar IS NULL OR source = %(source)s::varchar)
ORDER BY occurred_at DESC
LIMIT %(limit)s OFFSET %(offset)s;
