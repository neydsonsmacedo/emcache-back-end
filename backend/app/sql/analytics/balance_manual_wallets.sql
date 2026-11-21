SELECT
    inst.id AS institution_id,
    inst.name AS institution_name,
    inst.kind,
    COALESCE(SUM(CASE WHEN mt.type = 'CREDIT' THEN mt.valor ELSE -mt.valor END), 0)::BIGINT AS balance_cents,
    COUNT(*)::INTEGER AS transaction_count
FROM manual_transactions mt
JOIN institutions inst ON inst.id = mt.institution_id
WHERE inst.kind IN ('wallet', 'benefit')
  AND mt.status = 'completo'
  AND mt.matched_pluggy_transaction_id IS NULL
  AND mt.valor IS NOT NULL
  AND (%(user_id)s::uuid IS NULL OR mt.user_id = %(user_id)s::uuid)
GROUP BY inst.id, inst.name, inst.kind
ORDER BY inst.name;
