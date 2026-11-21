SELECT COUNT(*)::INTEGER AS pending_count
FROM manual_transactions
WHERE status = 'pendente'
  AND (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid);
