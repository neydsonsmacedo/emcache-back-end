SELECT
    DATE_TRUNC(%(granularity)s, transaction_date::timestamp)::DATE AS bucket_date,
    COALESCE(SUM(CASE WHEN type = 'CREDIT' THEN amount_cents ELSE 0 END), 0)::BIGINT AS income_cents,
    COALESCE(SUM(CASE WHEN type = 'DEBIT' THEN amount_cents ELSE 0 END), 0)::BIGINT AS expense_cents,
    COUNT(*)::INTEGER AS transaction_count
FROM unified_transactions
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(institution_id)s::uuid IS NULL OR institution_id = %(institution_id)s::uuid)
  AND (%(date_from)s::date IS NULL OR transaction_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR transaction_date <= %(date_to)s::date)
GROUP BY bucket_date
ORDER BY bucket_date ASC;
