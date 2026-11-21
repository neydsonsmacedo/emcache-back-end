DELETE FROM manual_transactions
WHERE id = %(id)s::uuid
  AND (%(user_id)s::uuid IS NULL OR user_id = %(user_id)s::uuid)
RETURNING id;
