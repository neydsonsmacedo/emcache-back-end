SELECT
    COALESCE(NULLIF(TRIM(category), ''), 'Sem categoria') AS category,
    type,
    COALESCE(SUM(amount_cents), 0)::BIGINT AS total_cents,
    COUNT(*)::INTEGER AS transaction_count
FROM unified_transactions
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(type)s::varchar IS NULL OR type = %(type)s::varchar)
  AND (%(institution_id)s::uuid IS NULL OR institution_id = %(institution_id)s::uuid)
  AND (%(date_from)s::date IS NULL OR transaction_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR transaction_date <= %(date_to)s::date)
GROUP BY COALESCE(NULLIF(TRIM(category), ''), 'Sem categoria'), type
ORDER BY total_cents DESC, category ASC;
