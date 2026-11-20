UPDATE pluggy_items pi
SET user_id = u.id
FROM users u
WHERE pi.user_id IS NULL
  AND pi.client_user_id IS NOT NULL
  AND pi.client_user_id = u.id::TEXT;
