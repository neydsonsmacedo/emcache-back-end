UPDATE institutions
SET name = %s,
    updated_at = NOW()
WHERE id = %s
  AND name IS DISTINCT FROM %s;
