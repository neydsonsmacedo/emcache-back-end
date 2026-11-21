SELECT
    institution_id,
    COALESCE(NULLIF(TRIM(institution_name), ''), 'Sem instituição') AS institution_name,
    COALESCE(SUM(CASE WHEN type = 'CREDIT' THEN amount_cents ELSE 0 END), 0)::BIGINT AS total_income_cents,
    COALESCE(SUM(CASE WHEN type = 'DEBIT' THEN amount_cents ELSE 0 END), 0)::BIGINT AS total_expense_cents,
    COUNT(*)::INTEGER AS transaction_count
FROM unified_transactions
WHERE (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
  AND (%(date_from)s::date IS NULL OR transaction_date >= %(date_from)s::date)
  AND (%(date_to)s::date IS NULL OR transaction_date <= %(date_to)s::date)
GROUP BY institution_id, COALESCE(NULLIF(TRIM(institution_name), ''), 'Sem instituição')
ORDER BY total_expense_cents DESC, institution_name ASC;
