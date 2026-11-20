SELECT id
FROM users
WHERE id::TEXT = %s
LIMIT 1;
